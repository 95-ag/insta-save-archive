"""
Instagram collection crawler.

Enumerates all saved post URLs in a named collection.
Returns canonical post URLs — extractor.py navigates to each one for metadata.

'all-posts' is Instagram's built-in catch-all view — not a user-created collection.
It contains every saved post regardless of collection membership. Use it only after
all named collections have been ingested, to capture posts not in any collection.

Usage:
    python crawler.py          # prints all URLs for TARGET_COLLECTION
"""

import json
import logging
import time
from pathlib import Path

from playwright.sync_api import BrowserContext

from pipeline.config import Config

_COLLECTIONS_FILE = Path(__file__).parent.parent / "config" / "collections.json"

INSTAGRAM_BASE = "https://www.instagram.com"

# Selector for post links visible on a collection page
POST_LINK_SELECTOR = "a[href*='/p/'], a[href*='/reel/'], a[href*='/tv/']"

# Selector for named collection links on the /saved/ index page
COLLECTION_LINK_SELECTOR = "a[href*='/saved/']"

SCROLL_PAUSE = 2.0      # seconds to wait after each scroll
MAX_UNCHANGED_SCROLLS = 3  # stop after this many scrolls with no new links

log = logging.getLogger(__name__)


ALL_POSTS_SLUG = "all-posts"  # Instagram's built-in catch-all — not a user collection


def _resolve_collection_url(page, config: Config) -> str:
    """
    Returns the full URL for the target collection.

    Named collections: navigates to /saved/ index and finds the matching link.
    'all-posts': constructs the URL directly. Should only be used after all
    named collections are processed — it contains every saved post, including
    duplicates of posts that already belong to named collections.
    """
    if config.target_collection.lower() == ALL_POSTS_SLUG:
        log.info("crawler: target is 'all-posts' (catch-all view, not a named collection)")
        return f"{INSTAGRAM_BASE}/{config.ig_username}/saved/all-posts/"

    # Fast path: construct URL directly from collections.json (slug + numeric_id).
    # Avoids navigating to the saved index, which Instagram partially lazy-loads.
    if _COLLECTIONS_FILE.exists():
        try:
            data = json.loads(_COLLECTIONS_FILE.read_text(encoding="utf-8"))
            meta = data.get(config.target_collection, {})
            slug = meta.get("slug")
            numeric_id = meta.get("numeric_id")
            if slug and numeric_id:
                url = f"{INSTAGRAM_BASE}/{config.ig_username}/saved/{slug}/{numeric_id}/"
                log.info("crawler: resolved '%s' from collections.json → %s", config.target_collection, url)
                return url
        except Exception as exc:
            log.warning("crawler: collections.json lookup failed — %s", exc)

    # Fallback: scrape the saved index (used if collection not in collections.json).
    saved_index = f"{INSTAGRAM_BASE}/{config.ig_username}/saved/"
    log.info("crawler: navigating to saved index to find collection '%s'", config.target_collection)
    page.goto(saved_index, wait_until="domcontentloaded", timeout=20_000)
    time.sleep(4)

    # Scroll until no new collection links appear — Instagram lazy-loads them.
    seen_count = 0
    unchanged = 0
    while unchanged < MAX_UNCHANGED_SCROLLS:
        current = len(page.locator(COLLECTION_LINK_SELECTOR).all())
        if current > seen_count:
            seen_count = current
            unchanged = 0
        else:
            unchanged += 1
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(SCROLL_PAUSE)
    log.info("crawler: found %d collection links on saved index", seen_count)

    links = page.locator(COLLECTION_LINK_SELECTOR).all()
    target = config.target_collection.lower()

    for link in links:
        text = (link.inner_text() or "").strip().lower()
        href = link.get_attribute("href") or ""
        if text == target or target in href.lower():
            url = INSTAGRAM_BASE + href if href.startswith("/") else href
            log.info("crawler: found collection '%s' at %s", config.target_collection, url)
            return url

    raise RuntimeError(
        f"crawler: collection '{config.target_collection}' not found on {saved_index}\n"
        f"Available collections: "
        + ", ".join(
            (link.inner_text() or "").strip()
            for link in page.locator(COLLECTION_LINK_SELECTOR).all()
            if (link.inner_text() or "").strip()
        )
    )


def _collect_post_urls(page) -> list[str]:
    """
    Scrolls the current collection page until no new post links appear.
    Returns deduplicated canonical post URLs.
    """
    seen: set[str] = set()
    unchanged = 0

    while unchanged < MAX_UNCHANGED_SCROLLS:
        links = page.locator(POST_LINK_SELECTOR).all()
        hrefs = {
            link.get_attribute("href")
            for link in links
            if link.get_attribute("href")
        }
        new_hrefs = hrefs - seen

        if new_hrefs:
            seen.update(new_hrefs)
            unchanged = 0
            log.debug("crawler: found %d new links (total %d)", len(new_hrefs), len(seen))
        else:
            unchanged += 1

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(SCROLL_PAUSE)

    urls = sorted(
        INSTAGRAM_BASE + href if href.startswith("/") else href
        for href in seen
    )
    return urls


def crawl_collection(context: BrowserContext, config: Config) -> list[str]:
    """
    Returns a list of canonical post URLs for the configured collection.
    Order is not guaranteed — caller should not assume feed order.
    """
    page = context.new_page()
    try:
        collection_url = _resolve_collection_url(page, config)

        log.info("crawler: navigating to collection %s", collection_url)
        page.goto(collection_url, wait_until="domcontentloaded", timeout=20_000)
        time.sleep(2)

        if "accounts/login" in page.url:
            raise RuntimeError("crawler: redirected to login — session may have expired")

        urls = _collect_post_urls(page)
        log.info("crawler: found %d posts in '%s'", len(urls), config.target_collection)
        return urls
    finally:
        page.close()
