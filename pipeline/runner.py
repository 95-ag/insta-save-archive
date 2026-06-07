"""
Shared priority-bucketed stage runner.

Every per-item processing stage (extraction, local enrichment) reads items in a
given pipeline_status, processes them highest-priority first, and writes results
back. This module owns that loop once:

    for bucket in PRIORITY_BUCKETS:          # High → Medium → Low → unprioritised
        for item in items(read_status, bucket):
            process_fn(config, item, ctx)    # does the work + Notion write + status

process_fn returns a counter name (e.g. "expanded", "skipped") that the runner
bumps on the StageProgress display. Failures raise; the runner counts them as
"failed" and routes the item to on_error (e.g. mark_failed) when given.

Items are read up front in bucket order so the progress bar has an accurate total;
processing still proceeds strictly High → Medium → Low → unprioritised.
"""

import logging

from pipeline.config import Config
from pipeline.notion import query_by_status_and_priority

log = logging.getLogger(__name__)

# Processed in this order. None is the unprioritised bucket — always last,
# nothing is dropped.
PRIORITY_BUCKETS = ["High", "Medium", "Low", None]


def _bucket_label(priority: str | None) -> str:
    return priority if priority is not None else "Unprioritised"


def run_priority_stage(
    config: Config,
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
) -> dict:
    """
    Drive a per-item stage over priority buckets, reporting into a StageProgress.

    Args:
        read_status: pipeline_status of items to pick up (e.g. "Queued", "Expanded").
        process_fn:  process_fn(config, item, ctx) -> str. Owns the work, the Notion
                     write, and the status transition. Returns a counter name.
        ctx:         shared per-stage resource passed to process_fn (e.g. a browser
                     BrowserContext for extraction, None for local enrichment).
        on_error:    optional on_error(config, item, exc) called when process_fn raises
                     (e.g. mark_failed). The runner always counts the item as "failed".
        limit:       cumulative cap on items processed across all buckets (None = all).
        source_id:   if set, process only the item with this source_id (it must be in
                     read_status); all others are skipped.
        stage_key:   label shown on the live status line (e.g. "extract", "enrich").
        bar_label:   description for the progress bar.

    Returns:
        dict of counter-name → count. Always includes "failed".
    """
    items: list[dict] = []
    for priority in PRIORITY_BUCKETS:
        for item in query_by_status_and_priority(config, read_status, priority):
            item["priority"] = priority
            items.append(item)

    if source_id:
        items = [i for i in items if i.get("source_id") == source_id]
        if not items:
            log.warning("runner: no %s item with source_id=%r", read_status, source_id)

    if limit is not None:
        items = items[:limit]

    log.info("runner: %d %s items to process", len(items), read_status)

    counts: dict[str, int] = {}
    bar = progress.add_bar(bar_label, total=len(items))

    current_bucket = "__unset__"
    for item in items:
        if item["priority"] != current_bucket:
            current_bucket = item["priority"]
            log.info("runner: bucket %s", _bucket_label(current_bucket))
        sid = item.get("source_id") or item["page_id"]
        progress.set_current(stage_key, sid)
        try:
            counter = process_fn(config, item, ctx)
            counts[counter] = counts.get(counter, 0) + 1
            progress.bump(counter)
        except Exception as exc:
            log.error("runner: failed %s — %s", sid, exc)
            if on_error is not None:
                try:
                    on_error(config, item, exc)
                except Exception as handler_exc:
                    log.error("runner: on_error failed for %s — %s", sid, handler_exc)
            counts["failed"] = counts.get("failed", 0) + 1
            progress.bump("failed")
        progress.advance(bar)

    counts.setdefault("failed", 0)
    return counts
