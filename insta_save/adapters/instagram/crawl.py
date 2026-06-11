"""Instagram crawls built on the one harvester: saved-index (collections) and
per-collection grids (post links). URLs come from collections.json (slug+numeric_id),
never by scraping the index (it lazy-loads unreliably)."""

import logging
import re
import time

from insta_save.adapters.instagram.harvest import scroll_harvest

log = logging.getLogger(__name__)

INSTAGRAM_BASE = "https://www.instagram.com"
ALL_POSTS_SLUG = "all-posts"

POST_LINK_SELECTOR = "a[href*='/p/'], a[href*='/reel/'], a[href*='/tv/']"
COLLECTION_LINK_SELECTOR = "a[href*='/saved/']"
_SHORTCODE_RE = re.compile(r"/(p|reel|tv)/([A-Za-z0-9_-]+)")
_COLLECTION_HREF_RE = re.compile(r"/saved/([^/]+)/(\d+)")

INDEX_WAIT = 4.0   # saved index needs ~4s to render (lessons.md)
GRID_WAIT = 2.0


def resolve_collection_url(ig_username: str, slug: str, numeric_id: str) -> str:
    if slug == ALL_POSTS_SLUG:
        return f"{INSTAGRAM_BASE}/{ig_username}/saved/all-posts/"
    return f"{INSTAGRAM_BASE}/{ig_username}/saved/{slug}/{numeric_id}/"


def _grid_extract(link):
    href = link.get_attribute("href") or ""
    m = _SHORTCODE_RE.search(href)
    if not m:
        return None
    return (m.group(2), f"{INSTAGRAM_BASE}/{m.group(1)}/{m.group(2)}/")


def _index_extract(link):
    href = link.get_attribute("href") or ""
    m = _COLLECTION_HREF_RE.search(href)
    if not m:
        return None
    slug, numeric_id = m.group(1), m.group(2)
    if slug == ALL_POSTS_SLUG:
        return None
    name = (link.inner_text() or "").strip()
    if not name:
        return None
    return (slug, {"name": name, "slug": slug, "numeric_id": numeric_id})


def crawl_collection(context, ig_username, slug, numeric_id) -> tuple[list[dict], bool]:
    """Crawl one collection grid → ([{shortcode, url}], complete)."""
    url = resolve_collection_url(ig_username, slug, numeric_id)
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        time.sleep(GRID_WAIT)
        if "accounts/login" in page.url:
            log.warning("crawl: login redirect on %s — session expired; INCOMPLETE", slug)
            return [], False
        items, complete = scroll_harvest(page, POST_LINK_SELECTOR, _grid_extract)
        return [{"shortcode": c, "url": u} for c, u in items.items()], complete
    finally:
        page.close()


def discover_collections(context, ig_username) -> tuple[dict, bool]:
    """Crawl the saved index → ({name: {slug, numeric_id}}, complete)."""
    page = context.new_page()
    try:
        page.goto(f"{INSTAGRAM_BASE}/{ig_username}/saved/",
                  wait_until="domcontentloaded", timeout=20_000)
        time.sleep(INDEX_WAIT)
        if "accounts/login" in page.url:
            log.warning("discover: login redirect — session expired; INCOMPLETE")
            return {}, False
        items, complete = scroll_harvest(page, COLLECTION_LINK_SELECTOR, _index_extract)
    finally:
        page.close()
    discovered = {v["name"]: {"slug": v["slug"], "numeric_id": v["numeric_id"]}
                  for v in items.values()}
    return discovered, complete
