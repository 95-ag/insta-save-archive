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


def test_has_group(tmp_path):
    v = _load(tmp_path)
    assert v.has_group("Hustling") is True
    assert v.has_group("Nope") is False
    # contrast: group_topics still raises for uncalibrated groups
    with pytest.raises(KeyError):
        v.group_topics("Nope")


# union_topics tests
FIXTURE_TWO_GROUPS = {
    "content_type": {"tool": "a thing to use"},
    "groups": {
        "Hustling": {"seo": "search ranking", "web-dev": "coding sites"},
        "Biz": {"sales": "selling", "marketing": "outreach"},
    },
    "cross_group": {"ai": "applied ai"},
}


def _load_two(tmp_path):
    p = tmp_path / "tags2.json"
    p.write_text(json.dumps(FIXTURE_TWO_GROUPS), encoding="utf-8")
    return tagcfg.load_vocab(p)


def test_union_topics_single_group_equals_allowed_topics(tmp_path):
    """Critical backward-compat: union_topics([G]) == allowed_topics(G)."""
    v = _load(tmp_path)
    assert tagcfg.union_topics(v, ["Hustling"]) == tagcfg.allowed_topics(v, "Hustling")


def test_union_topics_two_groups_union_deduped(tmp_path):
    v = _load_two(tmp_path)
    result = tagcfg.union_topics(v, ["Hustling", "Biz"])
    # Hustling granular first, then Biz granular, then cross_group; no dupes
    assert result == ["seo", "web-dev", "sales", "marketing", "ai"]


def test_union_topics_cross_group_not_duplicated(tmp_path):
    """cross_group topics must appear exactly once even when both groups share them."""
    v = _load_two(tmp_path)
    result = tagcfg.union_topics(v, ["Hustling", "Biz"])
    assert result.count("ai") == 1


def test_union_topics_uncalibrated_group_raises(tmp_path):
    v = _load(tmp_path)
    with pytest.raises((KeyError, RuntimeError)):
        tagcfg.union_topics(v, ["Nope"])


def test_lock_vocab_adds_group_without_clobbering(tmp_path):
    p = tmp_path / "tags.json"
    p.write_text(json.dumps({
        "content_type": {"tool": "an app"},
        "groups": {"Existing": {"old-topic": "kept"}},
        "cross_group": {"ai": "ai themes"},
    }), encoding="utf-8")
    proposed = {
        "content_type": {"tool": "an app", "explainer": "explains a concept"},
        "groups": {"NewGroup": {"web-dev": "building sites"}},
        "cross_group": {"ai": "SHOULD-NOT-OVERWRITE", "sustainability": "eco themes"},
    }
    tagcfg.lock_vocab("NewGroup", proposed, path=p)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["groups"]["NewGroup"] == {"web-dev": "building sites"}
    assert data["groups"]["Existing"] == {"old-topic": "kept"}          # untouched
    assert data["content_type"]["explainer"] == "explains a concept"    # added
    assert data["cross_group"]["sustainability"] == "eco themes"        # added
    assert data["cross_group"]["ai"] == "ai themes"                     # existing key NOT overwritten
    # loadable into a Vocab with the new group calibrated
    v = tagcfg.load_vocab(path=p)
    assert v.has_group("NewGroup") and "web-dev" in v.group_topics("NewGroup")


def test_merge_vocab_is_pure_and_additive():
    current = {
        "content_type": {"tool": "an app"},
        "groups": {"Existing": {"old-topic": "kept"}},
        "cross_group": {"ai": "ai themes"},
    }
    proposed = {
        "content_type": {"tool": "SHOULD-NOT-OVERWRITE", "explainer": "explains"},
        "groups": {"NewGroup": {"web-dev": "building sites"}},
        "cross_group": {"ai": "SHOULD-NOT-OVERWRITE", "sustainability": "eco"},
    }
    merged = tagcfg.merge_vocab(current, "NewGroup", proposed)
    assert merged["groups"]["NewGroup"] == {"web-dev": "building sites"}   # set outright
    assert merged["groups"]["Existing"] == {"old-topic": "kept"}           # untouched
    assert merged["content_type"]["explainer"] == "explains"               # added
    assert merged["content_type"]["tool"] == "an app"                      # existing key NOT overwritten
    assert merged["cross_group"]["sustainability"] == "eco"                # added
    assert merged["cross_group"]["ai"] == "ai themes"                      # existing NOT overwritten
    # purity: current is unchanged
    assert current["groups"] == {"Existing": {"old-topic": "kept"}}
    assert "explainer" not in current["content_type"]


def test_merge_vocab_group_set_outright_drops_rejected():
    """A topic absent from the proposal's group is GONE after merge (reject path)."""
    current = {"content_type": {}, "groups": {"G": {"a": "x", "b": "y"}}, "cross_group": {}}
    proposed = {"content_type": {}, "groups": {"G": {"a": "x"}}, "cross_group": {}}
    merged = tagcfg.merge_vocab(current, "G", proposed)
    assert merged["groups"]["G"] == {"a": "x"}   # 'b' dropped


def test_load_vocab_malformed_missing_key_raises_runtime_error(tmp_path):
    """A tags.json missing a required key raises RuntimeError with an actionable message."""
    p = tmp_path / "bad_tags.json"
    p.write_text('{"content_type": {}}', encoding="utf-8")  # missing 'groups' and 'cross_group'
    with pytest.raises(RuntimeError, match="malformed"):
        tagcfg.load_vocab(p)


def test_lock_vocab_still_matches_merge(tmp_path):
    """lock_vocab writes exactly what merge_vocab returns."""
    p = tmp_path / "tags.json"
    current = {"content_type": {"tool": "an app"}, "groups": {"E": {"o": "k"}}, "cross_group": {"ai": "t"}}
    p.write_text(json.dumps(current), encoding="utf-8")
    proposed = {"content_type": {}, "groups": {"N": {"w": "b"}}, "cross_group": {}}
    tagcfg.lock_vocab("N", proposed, path=p)
    assert json.loads(p.read_text(encoding="utf-8")) == tagcfg.merge_vocab(current, "N", proposed)


# load_vocab_or_empty + Vocab.empty tests

def test_vocab_empty_has_no_groups():
    from insta_save.config.tags import Vocab
    v = Vocab.empty()
    assert v.content_types == [] and v.cross_group_topics == []
    assert v.has_group("Hustle") is False


def test_load_vocab_or_empty_missing_returns_empty(tmp_path):
    from insta_save.config.tags import load_vocab_or_empty
    v = load_vocab_or_empty(path=str(tmp_path / "nope.json"))
    assert v.has_group("anything") is False
    assert v.content_types == []


def test_load_vocab_or_empty_present_valid_loads(tmp_path):
    import json
    from insta_save.config.tags import load_vocab_or_empty
    p = tmp_path / "tags.json"
    p.write_text(json.dumps({"content_type": {"tool": "x"}, "cross_group": {"ai": "y"},
                             "groups": {"Hustle": {"seo": "z"}}}))
    v = load_vocab_or_empty(path=str(p))
    assert v.has_group("Hustle") is True
    assert "tool" in v.content_types


def test_load_vocab_or_empty_present_malformed_raises(tmp_path):
    import json
    from insta_save.config.tags import load_vocab_or_empty
    p = tmp_path / "tags.json"
    p.write_text(json.dumps({"content_type": {"tool": "x"}}))  # missing groups/cross_group
    with pytest.raises(RuntimeError):
        load_vocab_or_empty(path=str(p))
