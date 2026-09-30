"""Distribute stage — Routed -> Distributed | Misrouted. Folds a routed item's content
into its target Knowledge-DB page behind a diff review gate.

prepare (deterministic bucketing, no model) -> fill (claude-p consolidation, versioned
prompt) -> apply (section-level replace, then relations and statuses). Attended only:
prepare+fill write no page content (the CLI ensures the schema, and prepare marks
already-related items Distributed), apply is a separate invocation."""

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
    query_items_for_rebuild,
    query_routed_for_target,
    remove_knowledge_relation,
    replace_section_blocks,
    reset_item_to_tagged,
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
    indented_bullets,
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


def _id_reason_list(raw, field) -> list:
    """Validate a trailer list of {source_id, reason} objects. `dropped` and `skipped` share
    the shape: an id the model accounts for, plus its stated reason (None when it gave none —
    never invented, see data-integrity.md)."""
    if raw is None:
        return []
    if not (isinstance(raw, list) and all(isinstance(d, dict) for d in raw)):
        raise ValueError(f"distribute proposal: {field!r} must be a list of objects")
    entries = []
    for entry in raw:
        source_id = entry.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError(
                f"distribute proposal: {field!r} entries need a non-empty source_id string"
            )
        reason = entry.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise ValueError(f"distribute proposal: {field!r} reason must be a string")
        entries.append({"source_id": source_id, "reason": reason})
    return entries


def _validate_trailer(meta) -> tuple:
    """Validate a parsed trailer's shape, returning (folded, dropped, skipped). Raises
    ValueError on any mismatch so the caller retries rather than propagating a wrong id list —
    these ids drive item relations and status writes downstream."""
    if not isinstance(meta, dict):
        raise ValueError("distribute proposal: trailer must be a JSON object")

    raw_folded = meta.get("folded")
    if raw_folded is None:
        folded = []
    elif isinstance(raw_folded, list) and all(isinstance(s, str) and s for s in raw_folded):
        folded = list(raw_folded)
    else:
        raise ValueError("distribute proposal: 'folded' must be a list of source ids")

    # A proposal staged before 'skipped' existed still parses: absent -> [].
    return folded, _id_reason_list(meta.get("dropped"), "dropped"), _id_reason_list(
        meta.get("skipped"), "skipped"
    )


