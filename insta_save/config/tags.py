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


def allowed_topics(vocab: Vocab, group: str) -> list[str]:
    """Topic enum for a group: its granular topics first, then cross-group."""
    return vocab.group_topics(group) + vocab.cross_group_topics
