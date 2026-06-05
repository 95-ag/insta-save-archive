"""
Phase 2 extraction CLI.

Usage:
    python run_extraction.py                    # process all Queued items
    python run_extraction.py --limit 10         # process up to 10 items
    python run_extraction.py --source_id XXXXX  # process one specific item
    python run_extraction.py --headed           # show browser window (auto-launches VcXsrv)
"""

import argparse
import logging
import sys

from playwright.sync_api import sync_playwright

from pipeline.config import load_config, validate_notion_config
from pipeline.queue_runner import run_queue
from pipeline.session import ensure_authenticated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Phase 2 deep extraction on Queued Notion items."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of items to process (default: all)",
    )
    parser.add_argument(
        "--source_id",
        type=str,
        default=None,
        metavar="ID",
        help="Process only the item with this source_id (shortcode)",
    )
    parser.add_argument("--headed", action="store_true", help="Run with visible browser window.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    config = load_config()
    validate_notion_config(config)

    with sync_playwright() as pw:
        browser, context = ensure_authenticated(pw, headless=not args.headed)
        try:
            result = run_queue(
                config=config,
                context=context,
                limit=args.limit,
                source_id=args.source_id,
            )
        finally:
            browser.close()
            if args.headed:
                from pipeline.display import close_display
                close_display()

    sys.exit(0 if result["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
