"""Extract stage — Queued -> Extracted. Dispatches on the trusted Notion type.

CARRYOVER: trust Notion `type` (no re-detection); content guard (all-empty -> stays Queued);
inter-item delay in finally. Browser opened lazily — only Carousel/Post need it."""

import datetime
import logging
import random
import re
import time

from playwright.sync_api import sync_playwright

from insta_save.adapters.instagram.session import ensure_authenticated
from insta_save.adapters.notion import mark_failed, query_by_status_and_priority, write_extraction
from insta_save.engines.ocr import extract_carousel, extract_ocr_frames, extract_post
from insta_save.engines.transcript import extract_transcript
from insta_save.orchestrator.runner import run_priority_stage

log = logging.getLogger(__name__)

_SHORTCODE_RE = re.compile(r"/(p|reel|tv)/([A-Za-z0-9_-]+)")


def _shortcode(ig_link: str):
    m = _SHORTCODE_RE.search(ig_link or "")
    return m.group(2) if m else None


class _LazyBrowser:
    """Opens an authenticated context on first use; closed by the stage in finally.
    Reel-only runs never trigger a browser launch."""
    def __init__(self, playwright, env, headless: bool):
        self._pw, self._env, self._headless = playwright, env, headless
        self._browser = self._ctx = None

    def context(self):
        if self._ctx is None:
            self._browser, self._ctx = ensure_authenticated(self._pw, self._env, headless=self._headless)
        return self._ctx

    def close(self):
        if self._browser is not None:
            self._browser.close()


def run_extract_item(env, run_extract_cfg, browser, item) -> str:
    """Extract one item; write results; return a counter name ('extracted'/'no_content')."""
    ig_link, page_id = item["ig_link"], item["page_id"]
    if not ig_link:
        raise ValueError("ig_link is None — cannot extract")
    shortcode = _shortcode(ig_link)
    if not shortcode:
        raise ValueError(f"could not parse shortcode from {ig_link}")

    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    post_type = item.get("type") or "Unknown"
    results = {"extract_version": env.extract_version, "last_processed_at": now,
               "transcript": None, "transcript_language": None,
               "ocr_text": None, "carousel_slides": None, "ocr_frames": None}

    if post_type in ("Reel", "IGTV"):
        t = extract_transcript(ig_link=ig_link, shortcode=shortcode, tmp_dir=env.tmp_dir,
                               cookies_json=env.cookies_file,
                               model_size=run_extract_cfg.transcript_model,
                               vad=run_extract_cfg.transcript_vad)
        results["transcript"] = t["transcript"]
        results["transcript_language"] = t.get("transcript_language")
        frames = extract_ocr_frames(ig_link=ig_link, shortcode=shortcode, tmp_dir=env.tmp_dir,
                                    cookies_json=env.cookies_file)
        results["ocr_text"] = frames["text"] or None
        results["ocr_frames"] = frames
    elif post_type == "Carousel":
        results["carousel_slides"] = extract_carousel(
            context=browser.context(), ig_link=ig_link, shortcode=shortcode,
            tmp_dir=env.tmp_dir, cookies_json=env.cookies_file)
    elif post_type == "Post":
        results["carousel_slides"] = extract_post(
            context=browser.context(), ig_link=ig_link, shortcode=shortcode,
            tmp_dir=env.tmp_dir, cookies_json=env.cookies_file)
    else:
        log.warning("extract: %s unknown type %r — skipping", item.get("source_id"), post_type)

    has_content = bool(results["transcript"] or results["ocr_text"] or results["carousel_slides"])
    if not has_content:
        log.warning("extract: %s — no content, stays Queued", item.get("source_id"))
        return "no_content"
    write_extraction(env, page_id, results)
    time.sleep(env.notion_write_delay)
    return "extracted"


def run_extract_stage(env, run_extract_cfg, progress, *, limit=None, source_id=None,
                      group=None, collections_cfg=None, reextract=False, headless=True) -> dict:
    """Drive extraction over Queued (and, if reextract, Extracted) items in priority order."""
    statuses = ["Queued"] + (["Extracted"] if reextract else [])
    totals: dict = {}
    with sync_playwright() as pw:
        browser = _LazyBrowser(pw, env, headless)
        try:
            def _process(env_, item, ctx):
                # ctx (from the runner) is unused; the browser context comes from the
                # _LazyBrowser closure, opened lazily only for Carousel/Post items.
                try:
                    return run_extract_item(env_, run_extract_cfg, browser, item)
                finally:
                    time.sleep(random.uniform(env_.extract_delay_min, env_.extract_delay_max))

            def _on_error(env_, item, exc):
                mark_failed(env_, item["page_id"], str(exc))

            for status in statuses:
                counts = run_priority_stage(
                    env, status, _process, progress, on_error=_on_error,
                    limit=limit, source_id=source_id, group=group, collections_cfg=collections_cfg,
                    stage_key="extract", bar_label=f"Extract ({status})")
                for k, v in counts.items():
                    totals[k] = totals.get(k, 0) + v
        finally:
            browser.close()
    totals.setdefault("failed", 0)
    return totals
