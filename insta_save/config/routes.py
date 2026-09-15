"""Optional routing map: tag > collection > group -> route_target. Deterministic,
no model. Missing/absent file => routing disabled (route_for always None)."""

import json
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_ROUTES = Path("config") / "routes.json"


@dataclass(frozen=True)
class Routes:
    # by_tag is an ORDERED list of (tag, target): list order is the priority. When an item carries
    # several routing tags, the highest-listed one wins — independent of the item's own tag order.
    by_tag: list[tuple[str, str]] = field(default_factory=list)
    by_collection: dict[str, str] = field(default_factory=dict)
    by_group: dict[str, str] = field(default_factory=dict)

    def route_for(self, tags: list[str], collections: list[str], groups: list[str]) -> str | None:
        for tag, target in self.by_tag:      # config order = priority, not the item's tag order
            if tag in tags:
                return target
        for c in collections:
            if c in self.by_collection:
                return self.by_collection[c]
        for g in groups:
            if g in self.by_group:
                return self.by_group[g]
        return None


def load_routes(path=_DEFAULT_ROUTES) -> Routes:
    p = Path(path)
    if not p.exists():
        return Routes()  # routing disabled
    data = json.loads(p.read_text(encoding="utf-8"))
    by_tag = [tuple(pair) for pair in data.get("by_tag", [])]
    seen = set()
    for tag, _target in by_tag:
        if tag in seen:
            raise ValueError(f"routes.json: duplicate by_tag tag {tag!r}")
        seen.add(tag)
    return Routes(
        by_tag=by_tag,
        by_collection=data.get("by_collection", {}),
        by_group=data.get("by_group", {}),
    )
