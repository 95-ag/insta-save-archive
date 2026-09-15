import types

import httpx
import pytest
from notion_client.errors import RequestTimeoutError

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


class _Blocks:
    def __init__(self, append_error=None):
        self.deleted, self.appends, self.updates = [], [], []
        self.calls = []
        self.append_error = append_error
        self.children = types.SimpleNamespace(append=self._append, list=None)

    def delete(self, block_id):
        self.calls.append(("delete", block_id))
        self.deleted.append(block_id)

    def update(self, block_id, **props):
        self.updates.append((block_id, props))

    def _append(self, block_id, children, after=None):
        self.calls.append(("append", after))
        if self.append_error is not None:
            raise self.append_error
        self.appends.append((after, len(children)))
        return {"results": [{"id": f"new-{len(self.appends)}-{i}"} for i in range(len(children))]}


def _api_error():
    import httpx

    return notion.APIResponseError(
        code="internal_server_error",
        status=500,
        message="boom",
        headers=httpx.Headers(),
        raw_body_text="boom",
    )


def test_replace_section_blocks_inserts_then_deletes(monkeypatch):
    blocks = _Blocks()
    _patch(monkeypatch, types.SimpleNamespace(blocks=blocks))
    notion.replace_section_blocks(
        _Env(), "pg", after_block_id="h1", delete_block_ids=["b1", "b2"], blocks=[{"x": 1}]
    )
    assert blocks.deleted == ["b1", "b2"]
    assert blocks.appends == [("h1", 1)]


def test_replace_section_blocks_inserts_every_block_before_deleting(monkeypatch):
    blocks = _Blocks()
    _patch(monkeypatch, types.SimpleNamespace(blocks=blocks))
    notion.replace_section_blocks(
        _Env(),
        "pg",
        after_block_id="h1",
        delete_block_ids=["b1", "b2"],
        blocks=[{"x": i} for i in range(150)],
    )
    kinds = [kind for kind, _ in blocks.calls]
    assert kinds == ["append", "append", "delete", "delete"]


def test_replace_section_blocks_failed_insert_deletes_nothing(monkeypatch):
    blocks = _Blocks(append_error=_api_error())
    _patch(monkeypatch, types.SimpleNamespace(blocks=blocks))
    with pytest.raises(RuntimeError, match="failed to replace section on pg"):
        notion.replace_section_blocks(
            _Env(), "pg", after_block_id="h1", delete_block_ids=["b1"], blocks=[{"x": 1}]
        )
    assert [kind for kind, _ in blocks.calls] == ["append"]


def test_replace_section_blocks_chunks_at_100_and_reanchors(monkeypatch):
    blocks = _Blocks()
    _patch(monkeypatch, types.SimpleNamespace(blocks=blocks))
    last = notion.replace_section_blocks(
        _Env(),
        "pg",
        after_block_id="h1",
        delete_block_ids=[],
        blocks=[{"x": i} for i in range(250)],
    )
    assert [n for _, n in blocks.appends] == [100, 100, 50]
    assert [a for a, _ in blocks.appends] == ["h1", "new-1-99", "new-2-99"]
    assert last == "new-3-49"


def test_replace_section_blocks_with_no_blocks_returns_the_anchor(monkeypatch):
    blocks = _Blocks()
    _patch(monkeypatch, types.SimpleNamespace(blocks=blocks))
    assert (
        notion.replace_section_blocks(
            _Env(), "pg", after_block_id="h1", delete_block_ids=[], blocks=[]
        )
        == "h1"
    )
    assert blocks.appends == []


def test_set_knowledge_relation_is_idempotent(monkeypatch):
    updates = []
    pages = types.SimpleNamespace(
        retrieve=lambda page_id: {"properties": {"Knowledge Base": {"relation": [{"id": "kp"}]}}},
        update=lambda page_id, properties: updates.append(properties),
    )
    _patch(monkeypatch, types.SimpleNamespace(pages=pages))
    assert notion.set_knowledge_relation(_Env(), "pg", "kp") is False
    assert updates == []


