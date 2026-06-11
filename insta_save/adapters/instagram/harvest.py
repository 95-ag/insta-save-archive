"""The one Instagram lazy-list harvester (D22 — retires list_collections.py's copy).

Instagram virtualizes long lists (saved index, collection grids): items unmount as
they scroll off-screen, so accumulate on EVERY step, not once at the end.
"""

import logging
import time

log = logging.getLogger(__name__)

SCROLL_PAUSE = 2.0           # seconds after each scroll step
MAX_UNCHANGED_SCROLLS = 3    # consecutive (at-bottom + no-new) steps → complete
MAX_SCROLLS = 80             # hard cap; hitting it means the crawl is INCOMPLETE

_AT_BOTTOM_JS = "(window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 50)"


def scroll_harvest(page, selector: str, extract) -> tuple[dict, bool]:
    """Scroll a lazy list to the bottom, accumulating items throughout.

    extract(link_locator) -> (key, value) | None — called per matched element; items
    de-duplicated by key. Returns (items_by_key, complete). complete is True only if
    the list ended (bottom + no new items for MAX_UNCHANGED_SCROLLS), False if capped.
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
        harvest()
        added = len(items) - before
        if at_bottom and added == 0:
            stable += 1
            if stable >= MAX_UNCHANGED_SCROLLS:
                complete = True
                break
        else:
            stable = 0

    if not complete:
        log.warning("harvest: hit MAX_SCROLLS (%d) without a stable bottom — INCOMPLETE (%d items)",
                    MAX_SCROLLS, len(items))
    return items, complete
