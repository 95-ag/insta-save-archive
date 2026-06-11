from insta_save.stages import select
from insta_save.config.collections import CollectionsConfig


def test_select_item_branches_on_extract_path(monkeypatch):
    cfg = CollectionsConfig(groups=("uncategorized",), collections={
        "Dev": {"group": "uncategorized", "extract": True, "slug": "d", "numeric_id": "1"},
        "Art": {"group": "uncategorized", "extract": False, "slug": "a", "numeric_id": "2"}})
    queued = []
    monkeypatch.setattr(select.notion, "mark_queued", lambda env, pid: queued.append(pid))

    assert select._select_item(None, cfg, {"page_id": "p1", "collections": ["Dev"]}) == "queued"
    assert select._select_item(None, cfg, {"page_id": "p2", "collections": ["Art"]}) == "deterministic_pending"
    assert queued == ["p1"]
