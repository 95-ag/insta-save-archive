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
