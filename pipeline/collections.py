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
