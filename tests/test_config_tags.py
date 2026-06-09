import json
import pytest
from insta_save.config import tags as tagcfg

FIXTURE = {
    "content_type": {"tool": "a thing to use", "explainer": "how it works"},
    "groups": {"Hustling": {"seo": "search ranking", "web-dev": "coding sites"}},
    "cross_group": {"ai": "applied ai", "design": "visual design"},
}


def _load(tmp_path):
    p = tmp_path / "tags.json"
    p.write_text(json.dumps(FIXTURE), encoding="utf-8")
    return tagcfg.load_vocab(p)


def test_axes(tmp_path):
    v = _load(tmp_path)
    assert v.content_types == ["tool", "explainer"]
    assert v.cross_group_topics == ["ai", "design"]
    assert v.group_topics("Hustling") == ["seo", "web-dev"]


def test_allowed_topics_group_plus_cross(tmp_path):
    v = _load(tmp_path)
    assert tagcfg.allowed_topics(v, "Hustling") == ["seo", "web-dev", "ai", "design"]


def test_definitions_span_all_axes(tmp_path):
    v = _load(tmp_path)
    assert v.definitions["seo"] == "search ranking"
    assert v.definitions["tool"] == "a thing to use"


def test_unknown_group_raises(tmp_path):
    v = _load(tmp_path)
    with pytest.raises(KeyError):
        tagcfg.allowed_topics(v, "Nope")
