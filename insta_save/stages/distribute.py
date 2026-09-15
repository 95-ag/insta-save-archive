"""Distribute stage — Routed -> Distributed | Misrouted. Folds a routed item's content
into its target Knowledge-DB page behind a diff review gate.

prepare (deterministic bucketing, no model) -> fill (claude-p consolidation, versioned
prompt) -> apply (section-level replace, then relations and statuses). Attended only:
prepare+fill write nothing to Notion, apply is a separate invocation."""

import logging
from collections import defaultdict
from dataclasses import dataclass

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


def bucket_items(items, routing_tags, content_types, *, max_items, char_budget) -> list:
    """Partition a page's routed items into prompt buckets. Deterministic, no model.

    Items sharing a routing-tag combo group together; a combo over either bound splits
    further by content_type. Sorted throughout so the same input renders the same prompt."""
    partitions = defaultdict(list)
    for item in items:
        partitions[_routing_combo(item, routing_tags)].append(item)

    buckets = []
    for combo in sorted(partitions):
        members = partitions[combo]
        name = " + ".join(combo) if combo else "untagged"
        if _within(members, max_items=max_items, char_budget=char_budget):
            buckets.append(Bucket(name, members))
            continue
        by_type = defaultdict(list)
        for item in members:
            by_type[_content_type(item, content_types) or "untyped"].append(item)
        for ctype in sorted(by_type):
            buckets.append(Bucket(f"{name} · {ctype}", by_type[ctype]))
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
