"""
Collection discovery — Stage 0 of the ingest sync.

Crawls the /saved/ index to find which collections exist, and additively merges
them into config/collections.json (slug + numeric_id). New collections are added;
existing annotations (group, extract, enrichment_order) are preserved.

Discovery follows the same presence/absence safety principle as the rest of the sync:
  • A collection SEEN in the index → safe to add to the known set.
  • A known collection NOT seen   → NOT proof it was deleted (the index lazy-loads
    unreliably). It is flagged as 'missing', never auto-removed. Whole-collection
    removal is a separate, explicitly-confirmed action.

discovery_complete is True only if the index crawl reached a stable bottom AND the
discovered set covers every previously-known collection — the signal callers use to
decide whether 'missing' collections are trustworthy enough to act on.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import BrowserContext

from pipeline.config import Config
from pipeline.crawler import ALL_POSTS_SLUG, INSTAGRAM_BASE, scroll_harvest

# Collection links on the /saved/ index page
COLLECTION_LINK_SELECTOR = "a[href*='/saved/']"

# /{username}/saved/{slug}/{numeric_id}/  — slug is non-numeric, id is digits
_COLLECTION_HREF_RE = re.compile(r"/saved/([^/]+)/(\d+)")

log = logging.getLogger(__name__)

_COLLECTIONS_FILE = Path(__file__).parent.parent / "config" / "collections.json"


@dataclass
class DiscoveryResult:
    discovered: dict = field(default_factory=dict)   # name -> {slug, numeric_id}
    new_names: list = field(default_factory=list)     # names added to collections.json
    missing_names: list = field(default_factory=list) # known but not seen this crawl
    complete: bool = False                             # index crawl reached stable bottom


def _load_collections() -> dict:
    if _COLLECTIONS_FILE.exists():
        return json.loads(_COLLECTIONS_FILE.read_text(encoding="utf-8"))
    return {}


def _merge_additive(discovered: dict) -> list:
    """
    Merge discovered collections into collections.json. Refresh slug/numeric_id;
    preserve existing annotations (group, extract, enrichment_order). Never delete.
    Returns the list of newly-added collection names.
    """
    existing = _load_collections()
    new_names = []
    for name, meta in discovered.items():
        if name in existing:
            existing[name]["slug"] = meta["slug"]
            existing[name]["numeric_id"] = meta["numeric_id"]
        else:
            existing[name] = {"slug": meta["slug"], "numeric_id": meta["numeric_id"]}
            new_names.append(name)
    _COLLECTIONS_FILE.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return new_names


def discover_collections(context: BrowserContext, config: Config) -> DiscoveryResult:
    """
    Crawl the /saved/ index, merge findings into collections.json, and report
    discovered / new / missing collections plus a completeness flag.
    """
    known_before = set(_load_collections().keys())

    saved_index = f"{INSTAGRAM_BASE}/{config.ig_username}/saved/"
    page = context.new_page()
    try:
        log.info("discovery: navigating to saved index %s", saved_index)
        page.goto(saved_index, wait_until="domcontentloaded", timeout=20_000)

        if "accounts/login" in page.url:
            log.warning("discovery: redirected to login — session expired; INCOMPLETE")
            return DiscoveryResult(complete=False)

        def extract(link):
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
            # key by slug (stable); value carries name + ids
            return (slug, {"name": name, "slug": slug, "numeric_id": numeric_id})

        items, complete = scroll_harvest(page, COLLECTION_LINK_SELECTOR, extract)
    finally:
        page.close()

    discovered = {v["name"]: {"slug": v["slug"], "numeric_id": v["numeric_id"]}
                  for v in items.values()}

    new_names = _merge_additive(discovered)
    discovered_names = set(discovered.keys())
    missing_names = sorted(known_before - discovered_names)

    # Complete only if the crawl ended naturally AND covers all previously-known.
    discovery_complete = complete and not missing_names

    log.info(
        "discovery: found %d collections (%d new, %d missing); crawl_complete=%s "
        "discovery_complete=%s",
        len(discovered), len(new_names), len(missing_names), complete, discovery_complete,
    )
    if missing_names:
        log.warning("discovery: known collections not seen this crawl: %s", missing_names)

    return DiscoveryResult(
        discovered=discovered,
        new_names=new_names,
        missing_names=missing_names,
        complete=discovery_complete,
    )