def test_set_knowledge_relation_preserves_existing_links(monkeypatch):
    updates = []
    pages = types.SimpleNamespace(
        retrieve=lambda page_id: {"properties": {"Knowledge Base": {"relation": [{"id": "old"}]}}},
        update=lambda page_id, properties: updates.append(properties),
    )
    _patch(monkeypatch, types.SimpleNamespace(pages=pages))
    assert notion.set_knowledge_relation(_Env(), "pg", "kp") is True
    assert updates == [{"Knowledge Base": {"relation": [{"id": "old"}, {"id": "kp"}]}}]


def test_write_distributed_sets_status_and_version(monkeypatch):
    updates = []
    _patch(
        monkeypatch,
        types.SimpleNamespace(
            pages=types.SimpleNamespace(
                update=lambda page_id, properties: updates.append(properties)
            )
        ),
    )
    notion.write_distributed(_Env(), "pg", "claude-sonnet/v2.0-distribute")
    assert updates[0]["status"] == {"select": {"name": "Distributed"}}
    assert "distribute_notes" not in updates[0]


def test_write_distributed_records_a_skip_note(monkeypatch):
    updates = []
    _patch(
        monkeypatch,
        types.SimpleNamespace(
            pages=types.SimpleNamespace(
                update=lambda page_id, properties: updates.append(properties)
            )
        ),
    )
    notion.write_distributed(_Env(), "pg", "v", notes="already covered")
    assert updates[0]["distribute_notes"]["rich_text"][0]["text"]["content"] == "already covered"


def test_write_misrouted_sets_status_and_reason(monkeypatch):
    updates = []
    _patch(
        monkeypatch,
        types.SimpleNamespace(
            pages=types.SimpleNamespace(
                update=lambda page_id, properties: updates.append(properties)
            )
        ),
    )
    notion.write_misrouted(_Env(), "pg", "meal prep, not movement", "v")
    assert updates[0]["status"] == {"select": {"name": "Misrouted"}}
    assert (
        updates[0]["distribute_notes"]["rich_text"][0]["text"]["content"]
        == "meal prep, not movement"
    )


def test_write_distributed_wraps_a_request_timeout(monkeypatch):
    def _update(page_id, properties):
        raise RequestTimeoutError()

    _patch(monkeypatch, types.SimpleNamespace(pages=types.SimpleNamespace(update=_update)))
    with pytest.raises(RuntimeError, match="failed to mark pg Distributed"):
        notion.write_distributed(_Env(), "pg", "v")


def test_write_distributed_wraps_a_transport_error(monkeypatch):
    def _update(page_id, properties):
        raise httpx.ConnectError("boom")

    _patch(monkeypatch, types.SimpleNamespace(pages=types.SimpleNamespace(update=_update)))
    with pytest.raises(RuntimeError, match="failed to mark pg Distributed"):
        notion.write_distributed(_Env(), "pg", "v")


@pytest.mark.parametrize("reason", [None, ""])
def test_write_misrouted_omits_notes_without_a_reason(monkeypatch, reason):
    updates = []
    _patch(
        monkeypatch,
        types.SimpleNamespace(
            pages=types.SimpleNamespace(
                update=lambda page_id, properties: updates.append(properties)
            )
        ),
    )
    notion.write_misrouted(_Env(), "pg", reason, "v")
    assert "distribute_notes" not in updates[0]
    assert updates[0]["status"] == {"select": {"name": "Misrouted"}}
    assert updates[0]["distribute_version"]["rich_text"][0]["text"]["content"] == "v"


REBUILD_FILTER = {
    "or": [
        {"property": "Knowledge Base", "relation": {"contains": "kp"}},
        {
            "and": [
                {"property": "route_target", "select": {"equals": "Page A"}},
                {"property": "status", "select": {"equals": "Distributed"}},
            ]
        },
        {
            "and": [
                {"property": "route_target", "select": {"equals": "Page A"}},
                {"property": "status", "select": {"equals": "Misrouted"}},
            ]
        },
    ]
}


