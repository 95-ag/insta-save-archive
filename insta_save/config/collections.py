"""Collections config (v2 nested shape): ordered groups + per-collection {group, extract}.

  {
    "groups": ["Hustling", "Biz", ..., "uncategorized"],   # processing order
    "collections": { "<name>": {"group": "...", "extract": bool} }
  }

Group ordering is group-level (you order ~6 groups, not every folder). Group names
live here (private file), never in committed code. Unknown collections fall into
the default last group `uncategorized`.
"""

import json
from dataclasses import dataclass
from pathlib import Path

UNCATEGORIZED = "uncategorized"
_DEFAULT_COLLECTIONS = Path("config") / "collections.json"


@dataclass(frozen=True)
class CollectionsConfig:
    groups: tuple[str, ...]            # ordered
    collections: dict[str, dict]       # name -> {"group": str, "extract": bool}

    def group_of(self, name: str) -> str:
        entry = self.collections.get(name)
        return entry["group"] if entry else UNCATEGORIZED

    def collections_in_group(self, group: str) -> set[str]:
        return {n for n, e in self.collections.items() if e["group"] == group}

    def extract_collections_in_group(self, group: str) -> set[str]:
        """The extract=yes collections in a group — the only ones whose items enrich
        under this group, so the only meaningful vocab guidance for calibrate. A det
        collection's items either go the deterministic branch or (when cross-tagged into
        another group's extract=yes collection) enrich under THAT group, never here."""
        return {n for n, e in self.collections.items() if e["group"] == group and e.get("extract")}

    def group_order_index(self, group: str) -> int:
        return self.groups.index(group) if group in self.groups else len(self.groups)

    def is_extract_path(self, names: list[str]) -> bool:
        """Extract path if ANY of the item's collections is extract=yes."""
        return any(self.collections.get(n, {}).get("extract") for n in names)

    def extract_groups_of(self, names: list[str]) -> list[str]:
        """Distinct groups (in `groups` order) that contain at least one of the item's
        extract=yes collections. Empty list when no extract=yes collection exists
        (the item goes to the deterministic branch)."""
        return [
            g for g in self.groups
            if any(self.collections.get(n, {}).get("extract") and self.collections[n]["group"] == g
                   for n in names)
        ]

    def enrich_group(self, names: list[str]) -> str | None:
        """
        The group an item is enriched under = the LAST group (in `groups` order)
        that contains at least one of the item's extract=yes collections. None when
        the item has no extract=yes collection (it goes to the deterministic branch).
        """
        groups = self.extract_groups_of(names)
        return groups[-1] if groups else None


def load_collections(path=_DEFAULT_COLLECTIONS) -> CollectionsConfig:
    p = Path(path)
    if not p.exists():
        raise RuntimeError(
            f"Collections config not found: {p}\nBuild it with `isa discover` (see README.md)."
        )
    data = json.loads(p.read_text(encoding="utf-8"))
    if "collections" not in data:
        # The v1 flat shape is top-level {name: {slug, group, ...}} with no
        # "collections" key. Without this guard the loader would silently treat
        # the file as empty (every collection -> uncategorized).
        looks_legacy = any(isinstance(v, dict) and ("slug" in v or "group" in v)
                           for v in data.values())
        hint = (' it looks like the legacy v1 flat format — migrate it to the v2 nested '
                'shape {"groups": [...], "collections": {<name>: {"group", "extract"}}}.'
                if looks_legacy else "")
        raise RuntimeError(f"Collections config {p} is missing the 'collections' key;{hint}")
    groups = tuple(data.get("groups", []))
    if UNCATEGORIZED not in groups:
        groups = groups + (UNCATEGORIZED,)
    collections = {
        name: {
            "group": meta.get("group", UNCATEGORIZED),
            "extract": bool(meta.get("extract", False)),
            "slug": meta.get("slug"),
            "numeric_id": meta.get("numeric_id"),
        }
        for name, meta in data.get("collections", {}).items()
    }
    return CollectionsConfig(groups=groups, collections=collections)


def merge_discovered(existing: dict, discovered: dict) -> tuple[dict, list, list]:
    """Additively merge {name: {slug, numeric_id}} into a collections.json dict.

    Pure: existing annotations (group/extract) are preserved; new collections get
    defaults (group=uncategorized, extract=False). Returns (merged, new_names,
    missing_names). missing = known but not in this crawl — never dropped (a single
    crawl can be incomplete; absence is not authoritative, mirrors reconcile).
    """
    groups = list(existing.get("groups", []))
    if UNCATEGORIZED not in groups:
        groups.append(UNCATEGORIZED)
    cols = dict(existing.get("collections", {}))
    new_names = []
    for name, ids in discovered.items():
        if name in cols:
            updates = {k: v for k, v in
                       {"slug": ids.get("slug"), "numeric_id": ids.get("numeric_id")}.items()
                       if v is not None}
            cols[name] = {**cols[name], **updates}
        else:
            cols[name] = {"group": UNCATEGORIZED, "extract": False,
                          "slug": ids.get("slug"), "numeric_id": ids.get("numeric_id")}
            new_names.append(name)
    new_names = sorted(new_names)
    missing_names = sorted(set(existing.get("collections", {})) - set(discovered))
    return {"groups": groups, "collections": cols}, new_names, missing_names


def write_collections(data: dict, path=_DEFAULT_COLLECTIONS) -> None:
    """Persist a collections.json dict (pretty, UTF-8, non-ASCII preserved)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
