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

from playwright.sync_api import sync_playwright

from config import load_config, validate_notion_config
from crawler import crawl_collection
from extractor import extract_post
from notion import create_page, query_by_source_id
from session import ensure_authenticated

log = logging.getLogger(__name__)


def run(headless: bool = True) -> None:
    config = load_config()
    validate_notion_config(config)

    log.info("ingest: starting — collection=%r", config.target_collection)

    with sync_playwright() as pw:
        browser, context = ensure_authenticated(pw, headless=headless)
        try:
            urls = crawl_collection(context, config)
            log.info("ingest: found %d post URLs", len(urls))

            created = skipped = failed = 0

            for i, url in enumerate(urls, 1):
                log.info("ingest: [%d/%d] %s", i, len(urls), url)

                metadata = extract_post(context, url, config.target_collection)
                source_id = metadata.get("source_id")

                if not source_id:
                    log.warning("ingest: could not parse source_id from %s — skipping", url)
                    failed += 1
                    continue

                existing = query_by_source_id(config, source_id)
                if existing:
                    log.info("ingest: %s already exists (%s) — skipping", source_id, existing)
                    skipped += 1
                    continue

                try:
                    page_id = create_page(config, metadata)
                    log.info("ingest: created %s → %s", source_id, page_id)
                    created += 1
                except Exception as exc:
                    log.error("ingest: failed to write %s — %s", source_id, exc)
                    failed += 1

                time.sleep(config.notion_write_delay)

        finally:
            browser.close()
            if not headless:
                from display import close_display
                close_display()

    log.info(
        "ingest: done — created=%d skipped=%d failed=%d",
        created, skipped, failed,
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
