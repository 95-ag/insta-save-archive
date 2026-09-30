"""Optional routing map: primary-topic rule > tag > collection > group -> route_target.
Deterministic, no model. Missing/absent file => routing disabled (route_for always None)."""

import json
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_ROUTES = Path("config") / "routes.json"


@dataclass(frozen=True)
class Routes:
    # by_tag is an ORDERED list of (tag, target): list order is the priority. When an item carries
    # several routing tags, the highest-listed one wins — independent of the item's own tag order.
    by_tag: list[tuple[str, str]] = field(default_factory=list)
    # Topics that route ONLY when they are the item's primary topic. A rule here says "an item
    # ABOUT this is for that page" without also claiming "any item merely carrying this tag
    # belongs there" — the two differ sharply for broad cross-group tags. Measured: 1 item has
    # `legal` as its primary topic while 9 carry the tag, and 1 has `ecommerce` while 85 carry it,
    # so putting either in by_tag would sweep in items about something else entirely.
    by_primary_topic: dict[str, str] = field(default_factory=dict)
    by_collection: dict[str, str] = field(default_factory=dict)
    by_group: dict[str, str] = field(default_factory=dict)

    def target_for_primary_topic(self, primary_topic: str | None) -> str | None:
        """The target for an item's primary topic, or None when it has no primary topic or the
        topic carries no rule. by_primary_topic wins over by_tag, so a topic can route as a
        primary signal without joining tag-order priority. Callers re-use it to report HOW a
        route was decided; route_for applies it as the first precedence step."""
        if not primary_topic:
            return None
        if primary_topic in self.by_primary_topic:
            return self.by_primary_topic[primary_topic]
        for tag, target in self.by_tag:
            if tag == primary_topic:
                return target
        return None

    def route_for(self, tags: list[str], collections: list[str], groups: list[str],
                  *, primary_topic: str | None = None) -> str | None:
        # A primary topic with a rule outranks every tag the item also carries: a multi-tag
        # item's tag combination is shared with items about something else, so static by_tag
        # order alone sends that whole population to one page (ARCHITECTURE §7).
        primary_target = self.target_for_primary_topic(primary_topic)
        if primary_target is not None:
            return primary_target
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
        by_primary_topic=data.get("by_primary_topic", {}),
        by_collection=data.get("by_collection", {}),
        by_group=data.get("by_group", {}),
    )
