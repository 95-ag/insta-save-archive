"""
Extraction stage runner.

Queries Notion for Queued items, runs deep extraction on each, and writes
results back (status → Extracted). One item at a time; fails loud and moves on.

Flow per item:
  1. Read type from the Notion stub (set by ingest via yt-dlp metadata)
  2. Run extraction by type:
       Reel/IGTV → transcript (yt-dlp + faster-whisper) + OCR frames (ffmpeg + RapidOCR)
       Carousel  → slide download + OCR (Playwright page navigation)
       Post      → single-image download + OCR (same pipeline as carousel, one slide)
  3. write_extraction on success; mark_failed on any unhandled exception
  4. Respect notion_write_delay between Notion writes
"""

import datetime
import logging
import random
import time

from playwright.sync_api import BrowserContext

from pipeline.config import Config
from pipeline.extractor_deep import extract_transcript, extract_carousel, extract_ocr_frames, extract_post
from pipeline.notion import mark_failed, write_extraction
from pipeline.runner import run_priority_stage

log = logging.getLogger(__name__)


def _shortcode_from_link(ig_link: str) -> str | None:
    import re
    m = re.search(r"/(p|reel|tv)/([A-Za-z0-9_-]+)/", ig_link)
    return m.group(2) if m else None


def run_extract_item(config: Config, context: BrowserContext, item: dict) -> bool:
    """
    Run deep extraction for a single Queued item and write results to Notion.
    Returns True if any content was extracted (transcript / ocr_text / carousel_slides).
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

    # Use the type already stored in Notion from ingest (set via yt-dlp metadata,
    # more reliable than DOM detection). Unknown is the safe fallback for old rows.
    post_type = item.get("type") or "Unknown"
    log.info("queue: %s type=%s", source_id, post_type)

    results: dict = {
        "processing_version": config.processing_version,
        "last_processed_at": now,
        "transcript": None,
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
        slides = extract_post(
            context=context,
            ig_link=ig_link,
            shortcode=shortcode,
            tmp_dir=config.tmp_dir,
        )
        results["carousel_slides"] = slides

    else:
        log.warning("queue: %s unknown type %r — skipping extraction", source_id, post_type)

    has_content = bool(results.get("transcript") or results.get("ocr_text") or results.get("carousel_slides"))
    if not has_content:
        log.warning("queue: %s — no content extracted, status unchanged (stays Queued)", source_id)
        return False

    write_extraction(config, page_id, results)
    time.sleep(config.notion_write_delay)
    return True


def run_extract_stage(
    config: Config,
    context: BrowserContext,
    progress,
    limit: int | None = None,
    source_id: str | None = None,
) -> dict:
    """
    Process Queued items from Notion in priority order (High → Medium → Low →
    unprioritised), driving the given StageProgress display.

    Args:
        progress:  StageProgress to report into (bar + counters)
        limit:     maximum number of items to process (None = all)
        source_id: if set, process only the Queued item with this source_id

    Returns:
        counter dict including at least {"extracted": int, "failed": int}
    """
    def _process(config: Config, item: dict, ctx: BrowserContext) -> str:
        try:
            had_data = run_extract_item(config, ctx, item)
            return "extracted" if had_data else "no_content"
        finally:
            time.sleep(random.uniform(config.extract_delay_min, config.extract_delay_max))

    def _on_error(config: Config, item: dict, exc: Exception) -> None:
        mark_failed(config, item["page_id"], str(exc))

    return run_priority_stage(
        config,
        "Queued",
        _process,
        progress,
        ctx=context,
        on_error=_on_error,
        limit=limit,
        source_id=source_id,
        stage_key="extract",
        bar_label="Extract",
    )
