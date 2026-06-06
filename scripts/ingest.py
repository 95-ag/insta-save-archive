"""
Single-collection ingest — sync one collection (TARGET_COLLECTION) into Notion.

Reuses the same fail-safe sync as the batch run, restricted to one collection and
skipping discovery (the collection must already exist in collections.json). Useful
for re-syncing or testing a single collection.

Usage:
    # set TARGET_COLLECTION in .env, then:
    python scripts/ingest.py
    python scripts/ingest.py --dry-run
    python scripts/ingest.py --headed
"""

import argparse

from playwright.sync_api import sync_playwright

from pipeline.config import load_config, validate_notion_config
from pipeline.ingest import sync
from pipeline.observability import StageProgress, setup_logging
from pipeline.session import ensure_authenticated


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a single Instagram collection into Notion.")
    parser.add_argument("--headed", action="store_true", help="Visible browser window.")
    parser.add_argument("--dry-run", action="store_true", help="Compute the plan; write nothing.")
    parser.add_argument("--fresh", action="store_true", help="Ignore snapshot; re-crawl.")
    args = parser.parse_args()

    log_path = setup_logging("ingest")
    config = load_config()
    validate_notion_config(config)

    with sync_playwright() as pw:
        browser, context = ensure_authenticated(pw, headless=not args.headed)
        try:
            title = "Ingest (dry-run)" if args.dry_run else "Ingest"
            with StageProgress(f"{title} · {config.target_collection}") as progress:
                summary = sync(
                    context, config,
                    progress=progress,
                    collection_names=[config.target_collection],
                    fresh=args.fresh,
                    dry_run=args.dry_run,
                    discover=False,
                )
                progress.log_line(
                    f"creates={summary['creates']} · retags={summary['retags']} · "
                    f"unchanged={summary['unchanged']}"
                )
        finally:
            browser.close()
            if args.headed:
                from pipeline.display import close_display
                close_display()

    print(f"\nlog: {log_path}")


if __name__ == "__main__":
    main()
