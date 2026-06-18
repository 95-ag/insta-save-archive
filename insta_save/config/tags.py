"""Tag vocabulary: content-type axis + per-group topics + cross-group topics, with
one-line definitions injected into the enrich prompt. Private file (gitignored)."""

import json
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_TAGS = Path("config") / "tags.json"


@dataclass(frozen=True)
class Vocab:
    content_types: list[str]
    cross_group_topics: list[str]
    _group_topics: dict[str, list[str]]
    definitions: dict[str, str]

    def has_group(self, group: str) -> bool:
        """Non-raising calibrated-check. group_topics() raises for uncalibrated groups;
        the sequencer must TEST, not catch."""
        return group in self._group_topics

    def group_topics(self, group: str) -> list[str]:
        if group not in self._group_topics:
            raise KeyError(f"tags: no topics for group {group!r}")
        return self._group_topics[group]


def load_vocab(path=_DEFAULT_TAGS) -> Vocab:
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"Tag vocabulary not found: {p} (see docs/OPERATING.md).")
    data = json.loads(p.read_text(encoding="utf-8"))
    content = data["content_type"]
    cross = data["cross_group"]
    groups = {g: list(t.keys()) for g, t in data["groups"].items()}

    definitions: dict[str, str] = {}
    definitions.update(content)
    definitions.update(cross)
    for t in data["groups"].values():
        definitions.update(t)

    return Vocab(
        content_types=list(content.keys()),
        cross_group_topics=list(cross.keys()),
        _group_topics=groups,
        definitions=definitions,
    )


def lock_vocab(group: str, proposed: dict, path=_DEFAULT_TAGS) -> None:
    """Merge a proposed vocab (calibrate shape: content_type/groups/cross_group) into
    config/tags.json. Sets the group's topics outright; for content_type/cross_group adds
    only NEW keys (existing definitions are preserved). Other groups are untouched."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("content_type", {})
    data.setdefault("groups", {})
    data.setdefault("cross_group", {})
    data["groups"][group] = dict(proposed.get("groups", {}).get(group, {}))
    for key, definition in proposed.get("content_type", {}).items():
        data["content_type"].setdefault(key, definition)
    for key, definition in proposed.get("cross_group", {}).items():
        data["cross_group"].setdefault(key, definition)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def allowed_topics(vocab: Vocab, group: str) -> list[str]:
    """Topic enum for a group: its granular topics first, then cross-group."""
    return vocab.group_topics(group) + vocab.cross_group_topics


def union_topics(vocab: Vocab, groups: list[str]) -> list[str]:
    """Union of granular topics across all groups (in groups order, deduped, first-occurrence
    wins), followed by cross_group_topics. Raises KeyError/RuntimeError for uncalibrated groups —
    §7.3 guarantees all enrich groups are locked by enrich time; failing loud is correct.

    Single-group equivalence: union_topics(vocab, [G]) == allowed_topics(vocab, G).
    """
    seen: set[str] = set()
    result: list[str] = []
    for g in groups:
        if g not in vocab._group_topics:
            raise KeyError(f"tags: no topics for group {g!r} (uncalibrated — lock vocab before enrich)")
        for t in vocab.group_topics(g):
            if t not in seen:
                seen.add(t)
                result.append(t)
    for t in vocab.cross_group_topics:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result
