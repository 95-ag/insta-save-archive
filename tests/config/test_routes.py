import json
import pytest
from insta_save.config import routes as routecfg

FIXTURE = {
    "by_tag": [["recipe", "RecipeDB"], ["web-dev", "WebDB"], ["ui-ux", "UiDB"]],
    "by_collection": {"Coding": "LearningDB"},
    "by_group": {"Biz": "MarketDB"},
}


def _load(tmp_path):
    p = tmp_path / "routes.json"
    p.write_text(json.dumps(FIXTURE), encoding="utf-8")
    return routecfg.load_routes(p)


def test_tag_wins_over_collection_and_group(tmp_path):
    r = _load(tmp_path)
    assert r.route_for(tags=["recipe"], collections=["Coding"], groups=["Biz"]) == "RecipeDB"


def test_collection_wins_over_group(tmp_path):
    r = _load(tmp_path)
    assert r.route_for(tags=[], collections=["Coding"], groups=["Biz"]) == "LearningDB"


def test_group_fallback(tmp_path):
    r = _load(tmp_path)
    assert r.route_for(tags=[], collections=["Unmapped"], groups=["Biz"]) == "MarketDB"


def test_no_match_returns_none(tmp_path):
    r = _load(tmp_path)
    assert r.route_for(tags=[], collections=["Unmapped"], groups=["Unmapped"]) is None


def test_missing_file_is_disabled(tmp_path):
    r = routecfg.load_routes(tmp_path / "nope.json")
    assert r.route_for(tags=["recipe"], collections=["Coding"], groups=["Biz"]) is None


def test_by_tag_priority_is_config_order_not_item_order(tmp_path):
    # web-dev is listed BEFORE ui-ux → it wins regardless of the ORDER tags sit on the item.
    r = _load(tmp_path)
    assert r.route_for(tags=["ui-ux", "web-dev"], collections=[], groups=[]) == "WebDB"
    assert r.route_for(tags=["web-dev", "ui-ux"], collections=[], groups=[]) == "WebDB"


def test_load_routes_rejects_duplicate_tag(tmp_path):
    p = tmp_path / "routes.json"
    p.write_text(json.dumps({"by_tag": [["x", "A"], ["x", "B"]]}), encoding="utf-8")
    with pytest.raises(ValueError):
        routecfg.load_routes(p)


# ---------------------------------------------------------------------------
# P4: the item's primary topic outranks plain by_tag order
# ---------------------------------------------------------------------------

def test_primary_topic_with_a_rule_beats_a_higher_ranked_tag(tmp_path):
    # web-dev is listed BEFORE ui-ux, but the item is mainly about ui-ux.
    r = _load(tmp_path)
    assert r.route_for(tags=["web-dev", "ui-ux"], collections=[], groups=[],
                       primary_topic="ui-ux") == "UiDB"


def test_primary_topic_beats_collection_and_group_too(tmp_path):
    r = _load(tmp_path)
    assert r.route_for(tags=["web-dev"], collections=["Coding"], groups=["Biz"],
                       primary_topic="ui-ux") == "UiDB"


def test_primary_topic_without_a_rule_falls_back_to_tag_order(tmp_path):
    r = _load(tmp_path)
    assert r.route_for(tags=["web-dev", "ui-ux"], collections=[], groups=[],
                       primary_topic="unmapped-topic") == "WebDB"


def test_primary_topic_none_falls_back_to_tag_order(tmp_path):
    r = _load(tmp_path)
    assert r.route_for(tags=["web-dev", "ui-ux"], collections=[], groups=[],
                       primary_topic=None) == "WebDB"


def test_primary_topic_defaults_to_none_for_existing_callers(tmp_path):
    r = _load(tmp_path)
    assert r.route_for(tags=["web-dev", "ui-ux"], collections=[], groups=[]) == "WebDB"


def test_primary_topic_empty_string_falls_back(tmp_path):
    r = _load(tmp_path)
    assert r.route_for(tags=["web-dev"], collections=[], groups=[], primary_topic="") == "WebDB"


def test_primary_topic_without_tags_still_routes(tmp_path):
    """The primary topic is a routing key in its own right — it does not have to be
    repeated in `tags` (a Notion write could lag, or the tag list could be clamped)."""
    r = _load(tmp_path)
    assert r.route_for(tags=[], collections=[], groups=[], primary_topic="ui-ux") == "UiDB"


def test_primary_topic_falls_through_to_collection_when_unmapped(tmp_path):
    r = _load(tmp_path)
    assert r.route_for(tags=[], collections=["Coding"], groups=["Biz"],
                       primary_topic="unmapped-topic") == "LearningDB"


def test_target_for_primary_topic_exposes_the_primary_decision(tmp_path):
    """The by_tag lookup the stage re-uses to report HOW a route was decided — it says
    "primary" even when plain tag order would have landed on the same target."""
    r = _load(tmp_path)
    assert r.target_for_primary_topic("web-dev") == "WebDB"   # also the tag-order winner
    assert r.target_for_primary_topic("ui-ux") == "UiDB"
    assert r.target_for_primary_topic("unmapped-topic") is None
    assert r.target_for_primary_topic(None) is None
    assert r.target_for_primary_topic("") is None


def test_missing_file_ignores_primary_topic(tmp_path):
    r = routecfg.load_routes(tmp_path / "nope.json")
    assert r.route_for(tags=["recipe"], collections=[], groups=[], primary_topic="recipe") is None
