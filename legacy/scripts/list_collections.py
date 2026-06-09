"""
Discover and manage Instagram saved collections.

Default: print all collection names found in the saved index.
--update:  merge newly-discovered collections into config/collections.json.
           Existing entries (group, extract) are preserved — only new collections
           get defaults (group="Unclassified", extract=false).

Usage:
    python scripts/list_collections.py              # print collections
    python scripts/list_collections.py --update     # merge into config/collections.json
    python scripts/list_collections.py --headed     # visible browser
"""

import json
import logging
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from pipeline.config import load_config
from pipeline.session import ensure_authenticated

INSTAGRAM_BASE = "https://www.instagram.com"
COLLECTION_LINK_SELECTOR = "a[href*='/saved/']"
COLLECTIONS_FILE = Path(__file__).parent.parent / "config" / "collections.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _parse_href(href: str) -> tuple[str, str]:
    """
    Extract slug and numeric_id from a saved collection href.
    Format: /username/saved/{slug}/{numeric_id}/
    Returns ("", "") if the href doesn't match the expected pattern.
    """
    parts = [p for p in href.split("/") if p]
    # Expected: [username, "saved", slug, numeric_id]
    if len(parts) >= 4 and parts[1] == "saved":
        return parts[2], parts[3]
    return "", ""


def _discover(context, ig_username: str) -> dict[str, dict]:
    """
    Crawl the saved index and return discovered collections.
    Returns {name: {slug, numeric_id}} — no group/extract (caller annotates).
    """
    page = context.new_page()
    try:
        saved_index = f"{INSTAGRAM_BASE}/{ig_username}/saved/"
        log.info("navigating to %s", saved_index)
        page.goto(saved_index, wait_until="domcontentloaded", timeout=20_000)
        time.sleep(4)

        # Capture during scroll — Instagram virtualises the index, items
        # at the top get unmounted as you scroll. Re-querying at the end
        # misses everything that has scrolled out of the DOM.
        collected: dict[str, str] = {}  # href -> text
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

        discovered = {}
        for href, name in collected.items():
            slug, numeric_id = _parse_href(href)
            discovered[name] = {"slug": slug, "numeric_id": numeric_id}

        return discovered
    finally:
        page.close()


def _merge_into_json(discovered: dict[str, dict]) -> None:
    """
    Merge discovered collections into config/collections.json.
    Existing entries preserve their group and extract values.
    New entries get defaults: group="Unclassified", extract=false.
    """
    existing: dict[str, dict] = {}
    if COLLECTIONS_FILE.exists():
        existing = json.loads(COLLECTIONS_FILE.read_text(encoding="utf-8"))

    merged = {}
    new_count = 0
    for name, meta in discovered.items():
        if name in existing:
            # Preserve annotations, update discovery fields (slug, numeric_id may change)
            merged[name] = {
                **meta,
                "group": existing[name].get("group", "Unclassified"),
                "extract": existing[name].get("extract", False),
            }
        else:
            merged[name] = {**meta, "group": "Unclassified", "extract": False}
            new_count += 1

    COLLECTIONS_FILE.parent.mkdir(exist_ok=True)
    COLLECTIONS_FILE.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info("updated %s: %d total, %d new", COLLECTIONS_FILE, len(merged), new_count)
    if new_count:
        print(f"\n{new_count} new collection(s) added with defaults.")
        print(f"Edit {COLLECTIONS_FILE} to set group and extract for new entries.")


def main(headed: bool = False, update: bool = False) -> None:
    config = load_config()

    with sync_playwright() as pw:
        browser, context = ensure_authenticated(pw, headless=not headed)
        try:
            discovered = _discover(context, config.ig_username)
        finally:
            browser.close()

    print(f"\n{len(discovered)} collections found:\n")
    for i, (name, meta) in enumerate(discovered.items(), 1):
        print(f"  {i:2}. {name}  (slug={meta['slug']})")

    if update:
        _merge_into_json(discovered)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Discover Instagram saved collections.")
    parser.add_argument("--headed", action="store_true", help="Visible browser window.")
    parser.add_argument(
        "--update",
        action="store_true",
        help="Merge discovered collections into config/collections.json (preserves existing annotations).",
    )
    args = parser.parse_args()
    main(headed=args.headed, update=args.update)
