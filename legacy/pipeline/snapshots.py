"""
Collection snapshots — durable per-collection crawl results for the ingest sync.

A snapshot records what a collection contained at crawl time, plus whether that
crawl was COMPLETE. Snapshots are the recovery mechanism (not an in-memory queue):
written to disk the moment a collection is crawled, so a crash loses at most the
in-flight crawl.

Reuse policy: a snapshot may be reused on a later run only if it is COMPLETE and
younger than max_age_minutes. An incomplete snapshot is never reused — we always
want a complete one before trusting absence for tag removal.

Stored under tmp/ingest/snapshots/<slug>.json (tmp/ is gitignored).
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_SNAPSHOT_DIR = Path(__file__).parent.parent / "tmp" / "ingest" / "snapshots"


def _path(slug: str) -> Path:
    return _SNAPSHOT_DIR / f"{slug}.json"


def write_snapshot(
    collection: str,
    slug: str,
    numeric_id: str,
    posts: list[dict],
    complete: bool,
) -> Path:
    """
    Persist a collection's crawl result. posts is [{"shortcode", "url"}, ...].
    crawled_at is stamped here (real wall clock — this is a side effect, not a
    resumable computation).
    """
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(slug)
    payload = {
        "collection": collection,
        "slug": slug,
        "numeric_id": numeric_id,
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        "complete": complete,
        "posts": posts,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log.debug("snapshots: wrote %s (%d posts, complete=%s)", path, len(posts), complete)
    return path


def read_snapshot(slug: str) -> dict | None:
    path = _path(slug)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("snapshots: could not read %s — %s", path, exc)
        return None


def is_reusable(snapshot: dict | None, max_age_minutes: int) -> bool:
    """
    True if the snapshot is COMPLETE and younger than max_age_minutes.
    Incomplete snapshots are never reusable. Unparseable timestamps → not reusable.
    """
    if not snapshot or not snapshot.get("complete"):
        return False
    crawled_at = snapshot.get("crawled_at")
    if not crawled_at:
        return False
    try:
        ts = datetime.fromisoformat(crawled_at)
    except ValueError:
        return False
    age_seconds = (datetime.now(timezone.utc) - ts).total_seconds()
    return age_seconds < max_age_minutes * 60


def clear_snapshots() -> int:
    """Delete all snapshots (used by --fresh). Returns count removed."""
    if not _SNAPSHOT_DIR.exists():
        return 0
    removed = 0
    for f in _SNAPSHOT_DIR.glob("*.json"):
        f.unlink()
        removed += 1
    log.info("snapshots: cleared %d snapshot(s)", removed)
    return removed
