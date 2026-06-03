"""
Phase 2 queue runner.

Queries Notion for Queued items, runs deep extraction on each, and writes
results back. One item at a time; fails loud and moves on.

Flow per item:
  1. Open a Playwright page for the post
  2. Detect type (reuse extractor.detect_type via the already-loaded page)
  3. Run extraction by type:
       Reel    → transcript + OCR frames
       Carousel → carousel slide OCR
       Post    → single image OCR (not yet implemented — skipped with note)
  4. write_extraction on success, mark_failed on any unhandled exception
  5. Respect notion_write_delay between Notion writes
"""

import datetime
import logging
import time

from playwright.sync_api import BrowserContext

from config import Config
from extractor import CAROUSEL_NEXT_SEL, VIDEO_SEL, AUDIO_BTN_SEL
from extractor_deep import extract_transcript, extract_carousel, extract_ocr_frames
from notion import mark_failed, query_by_status, write_extraction

log = logging.getLogger(__name__)


def _detect_type_from_page(ig_link: str, page) -> str:
    """Minimal type detection on an already-loaded page (mirrors extractor._detect_type)."""
    if "/reel/" in ig_link:
        return "Reel"
    if "/tv/" in ig_link:
        return "IGTV"
    if "/p/" in ig_link:
        if page.locator(CAROUSEL_NEXT_SEL).count() > 0:
            return "Carousel"
        if page.locator(VIDEO_SEL).count() > 0 and page.locator(AUDIO_BTN_SEL).count() > 0:
            return "Reel"
        return "Post"
    return "Unknown"


def _shortcode_from_link(ig_link: str) -> str | None:
    import re
    m = re.search(r"/(p|reel|tv)/([A-Za-z0-9_-]+)/", ig_link)
    return m.group(2) if m else None


def run_item(config: Config, context: BrowserContext, item: dict) -> None:
    """
    Run deep extraction for a single Queued item and write results to Notion.
    Raises on unexpected errors — caller decides whether to mark_failed.
    """
    page_id = item["page_id"]
    ig_link = item["ig_link"]
    source_id = item["source_id"]

    if not ig_link:
        raise ValueError("ig_link is None — cannot extract")

    shortcode = _shortcode_from_link(ig_link)
    if not shortcode:
        raise ValueError(f"could not parse shortcode from {ig_link}")

    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Load the page once; use it for type detection
    page = context.new_page()
    try:
        page.goto(ig_link, wait_until="domcontentloaded", timeout=20_000)
        time.sleep(2.5)
        post_type = _detect_type_from_page(ig_link, page)
    finally:
        page.close()

    log.info("queue: %s type=%s", source_id, post_type)

    results: dict = {
        "processing_version": config.processing_version,
        "last_processed_at": now,
        "transcript": None,
        "transcript_available": False,
        "ocr_text": None,
        "carousel_slides": None,
    }

    if post_type in ("Reel", "IGTV"):
        t = extract_transcript(
            ig_link=ig_link,
            shortcode=shortcode,
            tmp_dir=config.tmp_dir,
            model_size=config.whisper_model,
        )
        results["transcript"] = t["transcript"]
        results["transcript_available"] = t["transcript_available"]
        results["ocr_text"] = extract_ocr_frames(
            shortcode=shortcode,
            tmp_dir=config.tmp_dir,
        )

    elif post_type == "Carousel":
        slides = extract_carousel(
            context=context,
            ig_link=ig_link,
            shortcode=shortcode,
            tmp_dir=config.tmp_dir,
        )
        results["carousel_slides"] = slides

    elif post_type == "Post":
        # Single-image post OCR not yet implemented — logged, not failed
        log.info("queue: %s is a Post — single-image OCR not implemented, skipping OCR", source_id)

    else:
        log.warning("queue: %s unknown type %r — writing empty extraction", source_id, post_type)

    write_extraction(config, page_id, results)
    time.sleep(config.notion_write_delay)


def run_queue(
    config: Config,
    context: BrowserContext,
    limit: int | None = None,
    source_id: str | None = None,
) -> dict:
    """
    Process Queued items from Notion.

    Args:
        limit:     maximum number of items to process (None = all)
        source_id: if set, process only the item with this source_id

    Returns:
        {"expanded": int, "failed": int, "skipped": int}
    """
    items = query_by_status(config, "Queued")
    log.info("queue: found %d Queued items", len(items))

    if source_id:
        items = [i for i in items if i["source_id"] == source_id]
        if not items:
            log.warning("queue: no Queued item found with source_id=%r", source_id)

    if limit is not None:
        items = items[:limit]

    expanded = failed = skipped = 0

    for i, item in enumerate(items, 1):
        sid = item["source_id"]
        log.info("queue: [%d/%d] %s", i, len(items), sid)
        try:
            run_item(config, context, item)
            expanded += 1
            log.info("queue: expanded %s", sid)
        except Exception as exc:
            log.error("queue: failed %s — %s", sid, exc)
            try:
                mark_failed(config, item["page_id"], str(exc))
            except Exception as mark_exc:
                log.error("queue: could not mark %s as Failed — %s", sid, mark_exc)
            failed += 1

    log.info("queue: done — expanded=%d failed=%d skipped=%d", expanded, failed, skipped)
    return {"expanded": expanded, "failed": failed, "skipped": skipped}
