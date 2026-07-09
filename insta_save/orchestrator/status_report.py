"""Per-group pipeline status tally and Failed-item retry logic.

build_status()   — one query_all_pages pass → per-group counts + TOTAL row
retry_failed()   — for each Failed page, infer prior status and requeue
"""

import logging
from insta_save.adapters.notion import query_all_pages, requeue
from insta_save.helpers import observability
from insta_save.helpers.observability import StageProgress

log = logging.getLogger(__name__)

_STATUSES = ("Imported", "Queued", "Extracted", "Tagged", "Routed", "Failed")


def _parse_page(page: dict) -> tuple:
    """Extract (page_id, status, collections, has_content) from a raw page dict.

    has_content is True when raw_extraction carries non-empty rich_text, indicating
    the item was extracted (used by retry_failed to infer the prior status)."""
    page_id = page.get("page_id", "")
    props = page.get("properties", {})

    status_select = props.get("status", {}).get("select") or {}
    status = status_select.get("name") if status_select else None

    collections = [c["name"] for c in props.get("collection", {}).get("multi_select", [])]

    raw_blocks = props.get("raw_extraction", {}).get("rich_text", [])
    has_content = bool(raw_blocks)

    return page_id, status, collections, has_content


def build_status(env, collections_cfg) -> list[dict]:
    """One query_all_pages pass; tally per group.

    Each row: {"group": name, "Imported": n, ..., "Routed": n, "Failed": n, "remaining": n}
    where remaining = Imported + Queued + Extracted.

    An item is counted once per distinct group it touches (not once per collection).
    Rows are ordered by collections_cfg.groups, extra groups after, TOTAL last."""
    pages = query_all_pages(env)

    # group -> {status -> count}
    tallies: dict[str, dict[str, int]] = {}

    for page in pages:
        _, status, collections, _ = _parse_page(page)
        if status is None:
            continue

        # Collect the distinct groups this item belongs to
        groups_hit = set()
        for col in collections:
            groups_hit.add(collections_cfg.group_of(col))
        if not groups_hit:
            groups_hit.add("uncategorized")

        for group in groups_hit:
            if group not in tallies:
                tallies[group] = {s: 0 for s in _STATUSES}
            if status in tallies[group]:
                tallies[group][status] += 1

    def _make_row(group: str) -> dict:
        counts = tallies.get(group, {s: 0 for s in _STATUSES})
        row = {"group": group}
        for s in _STATUSES:
            row[s] = counts.get(s, 0)
        row["remaining"] = row["Imported"] + row["Queued"] + row["Extracted"]
        return row

    # Rows ordered by config group order; extra groups (like uncategorized if not in config) after
    ordered_groups = list(collections_cfg.groups)
    # Any groups in tallies but not in config come after
    for g in sorted(tallies):
        if g not in ordered_groups:
            ordered_groups.append(g)

    rows = [_make_row(g) for g in ordered_groups if g in tallies]

    # TOTAL row
    total_row: dict = {"group": "TOTAL"}
    for s in _STATUSES:
        total_row[s] = sum(r[s] for r in rows)
    total_row["remaining"] = total_row["Imported"] + total_row["Queued"] + total_row["Extracted"]
    rows.append(total_row)

    return rows


def retry_failed(env) -> dict:
    """For each Failed page, infer prior status from content presence and requeue it.

    Inference rule:
      - has_content (raw_extraction non-empty) -> was Extracted; requeue to Extracted
      - no content                             -> was at most Queued; requeue to Queued

    Returns {"requeued": n, "to_extracted": n, "to_queued": n}."""
    # The scan walks every page (can be thousands) — show a spinner so the terminal
    # isn't silent for seconds. Auto-no-op under non-TTY (tests/pipes).
    with observability.spinner("Scanning Notion for Failed items…"):
        pages = query_all_pages(env)

    failed = [p for p in pages if _parse_page(p)[1] == "Failed"]

    to_extracted = 0
    to_queued = 0
    # A live bar over the requeue loop (one Notion write per item) — otherwise the
    # command looks frozen while it works.
    with StageProgress("Retry Failed") as progress:
        bar = progress.add_bar("Requeue", total=len(failed))
        for page in failed:
            page_id, _status, _cols, has_content = _parse_page(page)
            target = "Extracted" if has_content else "Queued"
            try:
                requeue(env, page_id, target)
            except Exception as exc:  # noqa: BLE001 — one bad requeue must not abort the loop
                log.error("status: requeue failed for %s — %s", page_id, exc)
                progress.advance(bar)
                continue
            if target == "Extracted":
                to_extracted += 1
                progress.bump("→Extracted")
            else:
                to_queued += 1
                progress.bump("→Queued")
            progress.advance(bar)

    requeued = to_extracted + to_queued
    log.info("status: requeued %d Failed items (%d→Extracted, %d→Queued)",
             requeued, to_extracted, to_queued)
    return {"requeued": requeued, "to_extracted": to_extracted, "to_queued": to_queued}