def parse_proposal(text) -> tuple:
    """(body_markdown, folded_source_ids, dropped, skipped) from a fill response.

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
    folded, dropped, skipped = _validate_trailer(meta)
    return body, folded, dropped, skipped


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
    sections never appear, so a first-time build reads as all-additions.

    A nested bullet is shown nested here but lands flat on the page, so the diff is prefixed
    with a note naming those lines — otherwise the review shows structure the write cannot
    keep."""
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
    diff = "\n\n".join(chunks) if chunks else "no changes proposed"
    nested = indented_bullets(proposed_md)
    if nested:
        warning = [
            f"NOTE: {len(nested)} indented bullet(s) below will land FLAT on the page — "
            "Notion nesting is not written by this stage:",
            *(f"  {line}" for line in nested),
        ]
        diff = "\n".join(warning) + "\n\n" + diff
    return diff


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

    blocks = get_page_blocks(env, page_id)
    current_md = blocks_to_markdown(_content_blocks(blocks))
    read_only = read_only_sections(blocks)
    d = staging_dir(env, target)
    (d / "batch.json").write_text(
        json.dumps(
            {
                "target": target,
                "page_id": page_id,
                "items": pending,
                "read_only_sections": read_only,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (d / "current.md").write_text(current_md, encoding="utf-8")
    log.info("distribute.prepare: staged %d items for %s", len(pending), target)
    if read_only:
        log.info(
            "distribute.prepare: %s has %d read-only section(s) the model must not rewrite: %s",
            target,
            len(read_only),
            ", ".join(read_only),
        )
    return len(pending)


_FILL_ATTEMPTS = 3  # 1 initial + 2 retries for a transient or malformed reply
_RETRY_BACKOFF_S = 2.0


def _numbering_hint(current_md) -> str:
    """Mirror the page's own heading convention. Some knowledge pages number their headings
    (`1 Foundations`, `2.3 …`) and some do not; a mismatch reads as a different author. On a
    numbered page, name the NEXT number: an appended section has no neighbour to copy from."""
    numbers = []
    for heading, _ in split_sections(current_md):
        if heading and heading[0].isdigit():
            leading = heading.split()[0].split(".")[0]
            if leading.isdigit():
                numbers.append(int(leading))
    if not numbers:
        return "this page does not number its headings — do not add numbers"
    return (
        f"this page numbers its headings — mirror the same scheme; "
        f"the next top-level number is {max(numbers) + 1}"
    )


def _is_empty_page(current_md) -> bool:
    """True when the page has no real content yet — the first-time anchor (`# `, an empty
    heading with an empty body) counts as empty, not as a section named ""."""
    return not any(heading or body for heading, body in split_sections(current_md))


def _section_list(current_md, read_only=()) -> str:
    """The page's section headings for the prompt. A read-only heading is marked inline: the
    model has to be told which sections it must not return, since nothing in the markdown it
    is shown reveals that those sections hold blocks the write cannot reproduce."""
    marked = set(read_only)
    lines = []
    for heading, _ in split_sections(current_md):
        if not heading:
            continue
        suffix = "  [READ-ONLY — hand-made; never return this section]" if heading in marked else ""
        lines.append(f"- {heading}{suffix}")
    return "\n".join(lines) or "(none — this page has no sections yet)"


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


def render_prompt(template, *, target, current_md, buckets, read_only=()) -> str:
    """Substitute with str.replace, never str.format — the template contains literal JSON braces."""
    current = "(empty — this page has no content yet)" if _is_empty_page(current_md) else current_md
    return (
        template.replace("{page_name}", target)
        .replace("{numbering}", _numbering_hint(current_md))
        .replace("{sections}", _section_list(current_md, read_only))
        .replace("{current}", current)
        .replace("{buckets}", _render_buckets(buckets))
    )


def _check_reply(body, folded, dropped, skipped, links_by_id, read_only=()) -> None:
    """Validate a parsed proposal against the chunk it was generated for. Raises ValueError
    (never a terminal-error marker word — see _TERMINAL_ERROR_MARKERS) from inside
    _fill_chunk's retried region, so a bad reply is retried rather than propagated: folded/
    dropped ids drive relation and status writes downstream, and a wrong id there is worse
    than a slower retry.

    Also rejects a reply shape that would silently lose content: split_sections drops
    everything before the first heading, so an empty heading (content with no name to join
    on) or unclaimed text ahead of the first heading (when folded ids say something WAS
    folded in) must fail loud instead of vanishing.

    `links_by_id` maps every id in the chunk to its ig_link (None when it has none), so a
    claim of folding can be checked against the body rather than trusted. `read_only` names the
    page's hand-made sections: returning one is caught here so the model retries, rather than at
    apply, where _refuse_unshowable would abort a run the reviewer had already confirmed."""
    dropped_ids = [entry["source_id"] for entry in dropped]
    skipped_ids = [entry["source_id"] for entry in skipped]

    for source_id in folded + dropped_ids + skipped_ids:
        if source_id not in links_by_id:
            raise ValueError(f"distribute reply: id {source_id!r} is not in this batch")

    seen = {}
    for field, ids in (("folded", folded), ("dropped", dropped_ids), ("skipped", skipped_ids)):
        for source_id in ids:
            if source_id in seen:
                raise ValueError(
                    f"distribute reply: id {source_id!r} appears in both "
                    f"{seen[source_id]!r} and {field!r}"
                    if seen[source_id] != field
                    else f"distribute reply: duplicate {field} id {source_id!r}"
                )
            seen[source_id] = field

    sections = split_sections(body)
    if any(not heading for heading, _ in sections):
        raise ValueError("distribute reply: a section has an empty heading")

    headings = [heading for heading, _ in sections]
    if folded and not headings:
        raise ValueError("distribute reply: folded is non-empty but the body has no section")
    if len(headings) != len(set(headings)):
        raise ValueError("distribute reply: repeated section heading in the reply")

    rewritten = [heading for heading in headings if heading in set(read_only)]
    if rewritten:
        raise ValueError(
            f"distribute reply: section(s) {sorted(rewritten)} are read-only — they hold blocks "
            f"the page write cannot reproduce; add a new section instead of returning them"
        )

    if folded:
        lines = body.splitlines()
        first_heading = next(i for i, line in enumerate(lines) if line.startswith("# "))
        if "\n".join(lines[:first_heading]).strip():
            raise ValueError("distribute reply: content before the first heading would be dropped")

    # A claim of folding is only worth as much as the link that proves it. Without this the
    # model can list an id it never wrote about: apply then leaves the item Routed, but the
    # content is silently absent from the page (seen live — 6 of 29 items on a real page).
    # An item with no ig_link is exempt: rule 8 lets the model cite it without a link.
    missing = [
        source_id
        for source_id in folded
        if links_by_id.get(source_id) and f"]({links_by_id[source_id]})" not in body
    ]
    if missing:
        raise ValueError(
            f"distribute reply: folded id(s) {sorted(missing)} have no source link in the "
            f"body — every folded item must end its entry with its own link"
        )


_HREF_RE = re.compile(r"\]\((https?://[^)]+)\)")


def _dropped_links(running_md, body) -> list:
    """Source links the reply removed from a section that already had them.

    fill chains chunks: each reply is merged into the running body, so a later chunk that
    rewrites a section an earlier chunk created replaces it wholesale. Per-chunk validation
    cannot see that — the link was in the EARLIER chunk's body — so the loss only surfaced at
    apply, where the items were left Routed and had to be paid for again (2 of 17 on one live
    page, 1 of 47 on another). Sections the reply does not return are untouched and ignored."""
    current = dict(split_sections(running_md))
    missing = []
    for heading, proposed in split_sections(body):
        before = current.get(heading)
        if before is None:
            continue
        missing += [
            href for href in _HREF_RE.findall(before) if href not in set(_HREF_RE.findall(proposed))
        ]
    return missing


def _fill_chunk(prompt, model, *, call, sleep, label, links_by_id, read_only=(), running_md=""):
    """One consolidation call, parsed and validated. Retries transient failures AND a
    malformed or wrong-shaped proposal (claude-p is non-deterministic and usually succeeds
    on retry). A terminal error — auth expiry, usage limit — raises immediately; retrying
    those within a run is pointless."""
    last = None
    for attempt in range(1, _FILL_ATTEMPTS + 1):
        try:
            body, folded, dropped, skipped = parse_proposal(call(prompt, model))
            _check_reply(body, folded, dropped, skipped, links_by_id, read_only)
            lost = _dropped_links(running_md, body)
            if lost:
                raise ValueError(
                    f"distribute reply: rewriting a section dropped source link(s) "
                    f"{sorted(set(lost))} that were already on it — reproduce the whole section, "
                    f"including the entries you are keeping"
                )
            return body, folded, dropped, skipped
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
    # Fixed for the whole run: it describes the ORIGINAL page, so a section the model itself
    # adds in one chunk stays revisable by the next.
    read_only = batch.get("read_only_sections") or []

    buckets = bucket_items(
        batch["items"],
        _routing_tags_for(routes, target),
        vocab.content_types,
        max_items=budgets.max_items,
        char_budget=budgets.char_budget,
    )
    chunks = chunk_buckets(buckets, max_items=budgets.max_items, char_budget=budgets.char_budget)

    running, folded, dropped, skipped, prompts = current_md, [], [], [], []
    for index, chunk in enumerate(chunks, start=1):
        links_by_id = {
            item["source_id"]: item.get("ig_link")
            for bucket in chunk
            for item in bucket.items
            if item.get("source_id") is not None
        }
        prompt = render_prompt(
            template, target=target, current_md=running, buckets=chunk, read_only=read_only
        )
        prompts.append(prompt)
        body, chunk_folded, chunk_dropped, chunk_skipped = _fill_chunk(
            prompt,
            model,
            call=call,
            sleep=sleep,
            label=f"{target} call {index}/{len(chunks)}",
            links_by_id=links_by_id,
            read_only=read_only,
            running_md=running,
        )
        folded += chunk_folded
        dropped += chunk_dropped
        skipped += chunk_skipped
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
        json.dumps(
            {"folded": folded, "dropped": dropped, "skipped": skipped},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (d / "diff.txt").write_text(render_diff(current_md, proposed_md), encoding="utf-8")
    return {
        "calls": len(chunks),
        "folded": len(folded),
        "dropped": len(dropped),
        "skipped": len(skipped),
    }


# Fallback note for an item the model reports as skipped without giving a reason — it
# restates what 'skipped' means, and never invents a specific one (data-integrity.md).
_SKIP_NOTE = "no new information — already covered on the page"

# The markdown review shows these, and a divider carries no content, so a section holding only
# these can be replaced without deleting anything the reviewer never saw.
_SHOWABLE_TYPES = {"heading_2", "heading_3", "bulleted_list_item", "paragraph", "divider"}
_REWRITTEN_TYPES = {"heading_2", "heading_3", "bulleted_list_item"}


def _sections(blocks) -> list:
    """[(heading_block, [content_blocks])] for each H1 in the content region, in page order —
    duplicates included, so a caller that must see every section never loses one."""
    sections = []
    for block in blocks:
        if block.get("type") == "heading_1":
            sections.append((block, []))
        elif sections:
            sections[-1][1].append(block)
    return sections


def _section_spans(blocks) -> dict:
    """{heading_text: (heading_block_id, [content_blocks])} for each H1 in the content region."""
    return {_heading_text(h): (h["id"], content) for h, content in _sections(blocks)}


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


def read_only_sections(blocks) -> list:
    """Headings whose section holds blocks the markdown round-trip cannot represent, in page
    order. Rewriting such a section would delete what blocks_to_markdown never rendered, so the
    prompt names them and the model appends a new section instead of reusing them."""
    return [
        heading
        for heading, (_, content) in _section_spans(blocks).items()
        if _unshowable_kinds(content)
    ]


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


@dataclass(frozen=True)
class _Classification:
    """One classification of a batch's items against a fill proposal — the write rules
    `_write_items` applies, computed once and reused by `preview` so the two can never
    disagree about what apply will do."""

    linked: list  # items folded in with `](ig_link)` present on the applied body
    unverified: list  # items folded but whose link never landed — left Routed
    dropped: list  # (item, reason) dropped as off-topic
    skipped: list  # (item, reason) the model states are already covered on the page
    unmentioned: list  # items the model accounted for in NO list — left Routed, never marked
    unknown_folded: list  # source_ids folded but absent from this batch
    unknown_dropped: list  # source_ids dropped but absent from this batch


def _classify_items(items, proposal, proposed_md) -> _Classification:
    """Pure classification — no Notion calls, no logging. `folded`/`dropped` are read as the
    proposal trailer stored them: `folded` is a raw list (an id can recur), `dropped` is
    reduced to one reason per id first (a later duplicate wins), matching the order and
    dedup `_write_items` has always applied."""
    by_sid = {i["source_id"]: i for i in items if i.get("source_id")}
    folded = list(proposal.get("folded") or [])
    dropped_map = {d["source_id"]: d.get("reason") for d in proposal.get("dropped") or []}
    skipped_map = {d["source_id"]: d.get("reason") for d in proposal.get("skipped") or []}

    linked, unverified, unknown_folded = [], [], []
    for sid in folded:
        item = by_sid.get(sid)
        if item is None:
            unknown_folded.append(sid)
            continue
        ig_link = item.get("ig_link")
        if ig_link is None or f"]({ig_link})" not in proposed_md:
            unverified.append(item)
        else:
            linked.append(item)

    dropped, unknown_dropped = [], []
    for sid, reason in dropped_map.items():
        item = by_sid.get(sid)
        if item is None:
            unknown_dropped.append(sid)
            continue
        dropped.append((item, reason))

    # A skipped id absent from the batch needs no separate bucket: it is the same
    # "model named an id we did not send" error unknown_dropped already reports.
    skipped = []
    for sid, reason in skipped_map.items():
        item = by_sid.get(sid)
        if item is None:
            unknown_dropped.append(sid)
            continue
        skipped.append((item, reason))

    handled = set(folded) | set(dropped_map) | set(skipped_map)
    unmentioned = [item for sid, item in by_sid.items() if sid not in handled]

    return _Classification(
        linked, unverified, dropped, skipped, unmentioned, unknown_folded, unknown_dropped
    )


def _write_items(env, page_id, items, proposal, proposed_md, version) -> dict:
    """Item state — ONLY after every section write succeeded. The relation goes first so an
    interruption can never leave an item marked Distributed without its provenance link.

    A relation makes every future prepare skip the item, so a folded item is linked only when
    its link is on the applied body; otherwise it stays Routed for the next cycle."""
    c = _classify_items(items, proposal, proposed_md)
    counts = {
        "folded": 0,
        "skipped": 0,
        "misrouted": 0,
        "unknown": 0,
        "unverified": 0,
        "unaccounted": 0,
    }

    # Log every unknown-id / unverified-id warning before any item write, right after
    # classification — a later linked/dropped write that raises must never lose them.
    for sid in c.unknown_folded:
        log.warning("distribute.apply: proposal folded unknown source_id %s — ignoring", sid)
        counts["unknown"] += 1

    for item in c.unverified:
        log.warning(
            "distribute.apply: folded %s but its link is not on the page — left Routed",
            item["source_id"],
        )
        counts["unverified"] += 1

    for sid in c.unknown_dropped:
        log.warning("distribute.apply: proposal dropped unknown source_id %s — ignoring", sid)
        counts["unknown"] += 1

    for item in c.linked:
        set_knowledge_relation(env, item["page_id"], page_id)
        write_distributed(env, item["page_id"], version)
        counts["folded"] += 1

    for item, reason in c.dropped:
        write_misrouted(env, item["page_id"], reason, version)
        counts["misrouted"] += 1

    for item, reason in c.skipped:
        write_distributed(env, item["page_id"], version, notes=reason or _SKIP_NOTE)
        counts["skipped"] += 1

    # An item in none of the three lists was never accounted for. Marking it Distributed
    # would strand it: no relation, content not on the page, and never proposed again.
    for item in c.unmentioned:
        log.warning(
            "distribute.apply: %s appears in no trailer list — left Routed for the next run",
            item["source_id"],
        )
        counts["unaccounted"] += 1
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


@dataclass(frozen=True)
class _Staged:
    """The staged prepare+fill output for one target, as read from disk."""

    dir: Path
    batch: dict
    proposal: dict
    current_md: str
    proposed_md: str


def _load_staged(env, target) -> _Staged:
    """Read the staged files for `target`, raising apply's own errors when nothing is staged
    or fill hasn't run yet. Shared by `apply` and `preview` so both read exactly the same
    files, the same way — a preview can never describe a different run than apply performs."""
    d = staging_dir(env, target)
    if not (d / "batch.json").exists():
        raise RuntimeError(f"distribute.apply: nothing staged for {target!r} — run prepare first")
    if any(not (d / name).exists() for name in ("current.md", "proposed.md", "proposal.json")):
        raise RuntimeError(
            f"distribute.apply: {target!r} is prepared but not filled — "
            "run the stage without --apply first"
        )
    return _Staged(
        dir=d,
        batch=json.loads((d / "batch.json").read_text(encoding="utf-8")),
        proposal=json.loads((d / "proposal.json").read_text(encoding="utf-8")),
        current_md=(d / "current.md").read_text(encoding="utf-8"),
        proposed_md=(d / "proposed.md").read_text(encoding="utf-8"),
    )


@dataclass(frozen=True)
class SectionChange:
    """One section's change as `apply` would write it. `added`/`removed` are the `+`/`-`
    line counts from a unified diff between the current and proposed section body — every
    changed line counts, blank lines included; a sizing heuristic for the review summary,
    not a semantic diff."""

    heading: str
    action: str
    added: int
    removed: int


@dataclass(frozen=True)
class ApplyPreview:
    """Read-only summary of what `apply` would do for a target, built from staged files
    alone — makes no Notion calls. Item tuples never fabricate a display value: `title` is
    `None` when the source item has none (data-integrity.md) and the CLI falls back to the
    source_id for display. `folded`/`unverified`/`unmentioned` carry `(source_id, title)`;
    `dropped` and `skipped` carry `(source_id, title, reason)`."""

    sections: list
    folded: list
    unverified: list
    dropped: list
    skipped: list
    unmentioned: list
    diff_text: str


def _section_line_counts(before, after) -> tuple:
    """(added, removed) `+`/`-` line counts between two section bodies, via a plain unified
    diff — every changed line counts, blank lines included; a sizing heuristic for the
    review summary, not a semantic diff."""
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm=""))
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return added, removed


def preview(env, *, target) -> ApplyPreview:
    """What `apply` would do for `target`, read from staged files only — no Notion calls.
    Section changes come from the same `plan_sections` apply uses; item classification comes
    from the same `_classify_items` `_write_items` uses, so this can never disagree with a
    real apply. `diff_text` is recomputed via `render_diff` rather than read from the staged
    `diff.txt`, which goes stale after a hand edit to `proposed.md`."""
    staged = _load_staged(env, target)
    current_sections = dict(split_sections(staged.current_md))
    sections = [
        SectionChange(
            op.heading,
            op.action,
            *_section_line_counts(current_sections.get(op.heading, ""), op.body_markdown),
        )
        for op in plan_sections(staged.current_md, staged.proposed_md)
    ]

    c = _classify_items(staged.batch["items"], staged.proposal, staged.proposed_md)
    return ApplyPreview(
        sections=sections,
        folded=[(item["source_id"], item.get("title")) for item in c.linked],
        unverified=[(item["source_id"], item.get("title")) for item in c.unverified],
        dropped=[(item["source_id"], item.get("title"), reason) for item, reason in c.dropped],
        skipped=[(item["source_id"], item.get("title"), reason) for item, reason in c.skipped],
        unmentioned=[(item["source_id"], item.get("title")) for item in c.unmentioned],
        diff_text=render_diff(staged.current_md, staged.proposed_md),
    )


def apply(env, *, target, version) -> dict:
    """Write the reviewed proposal to the page, then the item state. Aborts if the page moved
    under the proposal — unless it already carries the proposal (a run that failed after its
    section writes), in which case only the item state is written.

    Returns {"folded", "skipped", "misrouted", "unknown", "unverified", "unaccounted"}:
    unverified counts folded items left Routed because their link is not on the applied body;
    unaccounted counts items the trailer named in no list, also left Routed."""
    staged = _load_staged(env, target)
    d = staged.dir
    batch, proposal = staged.batch, staged.proposal
    staged_current, proposed_md = staged.current_md, staged.proposed_md
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


@dataclass(frozen=True)
class RebuildPreview:
    """Read-only summary of what `rebuild` would do for a target. `sections` carries
    `(heading_text, blocks_to_delete)` per H1 section of the content region — the first
    heading_1 itself is kept (emptied), every later one counts as deleted. `blocks_kept` counts
    the dividers and empty paragraphs left in place. `items` carries `(source_id, title,
    status)` with `title` `None` when the item has none, for every item in scope.
    `relations_only` is the subset of `items` (same shape) whose status distribute does not
    own — `rebuild` removes only their relation to the page, leaving status and fields
    untouched. `refused` carries `(heading_text, sorted kinds)` for each section that makes
    `rebuild` refuse. `heading_needs_clearing` is True when the content region's first
    heading_1 (the template anchor) still carries text — a rebuild would empty it even when
    there are no items or blocks to touch."""

    sections: list
    blocks_to_delete: int
    blocks_kept: int
    items: list
    relations_only: list
    refused: list
    heading_needs_clearing: bool


def _is_template_spacing(block) -> bool:
    """Dividers and empty paragraphs belong to the page template, so a rebuild keeps them."""
    btype = block.get("type")
    if btype == "paragraph":
        return not blocks_to_markdown([block]).strip()
    return btype == "divider"


def _rebuild_deletes(region) -> list:
    """Content-region blocks a rebuild deletes: all but the first heading_1 (the template
    anchor, emptied instead) and the template spacing."""
    return [b for b in region[1:] if not _is_template_spacing(b)]


_HEADING_TYPES = {"heading_1", "heading_2", "heading_3"}


def _has_quick_access(block) -> bool:
    """True only for a HEADING carrying the marker — a bullet or paragraph that merely
    mentions "Quick Access" in prose is not the template section and must not refuse."""
    return block.get("type") in _HEADING_TYPES and _QUICK_ACCESS_MARKER in blocks_to_markdown(
        [block]
    )


def _rebuild_refusals(region) -> list:
    """[(heading_text, sorted kinds)] for each section holding what the markdown review never
    shows, or a `🔗 Quick Access` heading that has strayed into the content region — global-
    constraints.md: that section is never touched, so a rebuild must refuse rather than
    delete it. Checks the H1 itself for children too: deleting it would take them along."""
    refused = []
    for heading, content in _sections(region):
        kinds = _unshowable_kinds(content)
        if heading.get("has_children") and "has_children" not in kinds:
            kinds.append("has_children")
        if any(_has_quick_access(b) for b in [heading, *content]):
            kinds.append("quick_access")
        if kinds:
            refused.append((_heading_text(heading), sorted(kinds)))
    return refused


def refusal_messages(refused) -> list:
    """One or two messages per refused section — the Quick Access refusal is worded
    separately from an unshowable-block-type refusal since they are different problems for
    the user to fix in Notion."""
    messages = []
    for heading, kinds in refused:
        other_kinds = [k for k in kinds if k != "quick_access"]
        if "quick_access" in kinds:
            messages.append(f"section {heading!r} holds the Quick Access section")
        if other_kinds:
            messages.append(
                f"section {heading!r} holds blocks the review cannot show "
                f"({', '.join(other_kinds)})"
            )
    return messages


def _reraise_clearing_failure(target, exc) -> None:
    """Shared wording for either page-step write (block delete, heading clear) failing after
    the item loop has already committed — the same recovery applies to both: nothing else on
    the page was touched, and re-running rebuild picks up where this left off."""
    raise RuntimeError(
        f"distribute.rebuild: failed while clearing {target!r} — items were "
        f"already reset; re-run rebuild to finish: {exc}"
    ) from exc


# Statuses distribute owns: `rebuild` resets these to Tagged. Any other status (Extracted,
# Queued, Failed, Tagged, ...) means distribute never finished with the item, so a stale
# `Knowledge Base` relation is the only thing rebuild touches — the item's own state is left
# for whatever stage actually owns it.
_REBUILD_OWNED_STATUSES = {"Routed", "Distributed", "Misrouted"}


def rebuild_preview(env, *, target) -> RebuildPreview:
    """What `rebuild` would do for `target`. Notion reads only — never writes."""
    page_id = get_knowledge_page(env, target)
    region = _content_blocks(get_page_blocks(env, page_id))
    items = query_items_for_rebuild(env, target, page_id)
    deleted_ids = {b["id"] for b in _rebuild_deletes(region)}
    sections = [
        (
            _heading_text(heading),
            sum(1 for b in [heading, *content] if b["id"] in deleted_ids),
        )
        for heading, content in _sections(region)
    ]
    rows = [(item["source_id"], item.get("title"), item.get("status")) for item in items]
    return RebuildPreview(
        sections=sections,
        blocks_to_delete=len(deleted_ids),
        blocks_kept=sum(1 for b in region if _is_template_spacing(b)),
        items=rows,
        relations_only=[
            row
            for row, item in zip(rows, items)
            if item.get("status") not in _REBUILD_OWNED_STATUSES
        ],
        refused=_rebuild_refusals(region),
        heading_needs_clearing=bool(region) and bool(_heading_text(region[0])),
    )


def rebuild(env, *, target) -> dict:
    """Reset `target` to its template and every item distributed onto it back to Tagged, so
    route and distribute can run again. Destructive on the live page.

    Refuses before any write when the content region holds anything the markdown review
    cannot show (other block types, child blocks, a strayed Quick Access section): a rebuild
    must never delete content the user had no way to see first.

    Items go FIRST, page second: a failure among the items leaves the page untouched and a
    re-run picks up the rest, whereas a page wiped before its items would leave relations
    pointing at removed content. Within one item, the reset runs BEFORE the relation removal:
    if the removal then fails, the item is left Tagged but still related, so a re-run's
    relation-arm query catches it again — reordered the other way, a failed reset after a
    successful removal would drop the item out of scope on re-run.

    Only an item whose status distribute owns (Routed, Distributed, Misrouted) is reset to
    Tagged; any other status is left alone and only unlinked (`relations_only`).

    Because the item loop can run long, the page step re-reads the page and re-runs the
    refusal check on that fresh read before deleting anything — a page edited mid-run is
    caught here rather than blindly cleared.

    Re-running is safe: an already-reset page with no items in scope makes no writes.
    Returns {"items_reset", "relations_removed", "relations_only", "blocks_deleted",
    "heading_cleared", "page_skipped"}."""
    page_id = get_knowledge_page(env, target)
    region = _content_blocks(get_page_blocks(env, page_id))
    items = query_items_for_rebuild(env, target, page_id)

    refused = _rebuild_refusals(region)
    if refused:
        problems = "; ".join(refusal_messages(refused))
        raise RuntimeError(f"distribute.rebuild: {problems} — edit it in Notion first")

    counts = {"items_reset": 0, "relations_removed": 0, "relations_only": 0, "blocks_deleted": 0}
    for item in items:
        if item.get("status") in _REBUILD_OWNED_STATUSES:
            reset_item_to_tagged(env, item["page_id"])
            counts["items_reset"] += 1
            if remove_knowledge_relation(env, item["page_id"], page_id):
                counts["relations_removed"] += 1
        else:
            if remove_knowledge_relation(env, item["page_id"], page_id):
                counts["relations_removed"] += 1
            counts["relations_only"] += 1

    try:
        fresh_region = _content_blocks(get_page_blocks(env, page_id))
    except RuntimeError as exc:
        raise RuntimeError(
            f"distribute.rebuild: {target!r} changed during rebuild — items were already "
            f"reset and nothing on the page was deleted: {exc}"
        ) from exc
    if _rebuild_refusals(fresh_region):
        raise RuntimeError(
            f"distribute.rebuild: {target!r} changed during rebuild — nothing on the page was "
            "deleted (items were already reset; re-running rebuild is safe)"
        )

    heading_cleared = False
    page_skipped = not fresh_region
    if page_skipped:
        log.warning("distribute.rebuild: %s has no heading_1 — page left as is", target)
    else:
        anchor = fresh_region[0]
        delete_ids = [b["id"] for b in _rebuild_deletes(fresh_region)]
        if delete_ids:
            try:
                replace_section_blocks(
                    env,
                    page_id,
                    after_block_id=anchor["id"],
                    delete_block_ids=delete_ids,
                    blocks=[],
                )
            except RuntimeError as exc:
                _reraise_clearing_failure(target, exc)
            counts["blocks_deleted"] = len(delete_ids)
        if _heading_text(anchor):
            try:
                set_heading_text(env, anchor["id"], "")
            except RuntimeError as exc:
                _reraise_clearing_failure(target, exc)
            heading_cleared = True

    _clear_staging(env, target)
    counts["heading_cleared"] = heading_cleared
    counts["page_skipped"] = page_skipped
    log.info("distribute.rebuild: %s — %s", target, counts)
    return counts