def _compound_filter_depth(node) -> int:
    """Recursively measure and/or nesting depth. Notion's API allows compound filters
    (and/or) to nest at most two levels deep — a leaf property filter contributes 0."""
    if not isinstance(node, dict):
        return 0
    for key in ("and", "or"):
        if key in node:
            return 1 + max((_compound_filter_depth(child) for child in node[key]), default=0)
    return 0


def test_query_items_for_rebuild_sends_the_scope_filter_and_paginates(monkeypatch):
    seen = []

    def _query(ds, **kw):
        seen.append((ds, kw))
        if kw.get("start_cursor") is None:
            return {"results": [_routed_page()], "has_more": True, "next_cursor": "c1"}
        return {"results": [{"id": "pg2", "properties": {}}], "has_more": False}

    _patch(monkeypatch, types.SimpleNamespace(data_sources=types.SimpleNamespace(query=_query)))
    rows = notion.query_items_for_rebuild(_Env(), "Page A", "kp")
    assert [ds for ds, _ in seen] == ["ds-db", "ds-db"]
    assert all(kw["filter"] == REBUILD_FILTER for _, kw in seen)
    assert seen[1][1]["start_cursor"] == "c1"
    assert [r["page_id"] for r in rows] == ["pg", "pg2"]


def test_query_items_for_rebuild_maps_the_fields(monkeypatch):
    page = _routed_page()
    page["properties"]["status"] = {"select": {"name": "Distributed"}}
    bare = {"id": "pg2", "properties": {}}
    _patch(
        monkeypatch,
        types.SimpleNamespace(
            data_sources=types.SimpleNamespace(
                query=lambda ds, **kw: {"results": [page, bare], "has_more": False}
            )
        ),
    )
    rows = notion.query_items_for_rebuild(_Env(), "Page A", "kp")
    assert rows == [
        {
            "page_id": "pg",
            "source_id": "ABC",
            "title": "Hip drills",
            "status": "Distributed",
            "knowledge_base": ["kp"],
        },
        {
            "page_id": "pg2",
            "source_id": None,
            "title": None,
            "status": None,
            "knowledge_base": [],
        },
    ]


def test_query_items_for_rebuild_filter_nests_at_most_two_levels(monkeypatch):
    seen = []

    def _query(ds, **kw):
        seen.append(kw)
        return {"results": [], "has_more": False}

    _patch(monkeypatch, types.SimpleNamespace(data_sources=types.SimpleNamespace(query=_query)))
    notion.query_items_for_rebuild(_Env(), "Page A", "kp")
    assert _compound_filter_depth(seen[0]["filter"]) <= 2


@pytest.mark.parametrize("error", [RequestTimeoutError(), httpx.ConnectError("boom"), _api_error()])
def test_query_items_for_rebuild_wraps_notion_errors(monkeypatch, error):
    def _query(ds, **kw):
        raise error

    _patch(monkeypatch, types.SimpleNamespace(data_sources=types.SimpleNamespace(query=_query)))
    with pytest.raises(RuntimeError, match="failed to query rebuild items"):
        notion.query_items_for_rebuild(_Env(), "Page A", "kp")


def _relation_pages(relation, updates, *, has_more=False):
    return types.SimpleNamespace(
        retrieve=lambda page_id: {
            "properties": {"Knowledge Base": {"relation": relation, "has_more": has_more}}
        },
        update=lambda page_id, properties: updates.append((page_id, properties)),
    )


def test_remove_knowledge_relation_removes_only_the_target(monkeypatch):
    updates = []
    relation = [{"id": "other"}, {"id": "kp"}, {"id": "third"}]
    _patch(monkeypatch, types.SimpleNamespace(pages=_relation_pages(relation, updates)))
    assert notion.remove_knowledge_relation(_Env(), "pg", "kp") is True
    assert updates == [("pg", {"Knowledge Base": {"relation": [{"id": "other"}, {"id": "third"}]}})]


