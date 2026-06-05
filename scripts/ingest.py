"""
Ingestion orchestrator — end-to-end pipeline for one collection.

Flow:
  1. Authenticate (cookie persistence / headful re-auth)
  2. Crawl the target collection for post URLs
  3. For each URL: extract metadata, dedup against Notion, write if new

Safe to interrupt and restart — deduplication ensures no duplicates on re-run.
"""

import logging
import sys
import time

from playwright.sync_api import sync_playwright, BrowserContext

from pipeline.config import Config, load_config, validate_notion_config
from pipeline.crawler import crawl_collection
from pipeline.extractor import extract_post
from pipeline.notion import create_page, query_by_source_id
from pipeline.session import ensure_authenticated

log = logging.getLogger(__name__)

from pipeline.ingest import ingest_with_context

def run(headless: bool = True) -> None:
    config = load_config()
    validate_notion_config(config)
    log.info("ingest: starting — collection=%r", config.target_collection)

    with sync_playwright() as pw:
        browser, context = ensure_authenticated(pw, headless=headless)
        try:
            stats = ingest_with_context(context, config)
        finally:
            browser.close()
            if not headless:
                from pipeline.display import close_display
                close_display()

    log.info(
        "ingest: done — created=%d skipped=%d failed=%d",
        stats["created"], stats["skipped"], stats["failed"],
    )


if __name__ == "__main__":
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Ingest Instagram collection into Notion.")
    parser.add_argument("--headed", action="store_true", help="Run with visible browser window.")
    args = parser.parse_args()
    run(headless=not args.headed)
