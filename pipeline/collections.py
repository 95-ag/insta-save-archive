"""
Collection registry — loads from config/collections.json (gitignored).

Groups define ingestion priority order. The JSON file is never committed —
it is generated and annotated locally via scripts/list_collections.py --update.

To bootstrap: python scripts/list_collections.py --update
Then edit config/collections.json to set group and extract for each entry.
"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CollectionEntry:
    name: str        # Exact display name matching Instagram's collection label
    slug: str        # URL slug from /saved/{slug}/{numeric_id}/
    numeric_id: str  # Instagram numeric collection ID
    group: str       # Group name (must be in GROUP_PRIORITY)
    extract: bool    # True = include in Phase 3 pilot extraction


# Ingestion runs in this group order — edit to reprioritise.
GROUP_PRIORITY = [
    "Hustling",
    "Content",
    "Creative",
    "Biz",
    "Biz - Clothing",
    "Lifestyle",
]

_COLLECTIONS_FILE = Path(__file__).parent.parent / "config" / "collections.json"


def _load() -> list[CollectionEntry]:
    """Load collections from config/collections.json at import time."""
    if not _COLLECTIONS_FILE.exists():
        raise RuntimeError(
            f"Collections file not found: {_COLLECTIONS_FILE}\n"
            "Bootstrap it: python scripts/list_collections.py --update\n"
            "Then edit config/collections.json to set group and extract for each entry."
        )
    data = json.loads(_COLLECTIONS_FILE.read_text(encoding="utf-8"))
    entries = []
    for name, meta in data.items():
        group = meta.get("group", "Unclassified")
        entries.append(CollectionEntry(
            name=name,
            slug=meta.get("slug", ""),
            numeric_id=meta.get("numeric_id", ""),
            group=group,
            extract=bool(meta.get("extract", False)),
        ))
    return entries


COLLECTIONS: list[CollectionEntry] = _load()

# Validate all groups are known at import time.
_unknown = {e.group for e in COLLECTIONS} - set(GROUP_PRIORITY) - {"Unclassified"}
if _unknown:
    raise ValueError(f"collections: unknown groups: {_unknown}")


def ordered_for_ingestion() -> list[CollectionEntry]:
    """Return all collections sorted by group priority, preserving intra-group order."""
    priority = {g: i for i, g in enumerate(GROUP_PRIORITY)}
    return sorted(
        COLLECTIONS,
        key=lambda e: (priority.get(e.group, 999), COLLECTIONS.index(e)),
    )


def pilot_collections() -> list[CollectionEntry]:
    """Return collections with extract=True, in ingestion order."""
    return [e for e in ordered_for_ingestion() if e.extract]


def pilot_collections_by_enrichment_priority() -> list[CollectionEntry]:
    """
    Return collections that have enrichment_order set in config/collections.json,
    sorted ascending by that value. Collections without enrichment_order are excluded.

    enrichment_order is set manually in config/collections.json:
      "enrichment_order": 1   ← first priority
      "enrichment_order": 2   ← second priority
      (absent or null)        ← not in Claude enrichment scope
    """
    data = json.loads(_COLLECTIONS_FILE.read_text(encoding="utf-8"))
    ordered = [
        (meta["enrichment_order"], entry)
        for entry in COLLECTIONS
        if (meta := data.get(entry.name, {})).get("enrichment_order") is not None
    ]
    return [entry for _, entry in sorted(ordered, key=lambda x: x[0])]


def classify_new_collection(name: str) -> str:
    """
    Interactive prompt to assign a new collection to a group.
    Returns the chosen group name.
    """
    print(f"\nNew collection detected: {name!r}")
    print("Available groups:")
    for i, g in enumerate(GROUP_PRIORITY, 1):
        print(f"  {i}. {g}")
    while True:
        raw = input("Assign to group (enter number): ").strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(GROUP_PRIORITY):
                return GROUP_PRIORITY[idx]
        except ValueError:
            pass
        print("  Invalid — enter a number from the list.")