def test_remove_knowledge_relation_writes_nothing_when_absent(monkeypatch):
    updates = []
    _patch(monkeypatch, types.SimpleNamespace(pages=_relation_pages([{"id": "other"}], updates)))
    assert notion.remove_knowledge_relation(_Env(), "pg", "kp") is False
    assert updates == []


def test_remove_knowledge_relation_refuses_a_truncated_relation(monkeypatch):
    updates = []
    _patch(
        monkeypatch,
        types.SimpleNamespace(pages=_relation_pages([{"id": "kp"}], updates, has_more=True)),
    )
    with pytest.raises(RuntimeError, match="more than one page of"):
        notion.remove_knowledge_relation(_Env(), "pg", "kp")
    assert updates == []


def test_remove_knowledge_relation_truncated_message_names_the_item_and_the_manual_fix(
    monkeypatch,
):
    _patch(
        monkeypatch,
        types.SimpleNamespace(pages=_relation_pages([{"id": "kp"}], [], has_more=True)),
    )
    with pytest.raises(RuntimeError, match=r"pg's Knowledge Base .* link to kp in Notion manually"):
        notion.remove_knowledge_relation(_Env(), "pg", "kp")


@pytest.mark.parametrize("error", [RequestTimeoutError(), httpx.ConnectError("boom"), _api_error()])
def test_remove_knowledge_relation_wraps_write_errors(monkeypatch, error):
    def _update(page_id, properties):
        raise error

    pages = types.SimpleNamespace(
        retrieve=lambda page_id: {"properties": {"Knowledge Base": {"relation": [{"id": "kp"}]}}},
        update=_update,
    )
    _patch(monkeypatch, types.SimpleNamespace(pages=pages))
    with pytest.raises(RuntimeError, match="failed to unlink pg -> kp"):
        notion.remove_knowledge_relation(_Env(), "pg", "kp")


def test_reset_item_to_tagged_clears_distribute_state(monkeypatch):
    updates = []
    _patch(
        monkeypatch,
        types.SimpleNamespace(
            pages=types.SimpleNamespace(
                update=lambda page_id, properties: updates.append((page_id, properties))
            )
        ),
    )
    notion.reset_item_to_tagged(_Env(), "pg")
    assert updates == [
        (
            "pg",
            {
                "status": {"select": {"name": "Tagged"}},
                "route_target": {"select": None},
                "distribute_notes": {"rich_text": []},
                "distribute_version": {"rich_text": []},
            },
        )
    ]


@pytest.mark.parametrize("error", [RequestTimeoutError(), httpx.ConnectError("boom"), _api_error()])
def test_reset_item_to_tagged_wraps_write_errors(monkeypatch, error):
    def _update(page_id, properties):
        raise error

    _patch(monkeypatch, types.SimpleNamespace(pages=types.SimpleNamespace(update=_update)))
    with pytest.raises(RuntimeError, match="failed to reset pg to Tagged"):
        notion.reset_item_to_tagged(_Env(), "pg")


def test_set_heading_text_writes_a_truly_empty_heading_for_empty_text(monkeypatch):
    blocks = _Blocks()
    _patch(monkeypatch, types.SimpleNamespace(blocks=blocks))
    notion.set_heading_text(_Env(), "h1", "")
    assert blocks.updates == [("h1", {"heading_1": {"rich_text": []}})]


def test_set_heading_text_fills_non_empty_text(monkeypatch):
    blocks = _Blocks()
    _patch(monkeypatch, types.SimpleNamespace(blocks=blocks))
    notion.set_heading_text(_Env(), "h1", "Workouts")
    assert blocks.updates == [
        ("h1", {"heading_1": {"rich_text": [{"text": {"content": "Workouts"}}]}})
    ]
