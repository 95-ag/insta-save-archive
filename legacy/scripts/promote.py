"""
Queue CLI — promote Imported items to Queued status.

Queued items are picked up by scripts/extract.py for deep extraction.
Safe to re-run — already-Queued or Extracted items are skipped (only
Imported items are targeted).

Usage:
    python scripts/promote.py --collection "<YOUR_COLLECTION>"   # one collection
    python scripts/promote.py --all-pilot                        # all pilot collections
    python scripts/promote.py --all-pilot --dry-run              # preview only
"""

import logging
import sys

from pipeline.collections import pilot_collections
from pipeline.config import load_config
from pipeline.notion import mark_queued, query_by_collection_and_status

log = logging.getLogger(__name__)


def queue_collection(config, collection_name: str, dry_run: bool = False) -> int:
    """
    Marks all Imported items in collection_name as Queued.
    Returns count of items promoted.
    """
    items = query_by_collection_and_status(config, collection_name, "Imported")
    log.info("queue: %s — %d Imported items found", collection_name, len(items))
    if dry_run:
        log.info("queue: dry-run — would promote %d items", len(items))
        return len(items)
    for item in items:
        mark_queued(config, item["page_id"])
    log.info("queue: %s — promoted %d items to Queued", collection_name, len(items))
    return len(items)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(
        description="Promote Imported items to Queued for extraction."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--collection", metavar="NAME", help="Single collection to queue.")
    group.add_argument("--all-pilot", action="store_true", help="Queue all 15 pilot collections.")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no writes.")
    args = parser.parse_args()

    config = load_config()
    total = 0

    if args.collection:
        total = queue_collection(config, args.collection, dry_run=args.dry_run)
    else:
        for entry in pilot_collections():
            total += queue_collection(config, entry.name, dry_run=args.dry_run)

    action = "Would promote" if args.dry_run else "Promoted"
    log.info("queue: done — %s %d items total", action, total)
