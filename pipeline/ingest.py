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

from pipeline.config import Config
from pipeline.crawler import crawl_collection
from pipeline.extractor import extract_post
from pipeline.notion import add_collection_if_missing, create_page, query_by_source_id


log = logging.getLogger(__name__)


def ingest_with_context(context: BrowserContext, config: Config) -> dict:
    """
    Ingest one collection using an existing Playwright context.
    Returns {"created": int, "skipped": int, "failed": int}.
    Caller owns the browser lifecycle.
    """
    urls = crawl_collection(context, config)
    log.info("ingest: found %d post URLs for %r", len(urls), config.target_collection)

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
            try:
                added = add_collection_if_missing(config, existing, config.target_collection)
                if added:
                    log.info(
                        "ingest: %s exists — added collection %r",
                        source_id, config.target_collection,
                    )
                else:
                    log.info(
                        "ingest: %s already in %r — skipping",
                        source_id, config.target_collection,
                    )
            except Exception as exc:
                log.warning("ingest: could not update collection for %s — %s", source_id, exc)
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

    return {"created": created, "skipped": skipped, "failed": failed}
