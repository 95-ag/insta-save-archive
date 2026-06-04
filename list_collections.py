"""
One-off script: lists all named collections from the Instagram saved index.
Run once to discover collection names, then delete.
"""

import logging
import time
import sys

from playwright.sync_api import sync_playwright
from session import ensure_authenticated
from config import load_config

INSTAGRAM_BASE = "https://www.instagram.com"
COLLECTION_LINK_SELECTOR = "a[href*='/saved/']"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

config = load_config()

with sync_playwright() as pw:
    browser, context = ensure_authenticated(pw)
    try:
        page = context.new_page()
        saved_index = f"{INSTAGRAM_BASE}/{config.ig_username}/saved/"
        log.info("navigating to %s", saved_index)
        page.goto(saved_index, wait_until="domcontentloaded", timeout=20_000)
        time.sleep(4)

        # Capture text+href on every scroll pass — Instagram virtualises the index
        # so items at the top get unmounted as you scroll down. Re-querying at the
        # end only sees what's currently in the DOM, missing everything above.
        collected: dict[str, str] = {}  # href -> text, insertion-ordered
        unchanged = 0
        while unchanged < 3:
            links = page.locator(COLLECTION_LINK_SELECTOR).all()
            before = len(collected)
            for link in links:
                text = (link.inner_text() or "").strip()
                href = link.get_attribute("href") or ""
                if text and "/saved/" in href and "all-posts" not in href and href not in collected:
                    collected[href] = text
            if len(collected) > before:
                unchanged = 0
            else:
                unchanged += 1
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)

        collections = list(collected.items())  # [(href, text), ...]
        # Re-order to (text, href) for display
        collections = [(text, href) for href, text in collections]

        print(f"\n{len(collections)} collections found:\n")
        for i, (name, href) in enumerate(collections, 1):
            print(f"  {i:2}. {name}")
            print(f"       {href}")

        page.close()
    finally:
        browser.close()
