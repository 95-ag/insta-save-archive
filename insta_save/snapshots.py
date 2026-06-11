"""Per-collection crawl snapshots — the discover→ingest hand-off.

discover writes one snapshot per crawled collection; ingest reads them to build the
reconcile inputs. Reusable iff complete and fresh, so an interrupted discover resumes
cheaply and ingest can run without re-crawling.
"""

import datetime
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _snap_dir(tmp_dir) -> Path:
    d = Path(tmp_dir) / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot_path(tmp_dir, slug: str) -> Path:
    return _snap_dir(tmp_dir) / f"{slug}.json"


def _utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_snapshot(tmp_dir, *, name, slug, numeric_id, posts, complete, now=None) -> None:
    payload = {
        "name": name, "slug": slug, "numeric_id": numeric_id,
        "complete": bool(complete), "crawled_at": now or _utc_now(),
        "posts": posts,
    }
    snapshot_path(tmp_dir, slug).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("snapshot: wrote %s (%d posts, complete=%s)", slug, len(posts), complete)


def read_snapshot(tmp_dir, slug: str):
    p = snapshot_path(tmp_dir, slug)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def is_reusable(snapshot, max_age_min: int, now=None) -> bool:
    """Reuse a snapshot only if it is complete AND younger than max_age_min."""
    if not snapshot or not snapshot.get("complete"):
        return False
    crawled = snapshot.get("crawled_at")
    if not crawled:
        return False
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    now_dt = (datetime.datetime.strptime(now, fmt) if now
              else datetime.datetime.now(datetime.UTC).replace(tzinfo=None))
    age_min = (now_dt - datetime.datetime.strptime(crawled, fmt)).total_seconds() / 60
    return 0 <= age_min < max_age_min
