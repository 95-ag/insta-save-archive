"""
Extraction CLI — Playwright browser pass.

Processes Queued items: downloads transcript (yt-dlp + whisper) and OCR text
(carousel slides or video frames), writes to Notion, sets status → Extracted.

Usage:
    python scripts/extract.py                    # process all Queued items
    python scripts/extract.py --limit 10
    python scripts/extract.py --source_id XXXXX  # one specific item
    python scripts/extract.py --headed           # show browser window (auto-launches VcXsrv)
"""

import argparse
import sys

from playwright.sync_api import sync_playwright

from pipeline.config import load_config, validate_notion_config
from pipeline.observability import StageProgress, setup_logging
from pipeline.extract_runner import run_extract_stage
from pipeline.session import ensure_authenticated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extraction: transcript + OCR for Queued items → status Extracted."
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

    log_path = setup_logging("extraction")
    config = load_config()
    validate_notion_config(config)

    with sync_playwright() as pw:
        browser, context = ensure_authenticated(pw, headless=not args.headed)
        try:
            with StageProgress("Extraction") as progress:
                result = run_extract_stage(
                    config=config,
                    context=context,
                    progress=progress,
                    limit=args.limit,
                    source_id=args.source_id,
                )
        finally:
            browser.close()
            if args.headed:
                from pipeline.display import close_display
                close_display()

    print(f"\nlog: {log_path}")
    sys.exit(0 if result["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
