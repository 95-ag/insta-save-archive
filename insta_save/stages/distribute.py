"""Distribute stage — Routed -> Distributed | Misrouted. Folds a routed item's content
into its target Knowledge-DB page behind a diff review gate.

prepare (deterministic bucketing, no model) -> fill (claude-p consolidation, versioned
prompt) -> apply (section-level replace, then relations and statuses). Attended only:
prepare+fill write nothing to Notion, apply is a separate invocation."""

import difflib
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass

from insta_save.markdown_blocks import split_sections

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
            if reason is None:
                reason = ""
            elif not isinstance(reason, str):
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
