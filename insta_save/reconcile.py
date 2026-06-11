"""
Reconciliation — the safety-critical core of the ingest sync.

Pure function, no I/O: given what we crawled (desired) and what Notion currently
holds (current), compute the per-post plan of creates and tag changes.

Founding principle — presence is reliable, absence is not:
  • ADD a tag whenever we found the post in a collection (positive evidence is
    always trustworthy, even from an incomplete crawl).
  • REMOVE a tag only when its absence is trustworthy: the collection's crawl was
    COMPLETE this run, OR the collection was explicitly confirmed removed.

Invariants guaranteed:
  1. No tag removed unless complete[C] or C in confirmed_removed.
  2. A page is never deleted — at worst its managed tags go to empty.
  3. Adds never require completeness.
  4. Whole-collection removal requires explicit confirmation.
  5. Excluded collections (e.g. "All Posts") are never added or removed.
  6. Output is an absolute desired set per page → idempotent to apply.
"""

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Instagram's catch-all view — not a real membership signal; never reconciled.
DEFAULT_EXCLUDED = frozenset({"All Posts", "all-posts"})


@dataclass
class PostAction:
    source_id: str
    page_id: str | None        # None → create a new page
    url: str | None            # navigate here to extract metadata (creates only)
    current: set               # tags before
    final: set                 # tags after
    added: set
    removed: set


@dataclass
class Plan:
    creates: list = field(default_factory=list)        # PostAction, page_id is None
    retags: list = field(default_factory=list)         # PostAction, existing page changed
    unchanged: int = 0
    skipped_unsafe: list = field(default_factory=list) # {source_id, collection, reason}


def reconcile(
    desired: dict,
    post_urls: dict,
    notion_state: dict,
    complete_map: dict,
    confirmed_removed: set | None = None,
    excluded: frozenset = DEFAULT_EXCLUDED,
) -> Plan:
    """
    desired         : { source_id: set[collection_name] }   — from crawl snapshots
    post_urls       : { source_id: canonical_url }           — for creating new pages
    notion_state    : { source_id: {"page_id": str, "collections": set} }
    complete_map    : { collection_name: bool }              — was the crawl complete?
    confirmed_removed: set[collection_name]                  — --confirm-removed
    excluded        : collection names never touched (added or removed)
    """
    confirmed_removed = confirmed_removed or set()
    plan = Plan()

    for sid in set(desired) | set(notion_state):
        des = set(desired.get(sid, set())) - excluded
        entry = notion_state.get(sid)
        cur = set(entry["collections"]) if entry else set()
        page_id = entry["page_id"] if entry else None

        cur_excluded = cur & excluded          # protected — preserve verbatim
        cur_managed = cur - excluded

        add = des - cur_managed                # presence → always safe
        remove_candidates = cur_managed - des  # absence → must justify

        safe_remove = set()
        for c in remove_candidates:
            if complete_map.get(c, False) or c in confirmed_removed:
                safe_remove.add(c)
            else:
                plan.skipped_unsafe.append({
                    "source_id": sid,
                    "collection": c,
                    "reason": "crawl incomplete and not --confirm-removed",
                })

        final = ((cur_managed - safe_remove) | add) | cur_excluded

        if page_id is None:
            if not final:
                continue  # nothing to create
            plan.creates.append(PostAction(
                source_id=sid, page_id=None, url=post_urls.get(sid),
                current=set(), final=final, added=add, removed=set(),
            ))
        elif final != cur:
            plan.retags.append(PostAction(
                source_id=sid, page_id=page_id, url=None,
                current=cur, final=final, added=add, removed=safe_remove,
            ))
        else:
            plan.unchanged += 1

    log.info(
        "reconcile: creates=%d retags=%d unchanged=%d skipped_unsafe=%d",
        len(plan.creates), len(plan.retags), plan.unchanged, len(plan.skipped_unsafe),
    )
    return plan
