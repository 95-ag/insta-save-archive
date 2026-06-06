"""
Instagram collection crawler.

Enumerates the posts in a named collection and reports whether the crawl was
COMPLETE — i.e. it reached the bottom and stopped finding new posts on its own,
rather than giving up at a scroll cap or hitting a login redirect.

Completeness matters for the sync layer: a post's *absence* from a collection is
only trustworthy evidence for tag removal if the crawl that produced it was complete.

Collection URLs are built directly from config/collections.json (slug + numeric_id),
never scraped from the /saved/ index — that index lazy-loads unreliably. Discovering
new collections is the job of pipeline/discovery.py.

'all-posts' is Instagram's built-in catch-all view — excluded from sync reconciliation.
"""

import json
import logging
import re
import time
from pathlib import Path

from playwright.sync_api import BrowserContext

from pipeline.config import Config

INSTAGRAM_BASE = "https://www.instagram.com"

# Post links on a collection page
POST_LINK_SELECTOR = "a[href*='/p/'], a[href*='/reel/'], a[href*='/tv/']"

# Shortcode from a post href: /p/<code>/, /reel/<code>/, /tv/<code>/
_SHORTCODE_RE = re.compile(r"/(p|reel|tv)/([A-Za-z0-9_-]+)")

SCROLL_PAUSE = 2.0           # seconds after each scroll step
MAX_UNCHANGED_SCROLLS = 3    # consecutive (at-bottom + no-new) steps → complete
MAX_SCROLLS = 80             # hard cap; hitting it means crawl is INCOMPLETE

# JS: true when the viewport has reached the bottom of the scrollable page.
_AT_BOTTOM_JS = (
    "(window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 50)"
)

log = logging.getLogger(__name__)

ALL_POSTS_SLUG = "all-posts"  # Instagram's built-in catch-all — not a user collection

_COLLECTIONS_FILE = Path(__file__).parent.parent / "config" / "collections.json"


def _shortcode(href: str) -> str | None:
    m = _SHORTCODE_RE.search(href)
    return m.group(2) if m else None


def _canonical_url(href: str) -> str:
    m = _SHORTCODE_RE.search(href)
    if not m:
        return INSTAGRAM_BASE + href if href.startswith("/") else href
    return f"{INSTAGRAM_BASE}/{m.group(1)}/{m.group(2)}/"


def scroll_harvest(page, selector: str, extract) -> tuple[dict, bool]:
    """
    Scroll a lazy-loaded list to the bottom, accumulating items the whole way.

    Accumulating on EVERY step (not just at the end) is essential: Instagram
    virtualizes long lists, unmounting items that scroll out of view. A single
    harvest at the end would miss everything that scrolled past.

    extract(link_locator) -> (key, value) | None    — called per matched element.
    Items are de-duplicated by key.

    Returns (items_by_key, complete). complete is True only if we stopped because
    the list ended (reached bottom + no new items for MAX_UNCHANGED_SCROLLS steps),
    False if we hit MAX_SCROLLS first.
    """
    items: dict = {}

    def harvest() -> None:
        for link in page.locator(selector).all():
            try:
                kv = extract(link)
            except Exception:
                kv = None
            if kv is not None:
                items[kv[0]] = kv[1]

    stable = 0
    complete = False
    for _ in range(MAX_SCROLLS):
        harvest()
        before = len(items)
        at_bottom = bool(page.evaluate(_AT_BOTTOM_JS))

        page.evaluate("window.scrollBy(0, window.innerHeight)")
        time.sleep(SCROLL_PAUSE)

        harvest()  # catch items rendered by the scroll we just did
        added = len(items) - before

        if at_bottom and added == 0:
            stable += 1
            if stable >= MAX_UNCHANGED_SCROLLS:
                complete = True
                break
        else:
            stable = 0

    if not complete:
        log.warning(
            "crawler: scroll_harvest hit MAX_SCROLLS (%d) without reaching a stable "
            "bottom — result is INCOMPLETE (%d items)", MAX_SCROLLS, len(items)
        )
    return items, complete


def resolve_collection_url(config: Config) -> str:
    """
    Build the collection URL from config/collections.json (slug + numeric_id).

    No browser, no /saved/ index scrape — the index lazy-loads unreliably.
    Raises if the collection isn't in collections.json (run discovery first).
    """
    if config.target_collection.lower() == ALL_POSTS_SLUG:
        return f"{INSTAGRAM_BASE}/{config.ig_username}/saved/all-posts/"

    if not _COLLECTIONS_FILE.exists():
        raise RuntimeError(
            f"crawler: {_COLLECTIONS_FILE} not found. "
            "Run discovery first: python scripts/ingest_batch.py --discover-only"
        )

    data = json.loads(_COLLECTIONS_FILE.read_text(encoding="utf-8"))
    meta = data.get(config.target_collection, {})
    slug = meta.get("slug")
    numeric_id = meta.get("numeric_id")
    if not slug or not numeric_id:
        raise RuntimeError(
            f"crawler: collection {config.target_collection!r} missing slug/numeric_id "
            "in collections.json. Run: python scripts/ingest_batch.py --discover-only"
        )
    return f"{INSTAGRAM_BASE}/{config.ig_username}/saved/{slug}/{numeric_id}/"


def crawl_collection(context: BrowserContext, config: Config) -> tuple[list[dict], bool]:
    """
    Crawl one collection. Returns (posts, complete).

    posts    : list of {"shortcode": str, "url": canonical_url}
    complete : True if the crawl reached a stable bottom; False if capped/interrupted.

    Order is not guaranteed.
    """
    collection_url = resolve_collection_url(config)
    page = context.new_page()
    try:
        log.info("crawler: navigating to %s", collection_url)
        page.goto(collection_url, wait_until="domcontentloaded", timeout=20_000)
        time.sleep(2)

        if "accounts/login" in page.url:
            log.warning("crawler: redirected to login — session expired; crawl INCOMPLETE")
            return [], False

        def extract(link):
            href = link.get_attribute("href")
            if not href:
                return None
            code = _shortcode(href)
            if not code:
                return None
            return (code, _canonical_url(href))

        items, complete = scroll_harvest(page, POST_LINK_SELECTOR, extract)
        posts = [{"shortcode": code, "url": url} for code, url in items.items()]
        log.info(
            "crawler: '%s' → %d posts (complete=%s)",
            config.target_collection, len(posts), complete,
        )
        return posts, complete
    finally:
        page.close()
