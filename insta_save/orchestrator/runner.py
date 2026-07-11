"""
Shared stage runner (v2).

Every per-item processing stage (extraction, enrich, tag) reads items in a
given status and writes results back. This module owns that loop once:

    for item in items(read_status):
        process_fn(env, item, ctx)       # does the work + Notion write + status

process_fn returns a counter name (e.g. "extracted", "skipped") that the runner
bumps on the StageProgress display. Failures raise; the runner counts them as
"failed" and routes the item to on_error (e.g. mark_failed) when given.

Items are read up front so the progress bar has an accurate total.

The optional `group` + `collections_cfg` filter narrows the run to items whose
collections map to the given group (e.g. `isa run --stage extract --group Hustling`).
The filter is applied after the up-front read and before source_id/limit so the
progress bar total reflects the filtered set.
"""

import logging
import time

from insta_save.adapters.notion import query_by_status
from insta_save.orchestrator import run_control

log = logging.getLogger(__name__)


def run_priority_stage(
    env,
    read_status: str,
    process_fn,
    progress,
    *,
    ctx=None,
    on_error=None,
    limit: int | None = None,
    source_id: str | None = None,
    stage_key: str = "item",
    bar_label: str = "Items",
    group: str | None = None,
    collections_cfg=None,
    write_delay: float = 0.0,
    delay_on: set | None = None,
) -> dict:
    """
    Drive a per-item stage over items in a given status, reporting into a StageProgress.

    NOTE: the name is historical. This once bucketed items by a `priority` Notion select,
    but that column was never populated (always the empty bucket), so the priority layer
    was removed as vestigial. The name is kept to avoid a rename rippling across every
    caller; it now does a single status query.

    Args:
        env:             opaque config/env passed through to process_fn/on_error/query.
        read_status:     status of items to pick up (e.g. "Queued", "Extracted").
        process_fn:      process_fn(env, item, ctx) -> str. Owns the work, the Notion
                         write, and the status transition. Returns a counter name.
        ctx:             shared per-stage resource passed to process_fn (e.g. a browser
                         BrowserContext for extraction, None for local enrichment).
        on_error:        optional on_error(env, item, exc) called when process_fn raises
                         (e.g. mark_failed). The runner always counts the item as "failed".
        limit:           cumulative cap on items processed (None = all).
        source_id:       if set, process only the item with this source_id (it must be in
                         read_status); all others are skipped.
        stage_key:       label shown on the live status line (e.g. "extract", "enrich").
        bar_label:       description for the progress bar.
        group:           if set, restrict to items whose collections map to this group.
        collections_cfg: CollectionsConfig used to resolve group membership (required when
                         group is set; no-op if group is None).
        write_delay:     seconds to sleep after a real Notion write. 0.0 disables the delay.
        delay_on:        set of counter names that represent a real Notion write (e.g.
                         {"tagged", "queued"}). sleep fires only when the counter returned
                         by process_fn is in this set. None disables the delay regardless
                         of write_delay.

    Returns:
        dict of counter-name → count. Always includes "failed".
    """
    items = query_by_status(env, read_status)

    if group is not None and collections_cfg is not None:
        items = [
            it for it in items
            if any(collections_cfg.group_of(c) == group for c in it.get("collections", []))
        ]

    if source_id:
        items = [i for i in items if i.get("source_id") == source_id]
        if not items:
            log.warning("runner: no %s item with source_id=%r", read_status, source_id)

    if limit is not None:
        items = items[:limit]

    log.info("runner: %d %s items to process", len(items), read_status)

    counts: dict[str, int] = {}
    bar = progress.add_bar(bar_label, total=len(items))

    for item in items:
        run_control.checkpoint()
        sid = item.get("source_id") or item["page_id"]
        progress.set_current(stage_key, sid)
        counter = None
        try:
            counter = process_fn(env, item, ctx)
            counts[counter] = counts.get(counter, 0) + 1
            progress.bump(counter)
        except Exception as exc:
            log.error("runner: failed %s — %s", sid, exc)
            if on_error is not None:
                try:
                    on_error(env, item, exc)
                except Exception as handler_exc:
                    log.error("runner: on_error failed for %s — %s", sid, handler_exc)
            counts["failed"] = counts.get("failed", 0) + 1
            progress.bump("failed")
        progress.advance(bar)
        if write_delay and delay_on and counter in delay_on:
            time.sleep(write_delay)

    counts.setdefault("failed", 0)
    return counts
