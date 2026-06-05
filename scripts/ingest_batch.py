"""
Batch ingestion — processes all 43 collections in group priority order.

Reuses a single authenticated Playwright session across all collections to
avoid repeated login overhead. Deduplication in ingest_with_context ensures
already-imported items are silently skipped.

Usage:
    python ingest_batch.py                             # all collections, headless
    python ingest_batch.py --headed                    # visible browser
    python ingest_batch.py --start-from-group "Biz"   # skip groups before Biz
    python ingest_batch.py --dry-run                   # print order, don't ingest
"""

import dataclasses
import logging
import sys

from playwright.sync_api import sync_playwright

from pipeline.collections import ordered_for_ingestion, GROUP_PRIORITY
from pipeline.config import load_config, validate_notion_config
from pipeline.ingest import ingest_with_context
from pipeline.session import ensure_authenticated

log = logging.getLogger(__name__)


def run(headless: bool = True, start_from_group: str | None = None, dry_run: bool = False) -> None:
    config = load_config()
    validate_notion_config(config)

    collections = ordered_for_ingestion()

    if start_from_group:
        if start_from_group not in GROUP_PRIORITY:
            raise RuntimeError(
                f"Unknown group {start_from_group!r}. "
                f"Available: {', '.join(GROUP_PRIORITY)}"
            )
        collections = [
            e for e in collections
            if GROUP_PRIORITY.index(e.group) >= GROUP_PRIORITY.index(start_from_group)
        ]
        log.info("batch: resuming from group %r (%d collections)", start_from_group, len(collections))

    log.info("batch: %d collections to process", len(collections))

    if dry_run:
        print(f"\nDry-run — ingestion order ({len(collections)} collections):\n")
        for i, entry in enumerate(collections, 1):
            marker = "[EXTRACT]" if entry.extract else "         "
            print(f"  {i:2}. {marker} [{entry.group}] {entry.name}")
        return

    with sync_playwright() as pw:
        browser, context = ensure_authenticated(pw, headless=headless)
        try:
            total_created = total_skipped = total_failed = 0
            for i, entry in enumerate(collections, 1):
                log.info(
                    "batch: [%d/%d] %s (%s)",
                    i, len(collections), entry.name, entry.group,
                )
                col_config = dataclasses.replace(config, target_collection=entry.name)
                stats = ingest_with_context(context, col_config)
                log.info(
                    "batch: %s — created=%d skipped=%d failed=%d",
                    entry.name, stats["created"], stats["skipped"], stats["failed"],
                )
                total_created += stats["created"]
                total_skipped += stats["skipped"]
                total_failed += stats["failed"]
        finally:
            browser.close()
            if not headless:
                from pipeline.display import close_display
                close_display()

    log.info(
        "batch: complete — total created=%d skipped=%d failed=%d",
        total_created, total_skipped, total_failed,
    )


if __name__ == "__main__":
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Ingest all Instagram collections in priority order.")
    parser.add_argument("--headed", action="store_true", help="Run with visible browser.")
    parser.add_argument(
        "--start-from-group",
        metavar="GROUP",
        help=f"Skip groups before this one. Options: {', '.join(GROUP_PRIORITY)}",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print ingestion order without running.")
    args = parser.parse_args()
    run(headless=not args.headed, start_from_group=args.start_from_group, dry_run=args.dry_run)
