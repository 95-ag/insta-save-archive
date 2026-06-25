"""Notion -> JSON snapshot (safety net before the #7 capstone wipe). `ts` is passed in
(never generated here) so the writer is deterministic and unit-testable."""
import json
from collections import defaultdict
from pathlib import Path

from insta_save.adapters.notion import query_all_pages


def backup(env, *, out_dir, ts) -> Path:
    pages = query_all_pages(env)
    out = Path(out_dir) / f"notion-{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"snapshot_ts": ts, "count": len(pages), "pages": pages},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    return out


def _parse_page(page: dict) -> tuple[str | None, str | None, list[str]]:
    """Extract (page_id, status, collection_names) from a raw properties page.

    Returns (None, None, []) for entirely missing structure; individual fields
    are None when absent so callers can record field problems.
    """
    page_id = page.get("page_id") or None
    props = page.get("properties") or {}
    status = None
    try:
        status = props["status"]["select"]["name"]
    except (KeyError, TypeError):
        pass
    collections = []
    try:
        collections = [c["name"] for c in props.get("collection", {}).get("multi_select", [])]
    except (TypeError, AttributeError):
        pass
    return page_id, status, collections


def _tallies(pages: list[dict], collections_cfg) -> tuple[dict, dict]:
    """Compute per-status and per-group tallies from a list of raw-properties pages.

    Group tally rule: each item is counted once per DISTINCT group it touches via its
    collection memberships (an item in two collections from the same group counts once
    for that group; an item in collections from two different groups counts for each).
    Items with no collection default to 'uncategorized'.
    """
    status_counts: dict[str, int] = defaultdict(int)
    group_counts: dict[str, int] = defaultdict(int)

    for page in pages:
        _, status, col_names = _parse_page(page)
        if status:
            status_counts[status] += 1
        # Collect distinct groups touched by this item's collections.
        groups = {collections_cfg.group_of(c) for c in col_names} if col_names else {"uncategorized"}
        for g in groups:
            group_counts[g] += 1

    return dict(status_counts), dict(group_counts)


def restore_check(env, backup_path: Path, collections_cfg) -> dict:
    """Dry restore-check: compare a backup snapshot against live Notion (no writes).

    Reads backup_path, queries live Notion via query_all_pages, then diffs:
    - total count
    - per-status tallies
    - per-group tallies

    Also validates each backup page for required fields (page_id, resolvable status).

    Returns:
        {
            "ok": bool,          # True iff no mismatches and no field problems
            "count": int,        # number of pages in the backup
            "mismatches": list   # human-readable diff entries (empty when ok)
        }

    Group tally rule: an item is counted once per distinct group it belongs to (see
    _tallies). This is consistent with how the enrich stage groups items — an item in
    collections spanning two groups contributes to both group tallies.
    """
    try:
        data = json.loads(Path(backup_path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"backup file unreadable ({backup_path}): {exc}") from exc
    backup_pages: list[dict] = data.get("pages", [])
    backup_count = len(backup_pages)

    mismatches: list[str] = []

    # --- field validation on backup pages ---
    for idx, page in enumerate(backup_pages):
        page_id, status, _ = _parse_page(page)
        label = page_id or f"page[{idx}]"
        if not page_id:
            mismatches.append(f"field problem: missing page_id on {label}")
        if status is None:
            mismatches.append(f"field problem: missing/unresolvable status on {label}")

    # --- tally comparison ---
    backup_status, backup_groups = _tallies(backup_pages, collections_cfg)
    live_pages = query_all_pages(env)
    live_count = len(live_pages)
    live_status, live_groups = _tallies(live_pages, collections_cfg)

    if backup_count != live_count:
        mismatches.append(
            f"count delta: backup={backup_count} live={live_count} "
            f"(diff={live_count - backup_count:+d})"
        )

    # per-status deltas
    all_statuses = set(backup_status) | set(live_status)
    for s in sorted(all_statuses):
        bv = backup_status.get(s, 0)
        lv = live_status.get(s, 0)
        if bv != lv:
            mismatches.append(f"status '{s}' delta: backup={bv} live={lv}")

    # per-group deltas
    all_groups = set(backup_groups) | set(live_groups)
    for g in sorted(all_groups):
        bv = backup_groups.get(g, 0)
        lv = live_groups.get(g, 0)
        if bv != lv:
            mismatches.append(f"group '{g}' delta: backup={bv} live={lv}")

    return {"ok": len(mismatches) == 0, "count": backup_count, "mismatches": mismatches}
