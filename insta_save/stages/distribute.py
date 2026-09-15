"""Distribute stage — Routed -> Distributed | Misrouted. Folds a routed item's content
into its target Knowledge-DB page behind a diff review gate.

prepare (deterministic bucketing, no model) -> fill (claude-p consolidation, versioned
prompt) -> apply (section-level replace, then relations and statuses). Attended only:
prepare+fill write nothing to Notion, apply is a separate invocation."""

import difflib
import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from insta_save.adapters.notion import (
    get_knowledge_page,
    get_page_blocks,
    query_routed_for_target,
    replace_section_blocks,
    set_heading_text,
    set_knowledge_relation,
    write_distributed,
    write_misrouted,
)
from insta_save.backends import claude_p
from insta_save.backends.base import TerminalBackendError, is_terminal_error
from insta_save.backends.sanitize import scrub_fabricated
from insta_save.markdown_blocks import (
    blocks_to_markdown,
    markdown_to_blocks,
    merge_sections,
    split_sections,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Bucket:
    """One pre-grouped slice of a page's items. Buckets are prompt STRUCTURE: a page that
    fits the budget is one call carrying all of them, so the model dedups across them."""

    name: str
    items: list


def _routing_combo(item, routing_tags) -> tuple:
    return tuple(sorted(t for t in item.get("tags", []) if t in routing_tags))


def _content_type(item, content_types):
    return next((t for t in item.get("tags", []) if t in content_types), None)


def _within(items, *, max_items, char_budget) -> bool:
    if max_items is not None and len(items) > max_items:
        return False
    return sum(len(i.get("summary") or "") for i in items) <= char_budget


def _sort_key(item) -> tuple:
    return (item.get("source_id") or "", item.get("page_id") or "")


def _slice_to_budget(items, *, max_items, char_budget) -> list:
    """Greedily slice items (already in final order) into sequential parts each
    satisfying _within. Always makes progress: a single item whose summary alone
    exceeds char_budget still becomes its own part."""
    parts, current = [], []
    for item in items:
        candidate = current + [item]
        if current and not _within(candidate, max_items=max_items, char_budget=char_budget):
            parts.append(current)
            current = [item]
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def bucket_items(items, routing_tags, content_types, *, max_items, char_budget) -> list:
    """Partition a page's routed items into prompt buckets. Deterministic, no model:
    bucket order and item order within each bucket are both stable regardless of
    input order.

    Items sharing a routing-tag combo group together; a combo over either bound splits
    by content_type, and any content-type sub-bucket still over bound is sliced into
    sequential parts."""
    partitions = defaultdict(list)
    for item in items:
        partitions[_routing_combo(item, routing_tags)].append(item)

    buckets = []
    for combo in sorted(partitions):
        members = sorted(partitions[combo], key=_sort_key)
        name = " + ".join(combo) if combo else "untagged"
        if _within(members, max_items=max_items, char_budget=char_budget):
            buckets.append(Bucket(name, members))
            continue
        by_type = defaultdict(list)
        for item in members:
            by_type[_content_type(item, content_types) or "untyped"].append(item)
        for ctype in sorted(by_type):
            sub_members = by_type[ctype]
            sub_name = f"{name} · {ctype}"
            if _within(sub_members, max_items=max_items, char_budget=char_budget):
                buckets.append(Bucket(sub_name, sub_members))
                continue
            parts = _slice_to_budget(sub_members, max_items=max_items, char_budget=char_budget)
            for index, part in enumerate(parts, start=1):
                buckets.append(Bucket(f"{sub_name} · part {index}", part))
    return buckets


def chunk_buckets(buckets, *, max_items, char_budget) -> list:
    """Pack buckets into fill calls under the budget. One chunk = one call. A page that
    fits is a single call; an over-budget page chains, each call seeing the merged body
    the previous one produced."""
    chunks, current = [], []
    for bucket in buckets:
        candidate = current + [bucket]
        flat = [i for b in candidate for i in b.items]
        if current and not _within(flat, max_items=max_items, char_budget=char_budget):
            chunks.append(current)
            current = [bucket]
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


_FENCE_RE = re.compile(r"```\s*json\s*\n(.*?)\n```", re.S)


def _validate_trailer(meta) -> tuple:
    """Validate a parsed trailer's shape, returning (folded, dropped). Raises ValueError
    on any mismatch so the caller retries rather than propagating a wrong id list —
    folded/dropped ids drive item relations and status writes downstream."""
    if not isinstance(meta, dict):
        raise ValueError("distribute proposal: trailer must be a JSON object")

    raw_folded = meta.get("folded")
    if raw_folded is None:
        folded = []
    elif isinstance(raw_folded, list) and all(isinstance(s, str) and s for s in raw_folded):
        folded = list(raw_folded)
    else:
        raise ValueError("distribute proposal: 'folded' must be a list of source ids")

    raw_dropped = meta.get("dropped")
    if raw_dropped is None:
        dropped = []
    elif isinstance(raw_dropped, list) and all(isinstance(d, dict) for d in raw_dropped):
        dropped = []
        for entry in raw_dropped:
            source_id = entry.get("source_id")
            if not isinstance(source_id, str) or not source_id:
                raise ValueError(
                    "distribute proposal: 'dropped' entries need a non-empty source_id string"
                )
            reason = entry.get("reason")
            if reason is not None and not isinstance(reason, str):
                raise ValueError("distribute proposal: 'dropped' reason must be a string")
            dropped.append({"source_id": source_id, "reason": reason})
    else:
        raise ValueError("distribute proposal: 'dropped' must be a list of objects")

    return folded, dropped


def parse_proposal(text) -> tuple:
    """(body_markdown, folded_source_ids, dropped) from a fill response.

    The LAST fenced block is the JSON trailer; everything before it is the page body.
    strict=False tolerates the raw control characters the model emits inside JSON strings.
    Raises ValueError when the trailer is missing, malformed, or wrong-shaped so the
    caller can retry."""
    matches = list(_FENCE_RE.finditer(text or ""))
    if not matches:
        raise ValueError("distribute proposal: no fenced JSON trailer found")
    trailer = matches[-1]
    try:
        meta = json.loads(trailer.group(1), strict=False)
    except json.JSONDecodeError as exc:
        raise ValueError(f"distribute proposal: malformed JSON trailer — {exc}") from exc
    body = text[: trailer.start()].rstrip()
    folded, dropped = _validate_trailer(meta)
    return body, folded, dropped


@dataclass(frozen=True)
class SectionOp:
    """One section write. `replace` rewrites an existing H1's children in place;
    `append` adds a new section at the end of the content region."""

    heading: str
    body_markdown: str
    action: str


def plan_sections(current_md, proposed_md) -> list:
    """Map the proposal's H1 sections onto the live page. Heading text is the join key,
    so the prompt forbids renaming an existing heading. Sections the proposal omits are
    never returned and never rewritten."""
    proposed = split_sections(proposed_md)
    seen = set()
    for heading, _ in proposed:
        if heading in seen:
            raise ValueError(f"distribute proposal: duplicate section heading {heading!r}")
        seen.add(heading)
    current = dict(split_sections(current_md))
    ops = []
    for heading, body in proposed:
        if current.get(heading) == body:
            continue  # byte-identical -> never rewritten, whatever the model echoed back
        ops.append(SectionOp(heading, body, "replace" if heading in current else "append"))
    return ops


def render_diff(current_md, proposed_md) -> str:
    """Per-section unified diff, before vs proposed. The review surface: untouched
    sections never appear, so a first-time build reads as all-additions."""
    current = dict(split_sections(current_md))
    chunks = []
    for op in plan_sections(current_md, proposed_md):
        before = current.get(op.heading, "")
        chunks.append(
            "\n".join(
                difflib.unified_diff(
                    before.splitlines(),
                    op.body_markdown.splitlines(),
                    fromfile=f"{op.heading} (current)",
                    tofile=f"{op.heading} (proposed)",
                    lineterm="",
                )
            )
        )
    return "\n\n".join(chunks) if chunks else "no changes proposed"


_QUICK_ACCESS_MARKER = "Quick Access"

_STAGED_FILES = (
    "batch.json",
    "current.md",
    "prompt.txt",
    "proposed.md",
    "proposal.json",
    "diff.txt",
)


def _slug(name) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "page"


def staging_dir(env, target) -> Path:
    d = Path(env.tmp_dir) / "distribute" / _slug(target)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clear_staging(env, target) -> None:
    """Remove any staged files left from a previous cycle. apply's only freshness check
    compares the live page to current.md, which can't detect a stale batch/proposal pair
    from before new items were routed — so every prepare invalidates the prior cycle."""
    d = staging_dir(env, target)
    for name in _STAGED_FILES:
        (d / name).unlink(missing_ok=True)


def _heading_text(block) -> str:
    return blocks_to_markdown([block]).lstrip("#").strip()


def _content_blocks(blocks) -> list:
    """The content region: everything from the page's FIRST heading_1 onward.

    Above it sits template furniture (table of contents, Quick Access, dividers) that this
    stage never reads or rewrites. Quick Access uses a lower heading level by convention;
    if it ever becomes an H1 the anchor cannot be identified, so stop rather than guess."""
    for index, block in enumerate(blocks or []):
        if block.get("type") == "heading_1":
            text = _heading_text(block)
            if _QUICK_ACCESS_MARKER in text:
                raise RuntimeError(
                    f"distribute: the page's first heading_1 is the {text!r} template section — "
                    "the content anchor cannot be identified; fix the page template first"
                )
            return list(blocks[index:])
    return []


def _routing_tags_for(routes, target) -> set:
    """Tags whose routes.json target is this page — the bucketing axis. Derived from the
    live routing config, so there is no second place to keep in sync."""
    return {tag for tag, page in routes.by_tag if page == target}


def prepare(env, *, target, routes, vocab, budgets, version) -> int:
    """Stage one target page for fill. Writes nothing to the page.

    Reconciles items already related to the page (proven folded in -> Distributed, no model
    call), then stages the rest. Returns the number of items staged; 0 means nothing left."""
    page_id = get_knowledge_page(env, target)
    _clear_staging(env, target)
    items = query_routed_for_target(env, target)

    already = [i for i in items if page_id in i.get("knowledge_base", [])]
    for item in already:
        write_distributed(env, item["page_id"], version)
    if already:
        log.info(
            "distribute.prepare: reconciled %d already-related items on %s", len(already), target
        )

    pending = [i for i in items if page_id not in i.get("knowledge_base", [])]
    if not pending:
        log.info("distribute.prepare: nothing pending for %s", target)
        return 0

    current_md = blocks_to_markdown(_content_blocks(get_page_blocks(env, page_id)))
    d = staging_dir(env, target)
    (d / "batch.json").write_text(
        json.dumps(
            {"target": target, "page_id": page_id, "items": pending}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    (d / "current.md").write_text(current_md, encoding="utf-8")
    log.info("distribute.prepare: staged %d items for %s", len(pending), target)
    return len(pending)


_FILL_ATTEMPTS = 3  # 1 initial + 2 retries for a transient or malformed reply
_RETRY_BACKOFF_S = 2.0


def _numbering_hint(current_md) -> str:
    """Mirror the page's own heading convention. Some knowledge pages number their headings
    (`1 Foundations`, `2.3 …`) and some do not; a mismatch reads as a different author."""
    for heading, _ in split_sections(current_md):
        if heading and heading[0].isdigit():
            return "this page numbers its headings — mirror the same scheme"
    return "this page does not number its headings — do not add numbers"


def _is_empty_page(current_md) -> bool:
    """True when the page has no real content yet — the first-time anchor (`# `, an empty
    heading with an empty body) counts as empty, not as a section named ""."""
    return not any(heading or body for heading, body in split_sections(current_md))


def _section_list(current_md) -> str:
    headings = [h for h, _ in split_sections(current_md) if h]
    return "\n".join(f"- {h}" for h in headings) or "(none — this page has no sections yet)"


def _render_buckets(buckets) -> str:
    lines = []
    for bucket in buckets:
        lines.append(f"### BUCKET: {bucket.name}")
        for item in bucket.items:
            title = item.get("title")
            ig_link = item.get("ig_link")
            summary = item.get("summary")
            head = f"- [{item.get('source_id')}]"
            if title is not None:
                head += f" «{title}»"
            if ig_link is not None:
                head += f" (url={ig_link})"
            lines.append(head)
            if summary is not None:
                lines.append(f"  summary: {summary}")
        lines.append("")
    return "\n".join(lines)


def render_prompt(template, *, target, current_md, buckets) -> str:
    """Substitute with str.replace, never str.format — the template contains literal JSON braces."""
    current = "(empty — this page has no content yet)" if _is_empty_page(current_md) else current_md
    return (
        template.replace("{page_name}", target)
        .replace("{numbering}", _numbering_hint(current_md))
        .replace("{sections}", _section_list(current_md))
        .replace("{current}", current)
        .replace("{buckets}", _render_buckets(buckets))
    )


def _check_reply(body, folded, dropped, known_ids) -> None:
    """Validate a parsed proposal against the chunk it was generated for. Raises ValueError
    (never a terminal-error marker word — see _TERMINAL_ERROR_MARKERS) from inside
    _fill_chunk's retried region, so a bad reply is retried rather than propagated: folded/
    dropped ids drive relation and status writes downstream, and a wrong id there is worse
    than a slower retry.

    Also rejects a reply shape that would silently lose content: split_sections drops
    everything before the first heading, so an empty heading (content with no name to join
    on) or unclaimed text ahead of the first heading (when folded ids say something WAS
    folded in) must fail loud instead of vanishing."""
    dropped_ids = [entry["source_id"] for entry in dropped]

    for source_id in folded + dropped_ids:
        if source_id not in known_ids:
            raise ValueError(f"distribute reply: id {source_id!r} is not in this batch")

    fold_seen, drop_seen = set(), set()
    for source_id in folded:
        if source_id in fold_seen:
            raise ValueError(f"distribute reply: duplicate folded id {source_id!r}")
        fold_seen.add(source_id)
    for source_id in dropped_ids:
        if source_id in drop_seen:
            raise ValueError(f"distribute reply: duplicate dropped id {source_id!r}")
        drop_seen.add(source_id)

    both = fold_seen & drop_seen
    if both:
        raise ValueError(f"distribute reply: id(s) {sorted(both)} are both folded and dropped")

    sections = split_sections(body)
    if any(not heading for heading, _ in sections):
        raise ValueError("distribute reply: a section has an empty heading")

    headings = [heading for heading, _ in sections]
    if folded and not headings:
        raise ValueError("distribute reply: folded is non-empty but the body has no section")
    if len(headings) != len(set(headings)):
        raise ValueError("distribute reply: repeated section heading in the reply")

    if folded:
        lines = body.splitlines()
        first_heading = next(i for i, line in enumerate(lines) if line.startswith("# "))
        if "\n".join(lines[:first_heading]).strip():
            raise ValueError("distribute reply: content before the first heading would be dropped")


def _fill_chunk(prompt, model, *, call, sleep, label, known_ids):
    """One consolidation call, parsed and validated. Retries transient failures AND a
    malformed or wrong-shaped proposal (claude-p is non-deterministic and usually succeeds
    on retry). A terminal error — auth expiry, usage limit — raises immediately; retrying
    those within a run is pointless."""
    last = None
    for attempt in range(1, _FILL_ATTEMPTS + 1):
        try:
            body, folded, dropped = parse_proposal(call(prompt, model))
            _check_reply(body, folded, dropped, known_ids)
            return body, folded, dropped
        except Exception as exc:  # noqa: BLE001 — classify, then retry or stop
            if is_terminal_error(exc):
                raise TerminalBackendError(
                    f"claude-p hit a terminal error during distribute ({label}): {exc}"
                ) from exc
            last = exc
            log.warning(
                "distribute.fill: attempt %d/%d failed for %s — %s",
                attempt,
                _FILL_ATTEMPTS,
                label,
                exc,
            )
            if attempt < _FILL_ATTEMPTS:
                sleep(_RETRY_BACKOFF_S * attempt)
    raise RuntimeError(f"distribute.fill: {label} failed after {_FILL_ATTEMPTS} attempts: {last}")


def _scrub(body_md, items, current_md) -> str:
    """Strip URLs and version tokens absent from the sources, one line at a time.

    scrub_fabricated's tidy regexes (collapsing empty parens, space-before-punctuation) run
    over the WHOLE cleaned text whenever anything was removed. Run over a multi-line body,
    that rewrites hand-written lines that had nothing fabricated on them at all
    (`useEffect()` -> `useEffect`, `3 : 1` -> `3: 1`) — enough to make an echoed untouched
    section look different from the current page and get rewritten. Scrubbing line by line
    confines that side effect to only the lines actually cleaned.

    The CURRENT page body is part of the source text: without it, legitimate pre-existing
    links in prose the model reproduced would be removed as if it had invented them."""
    parts = [current_md]
    for item in items:
        parts += [item.get("title"), item.get("summary"), item.get("ig_link")]
    source = " ".join(p for p in parts if p)

    removed_all, cleaned_lines = [], []
    for line in body_md.splitlines():
        cleaned, removed = scrub_fabricated(line, source)
        if removed:
            cleaned_lines.append(cleaned.rstrip())
            removed_all += removed
        else:
            cleaned_lines.append(line)
    if not removed_all:
        return body_md

    result = "\n".join(cleaned_lines)
    if body_md.endswith("\n"):
        result += "\n"
    log.info("distribute.fill: scrubbed fabricated tokens — %s", removed_all)
    return result


def fill(
    env, *, target, routes, vocab, budgets, model, template, call=None, sleep=time.sleep
) -> dict:
    """Consolidate the staged items into the page and stage the proposal for review.
    Writes nothing to Notion. Returns {"calls", "folded", "dropped"}."""
    call = call or claude_p.consolidate
    d = staging_dir(env, target)
    batch = json.loads((d / "batch.json").read_text(encoding="utf-8"))
    current_md = (d / "current.md").read_text(encoding="utf-8")

    buckets = bucket_items(
        batch["items"],
        _routing_tags_for(routes, target),
        vocab.content_types,
        max_items=budgets.max_items,
        char_budget=budgets.char_budget,
    )
    chunks = chunk_buckets(buckets, max_items=budgets.max_items, char_budget=budgets.char_budget)

    running, folded, dropped, prompts = current_md, [], [], []
    for index, chunk in enumerate(chunks, start=1):
        known_ids = {
            item.get("source_id")
            for bucket in chunk
            for item in bucket.items
            if item.get("source_id") is not None
        }
        prompt = render_prompt(template, target=target, current_md=running, buckets=chunk)
        prompts.append(prompt)
        body, chunk_folded, chunk_dropped = _fill_chunk(
            prompt,
            model,
            call=call,
            sleep=sleep,
            label=f"{target} call {index}/{len(chunks)}",
            known_ids=known_ids,
        )
        folded += chunk_folded
        dropped += chunk_dropped
        # Scrub THIS chunk's reply before merging: scrubbing the final merged body would
        # run scrub_fabricated's tidy regexes over sections the model never touched.
        body = _scrub(body, batch["items"], running)
        # Chain on the MERGED body: the next call must see the whole page as it now stands,
        # not just the sections the previous call happened to touch.
        running = merge_sections(running, body)

    proposed_md = running
    (d / "prompt.txt").write_text("\n\n=== NEXT CALL ===\n\n".join(prompts), encoding="utf-8")
    (d / "proposed.md").write_text(proposed_md, encoding="utf-8")
    (d / "proposal.json").write_text(
        json.dumps({"folded": folded, "dropped": dropped}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (d / "diff.txt").write_text(render_diff(current_md, proposed_md), encoding="utf-8")
    return {"calls": len(chunks), "folded": len(folded), "dropped": len(dropped)}


_SKIP_NOTE = "no new information — already covered on the page"

# The markdown review shows these, and a divider carries no content, so a section holding only
# these can be replaced without deleting anything the reviewer never saw.
_SHOWABLE_TYPES = {"heading_2", "heading_3", "bulleted_list_item", "paragraph", "divider"}
_REWRITTEN_TYPES = {"heading_2", "heading_3", "bulleted_list_item"}


def _section_spans(blocks) -> dict:
    """{heading_text: (heading_block_id, [content_blocks])} for each H1 in the content region."""
    spans, heading, content = {}, None, []
    for block in blocks:
        if block.get("type") == "heading_1":
            if heading is not None:
                spans[heading[0]] = (heading[1], content)
            heading, content = (_heading_text(block), block["id"]), []
        elif heading is not None:
            content.append(block)
    if heading is not None:
        spans[heading[0]] = (heading[1], content)
    return spans


def _unshowable_kinds(content) -> list:
    """What in a section the markdown review never showed: unrendered block types, and child
    blocks (sub-bullets, toggle contents) that Notion deletes together with their parent."""
    kinds = []
    for block in content:
        btype = block.get("type")
        if btype not in _SHOWABLE_TYPES and btype not in kinds:
            kinds.append(btype)
    if any(block.get("has_children") for block in content):
        kinds.append("has_children")
    return kinds


def _refuse_unshowable(blocks, ops) -> None:
    spans = _section_spans(blocks)
    problems = []
    for op in (o for o in ops if o.action == "replace"):
        kinds = _unshowable_kinds(spans[op.heading][1])
        if kinds:
            problems.append(
                f"section {op.heading!r} holds blocks the review cannot show ({', '.join(kinds)})"
            )
    if problems:
        raise RuntimeError(
            f"distribute.apply: {'; '.join(problems)} — "
            "edit it in Notion or leave it out of the proposal"
        )


def _is_rewritten(block) -> bool:
    """Dividers and empty paragraphs are template spacing, kept in place on a replace."""
    btype = block.get("type")
    if btype == "paragraph":
        return bool(blocks_to_markdown([block]).strip())
    return btype in _REWRITTEN_TYPES


def _empty_anchor_id(blocks):
    """The template's empty heading_1 — where a first-time build starts its content."""
    return next(
        (b["id"] for b in blocks if b.get("type") == "heading_1" and not _heading_text(b)), None
    )


def _write_sections(env, page_id, blocks, ops, *, empty_page) -> None:
    """Every content write, before any item write. Replaces run first, then appends against
    a freshly-read tail — a replace can move the page's last block, so the stale one is not
    a safe anchor.

    Only a page with no content yet starts at its empty heading_1: on a filled page a stray
    empty heading is not the anchor, and new sections go after the true page tail."""
    spans = _section_spans(blocks)
    for op in (o for o in ops if o.action == "replace"):
        heading_id, content = spans[op.heading]
        replace_section_blocks(
            env,
            page_id,
            after_block_id=heading_id,
            delete_block_ids=[b["id"] for b in content if _is_rewritten(b)],
            blocks=markdown_to_blocks(op.body_markdown),
        )

    appends = [o for o in ops if o.action == "append"]
    if not appends:
        return
    fresh = _content_blocks(get_page_blocks(env, page_id))
    anchor_id = _empty_anchor_id(fresh) if empty_page else None
    tail = fresh[-1]["id"] if fresh else None
    for op in appends:
        body = markdown_to_blocks(op.body_markdown)
        if anchor_id is not None:
            set_heading_text(env, anchor_id, op.heading)
            tail = replace_section_blocks(
                env, page_id, after_block_id=anchor_id, delete_block_ids=[], blocks=body
            )
            anchor_id = None
            continue
        if tail is None:
            raise RuntimeError(
                "distribute.apply: the target page has no content anchor — refusing to write"
            )
        tail = replace_section_blocks(
            env,
            page_id,
            after_block_id=tail,
            delete_block_ids=[],
            blocks=markdown_to_blocks(f"# {op.heading}") + body,
        )


def _write_items(env, page_id, items, proposal, proposed_md, version) -> dict:
    """Item state — ONLY after every section write succeeded. The relation goes first so an
    interruption can never leave an item marked Distributed without its provenance link.

    A relation makes every future prepare skip the item, so a folded item is linked only when
    its link is on the applied body; otherwise it stays Routed for the next cycle."""
    by_sid = {i["source_id"]: i for i in items if i.get("source_id")}
    folded = list(proposal.get("folded") or [])
    dropped = {d["source_id"]: d.get("reason") for d in proposal.get("dropped") or []}
    counts = {"folded": 0, "skipped": 0, "misrouted": 0, "unknown": 0, "unverified": 0}

    for sid in folded:
        item = by_sid.get(sid)
        if item is None:
            log.warning("distribute.apply: proposal folded unknown source_id %s — ignoring", sid)
            counts["unknown"] += 1
            continue
        ig_link = item.get("ig_link")
        if ig_link is None or f"]({ig_link})" not in proposed_md:
            log.warning(
                "distribute.apply: folded %s but its link is not on the page — left Routed", sid
            )
            counts["unverified"] += 1
            continue
        set_knowledge_relation(env, item["page_id"], page_id)
        write_distributed(env, item["page_id"], version)
        counts["folded"] += 1

    for sid, reason in dropped.items():
        item = by_sid.get(sid)
        if item is None:
            log.warning("distribute.apply: proposal dropped unknown source_id %s — ignoring", sid)
            counts["unknown"] += 1
            continue
        write_misrouted(env, item["page_id"], reason, version)
        counts["misrouted"] += 1

    handled = set(folded) | set(dropped)
    for sid, item in by_sid.items():
        if sid in handled:
            continue
        write_distributed(env, item["page_id"], version, notes=_SKIP_NOTE)
        counts["skipped"] += 1
    return counts


def _refuse_orphan_text(proposed_md) -> None:
    """split_sections drops text ahead of the first heading, so a hand edit placed there
    would silently never reach the page."""
    lines = proposed_md.splitlines()
    first = next((i for i, line in enumerate(lines) if line.startswith("# ")), len(lines))
    if "\n".join(lines[:first]).strip():
        raise RuntimeError(
            "distribute.apply: proposed.md has text before its first heading — "
            "it would be dropped; move it under a section"
        )


def _comparable_sections(md) -> list:
    """Sections as the page renders them after a write: empty paragraphs and dividers leave
    blank or no lines, and the anchor heading of an empty page renders as an empty section."""
    return [
        (heading, "\n".join(line for line in body.splitlines() if line.strip()))
        for heading, body in split_sections(md)
        if heading or body
    ]


def _already_applied(live_md, staged_current, proposed_md) -> bool:
    """True when the live page already renders the proposal's effect, so a re-run after a
    partial failure can resume at the item writes instead of rewriting sections.

    Comparing the live page against RAW proposed_md misses two things markdown_to_blocks
    normalizes away when a real write happens: an indented sub-bullet and trailing
    whitespace. Round-tripping the merged proposal through that same write/read path
    reproduces exactly what live_md contains after a real write. merge_sections also
    carries an untouched section forward when a reviewer deletes it from proposed.md by
    hand, so its removal alone doesn't look like drift. A duplicate-heading ValueError from
    merge_sections means the page isn't in a shape this check can reason about — treat it as
    not-yet-applied and let the caller's stale-page error fire instead."""
    try:
        merged = merge_sections(staged_current, proposed_md)
    except ValueError:
        return False
    expected_md = blocks_to_markdown(markdown_to_blocks(merged))
    return _comparable_sections(
        blocks_to_markdown(markdown_to_blocks(live_md))
    ) == _comparable_sections(expected_md)


def apply(env, *, target, version) -> dict:
    """Write the reviewed proposal to the page, then the item state. Aborts if the page moved
    under the proposal — unless it already carries the proposal (a run that failed after its
    section writes), in which case only the item state is written.

    Returns {"folded", "skipped", "misrouted", "unknown", "unverified"}: unverified counts
    folded items left Routed because their link is not on the applied body."""
    d = staging_dir(env, target)
    if not (d / "batch.json").exists():
        raise RuntimeError(f"distribute.apply: nothing staged for {target!r} — run prepare first")
    if any(not (d / name).exists() for name in ("current.md", "proposed.md", "proposal.json")):
        raise RuntimeError(
            f"distribute.apply: {target!r} is prepared but not filled — "
            "run the stage without --apply first"
        )
    batch = json.loads((d / "batch.json").read_text(encoding="utf-8"))
    proposal = json.loads((d / "proposal.json").read_text(encoding="utf-8"))
    staged_current = (d / "current.md").read_text(encoding="utf-8")
    proposed_md = (d / "proposed.md").read_text(encoding="utf-8")
    _refuse_orphan_text(proposed_md)

    page_id = batch["page_id"]
    blocks = _content_blocks(get_page_blocks(env, page_id))
    live_md = blocks_to_markdown(blocks)
    if live_md == staged_current:
        ops = plan_sections(staged_current, proposed_md)
        _refuse_unshowable(blocks, ops)
        _write_sections(env, page_id, blocks, ops, empty_page=_is_empty_page(staged_current))
    elif _already_applied(live_md, staged_current, proposed_md):
        log.info("distribute.apply: %s already carries the proposal — resuming item writes", target)
    else:
        raise RuntimeError(
            f"distribute.apply: {target!r} changed since prepare — re-run prepare to restage"
        )

    counts = _write_items(env, page_id, batch["items"], proposal, proposed_md, version)
    for name in _STAGED_FILES:
        (d / name).unlink(missing_ok=True)
    log.info("distribute.apply: %s — %s", target, counts)
    return counts
