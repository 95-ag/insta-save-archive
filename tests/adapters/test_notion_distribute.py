import types

import pytest

from insta_save.adapters import notion


class _Env:
    notion_token = "t"
    notion_database_id = "db"
    notion_knowledge_database_id = "kdb"


def _patch(monkeypatch, client):
    monkeypatch.setattr(notion, "validate_notion", lambda env: None)
    monkeypatch.setattr(notion, "_get_data_source_id", lambda c, db: f"ds-{db}")
    monkeypatch.setattr(notion, "Client", lambda auth=None: client)


def _titled(pid, name):
    return {"id": pid, "properties": {"Name": {"type": "title", "title": [{"plain_text": name}]}}}


def test_get_knowledge_page_resolves_a_name(monkeypatch):
    pages = [_titled("p1", "Page A"), _titled("p2", "Page B")]
    _patch(
        monkeypatch,
        types.SimpleNamespace(
            data_sources=types.SimpleNamespace(
                query=lambda ds, **kw: {"results": pages, "has_more": False}
            )
        ),
    )
    assert notion.get_knowledge_page(_Env(), "Page B") == "p2"


def test_get_knowledge_page_fails_loud_when_absent(monkeypatch):
    _patch(
        monkeypatch,
        types.SimpleNamespace(
            data_sources=types.SimpleNamespace(
                query=lambda ds, **kw: {"results": [_titled("p1", "Other")], "has_more": False}
            )
        ),
    )
    with pytest.raises(RuntimeError, match="found 0"):
        notion.get_knowledge_page(_Env(), "Page B")


def test_get_knowledge_page_fails_loud_when_ambiguous(monkeypatch):
    dupes = [_titled("p1", "Page B"), _titled("p2", "Page B")]
    _patch(
        monkeypatch,
        types.SimpleNamespace(
            data_sources=types.SimpleNamespace(
                query=lambda ds, **kw: {"results": dupes, "has_more": False}
            )
        ),
    )
    with pytest.raises(RuntimeError, match="found 2"):
        notion.get_knowledge_page(_Env(), "Page B")


def test_get_page_blocks_paginates(monkeypatch):
    def _list(**kw):
        if kw.get("start_cursor") is None:
            return {"results": [{"id": "b1"}], "has_more": True, "next_cursor": "c1"}
        return {"results": [{"id": "b2"}], "has_more": False}

    _patch(
        monkeypatch,
        types.SimpleNamespace(
            blocks=types.SimpleNamespace(children=types.SimpleNamespace(list=_list))
        ),
    )
    assert [b["id"] for b in notion.get_page_blocks(_Env(), "page")] == ["b1", "b2"]


def _routed_page():
    return {
        "id": "pg",
        "properties": {
            "source_id": {"rich_text": [{"plain_text": "ABC"}]},
            "title": {"title": [{"plain_text": "Hip drills"}]},
            "summary": {"rich_text": [{"plain_text": "does a thing"}]},
            "tags": {"multi_select": [{"name": "fitness-workouts"}]},
            "ig_link": {"url": "https://ig/x"},
            "Knowledge Base": {"relation": [{"id": "kp"}]},
        },
    }


def test_query_routed_for_target_maps_the_fields(monkeypatch):
    _patch(
        monkeypatch,
        types.SimpleNamespace(
            data_sources=types.SimpleNamespace(
                query=lambda ds, **kw: {"results": [_routed_page()], "has_more": False}
            )
        ),
    )
    [row] = notion.query_routed_for_target(_Env(), "Page A")
    assert row == {
        "page_id": "pg",
        "source_id": "ABC",
        "title": "Hip drills",
        "summary": "does a thing",
        "tags": ["fitness-workouts"],
        "ig_link": "https://ig/x",
        "knowledge_base": ["kp"],
    }


def test_query_routed_for_target_filters_on_status_and_target(monkeypatch):
    seen = {}

    def _query(ds, **kw):
        seen.update(kw)
        return {"results": [], "has_more": False}

    _patch(monkeypatch, types.SimpleNamespace(data_sources=types.SimpleNamespace(query=_query)))
    notion.query_routed_for_target(_Env(), "Page B")
    assert seen["filter"] == {
        "and": [
            {"property": "status", "select": {"equals": "Routed"}},
            {"property": "route_target", "select": {"equals": "Page B"}},
        ]
    }


def test_query_routed_for_target_returns_none_for_missing_text(monkeypatch):
    page = {"id": "pg", "properties": {}}
    _patch(
        monkeypatch,
        types.SimpleNamespace(
            data_sources=types.SimpleNamespace(
                query=lambda ds, **kw: {"results": [page], "has_more": False}
            )
        ),
    )
    [row] = notion.query_routed_for_target(_Env(), "X")
    assert row["source_id"] is None and row["summary"] is None and row["ig_link"] is None
