"""
Instagram post metadata extractor.

Navigates to a single post URL and extracts structured metadata.
All selectors are defined as module-level constants — update here when
Instagram changes its DOM.

Returns null for any field that cannot be extracted. Never returns empty
strings or placeholder text for nullable fields.
"""

import logging
import re
import time

from playwright.sync_api import BrowserContext

# ---------------------------------------------------------------------------
# Selectors — update here when Instagram changes its DOM
# ---------------------------------------------------------------------------

# Author: username links appear as a[role='link'] with href matching /{username}/
ROLE_LINK_SEL = "a[role='link'][href]"

# Caption: lives in the longest span[dir='auto'] on the page, prefixed with
# "{author}\n\xa0\n{relative_time}\n". Strip the prefix to get the caption.
CAPTION_SEL = "span[dir='auto']"

# Posted date: first time[datetime] on the page is the post date
DATE_SEL = "time[datetime]"

# Carousel indicator: navigation button present when post has multiple images
CAROUSEL_NEXT_SEL = "button[aria-label='Next']"

# Username pattern: /username/ — single path segment, alphanumeric + . _ -
_USERNAME_RE = re.compile(r'^/[A-Za-z0-9._]+/$')

# Non-user hrefs that match the pattern but are nav/system links
_NON_USER_HREFS = {'/', '/reels/', '/explore/', '/direct/', '/stories/', '/accounts/'}

# Shortcode extraction from post URL
_SHORTCODE_RE = re.compile(r'/(p|reel|tv)/([A-Za-z0-9_-]+)/')

# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)

INSTAGRAM_BASE = "https://www.instagram.com"
PAGE_LOAD_PAUSE = 2.5  # seconds after domcontentloaded before extracting


def _parse_shortcode(url: str) -> str | None:
    m = _SHORTCODE_RE.search(url)
    return m.group(2) if m else None


def _canonical_url(url: str) -> str:
    """Normalise to https://www.instagram.com/{type}/{shortcode}/"""
    m = _SHORTCODE_RE.search(url)
    if not m:
        return url
    return f"{INSTAGRAM_BASE}/{m.group(1)}/{m.group(2)}/"


# Video indicators on the post page
VIDEO_SEL = "video"
AUDIO_BTN_SEL = "button[aria-label='Toggle audio']"


def _detect_type(url: str, page) -> str:
    if "/reel/" in url:
        return "Reel"
    if "/tv/" in url:
        # IGTV is deprecated; /tv/ URLs still exist in old saved content
        return "IGTV"
    if "/p/" in url:
        if page.locator(CAROUSEL_NEXT_SEL).count() > 0:
            return "Carousel"
        # Reels cross-posted to feed appear as /p/ URLs but have a video element
        # and an audio toggle button — same signal as an explicit /reel/ URL
        if page.locator(VIDEO_SEL).count() > 0 and page.locator(AUDIO_BTN_SEL).count() > 0:
            return "Reel"
        return "Post"
    return "Unknown"


def _extract_author(page) -> str | None:
    links = page.locator(ROLE_LINK_SEL).all()
    for link in links:
        href = link.get_attribute("href") or ""
        if not _USERNAME_RE.match(href):
            continue
        if href in _NON_USER_HREFS:
            continue
        text = (link.inner_text() or "").strip()
        if text:
            return text.lstrip("@")
    return None


def _extract_caption(page, author: str | None) -> str | None:
    spans = page.locator(CAPTION_SEL).all()
    candidates = []
    for span in spans:
        try:
            text = (span.inner_text() or "").strip()
            if text:
                candidates.append(text)
        except Exception:
            pass

    if not candidates:
        return None

    # The caption span is the longest one
    raw = max(candidates, key=len)

    # Strip the "{author}\n\xa0\n{relative_time}\n" prefix
    lines = raw.split("\n")
    if len(lines) >= 3 and lines[1].strip() in ("\xa0", ""):
        # lines[0]=author, lines[1]=\xa0, lines[2]=relative_time, lines[3:]=caption
        caption = "\n".join(lines[3:]).strip()
    elif author and lines and lines[0].strip() == author:
        caption = "\n".join(lines[1:]).strip()
    else:
        caption = raw

    return caption or None


def _extract_date(page) -> str | None:
    times = page.locator(DATE_SEL).all()
    if not times:
        return None
    dt = times[0].get_attribute("datetime")
    return dt or None


def extract_post(context: BrowserContext, url: str, collection: str) -> dict:
    """
    Navigate to a post URL and return structured metadata.

    Always returns a dict with all Stage 1 fields. Nullable fields are None
    when extraction fails — never empty strings.
    """
    source_id = _parse_shortcode(url)
    ig_link = _canonical_url(url)

    base: dict = {
        "source_id": source_id,
        "ig_link": ig_link,
        "author": None,
        "type": "Unknown",
        "caption": None,
        "posted_date": None,
        "collection": collection,
    }

    page = context.new_page()
    try:
        page.goto(ig_link, wait_until="domcontentloaded", timeout=20_000)
        time.sleep(PAGE_LOAD_PAUSE)

        if "accounts/login" in page.url:
            log.warning("extractor: redirected to login for %s — session expired", url)
            return base

        base["type"] = _detect_type(ig_link, page)
        base["author"] = _extract_author(page)
        base["caption"] = _extract_caption(page, base["author"])
        base["posted_date"] = _extract_date(page)

        log.debug(
            "extractor: %s → author=%r type=%s date=%s caption_len=%s",
            source_id,
            base["author"],
            base["type"],
            base["posted_date"],
            len(base["caption"]) if base["caption"] else None,
        )
        return base

    except Exception as e:
        log.error("extractor: failed on %s — %s", url, e)
        log.debug("extractor: page HTML on failure:\n%s", page.content()[:3000])
        return base
    finally:
        page.close()


if __name__ == "__main__":
    import sys
    import json
    from playwright.sync_api import sync_playwright
    from session import ensure_authenticated
    from config import load_config

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    load_config()  # fail fast on missing env vars

    TEST_URLS = [
        "https://www.instagram.com/p/C--xP58PhNv/",
        "https://www.instagram.com/p/C27-6SoumUZ/",
    ]

    with sync_playwright() as pw:
        browser, context = ensure_authenticated(pw)
        try:
            for url in TEST_URLS:
                result = extract_post(context, url, collection="test")
                print(json.dumps(result, indent=2))
                print()
        finally:
            browser.close()
