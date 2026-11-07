# tests/adapters/test_notion_enrich.py
import types

from insta_save.adapters import notion


def _rt(s):
    return {"rich_text": [{"text": {"content": s}}]}


def test_get_page_content_applies_ocr_cleaner(monkeypatch):
    """OCR is cleaned at enrich-read (raw_extraction stays full); near-dup frames collapse."""
    page = {"properties": {
        "source_id": _rt("SRC1"),
        "ocr_text": _rt("dup frame line\ndup frame line\nunique tail line"),
        "raw_extraction": _rt("{}"),
        "extract_version": _rt("v2.0-base-tuned"),
        "type": {"select": {"name": "Reel"}},
        "collection": {"multi_select": [{"name": "Coding - AI"}]},
        "title": {"title": [{"text": {"content": "T"}}]},
    }}
    fake_pages = types.SimpleNamespace(retrieve=lambda page_id: page)
    monkeypatch.setattr(notion, "validate_notion", lambda env: None)
    monkeypatch.setattr(notion, "Client", lambda auth=None: types.SimpleNamespace(pages=fake_pages))
    env = types.SimpleNamespace(notion_token="x", notion_database_id="db", tmp_dir="tmp",
                                data_dir="data")

    out = notion.get_page_content(env, "pid")
    assert out["ocr_text"] == "dup frame line\nunique tail line"


def test_transcript_language_from_raw_picks_matching_version():
    raw = {"v2.0-base-tuned": {"transcript_language": "ta"},
           "v1": {"transcript_language": "en"}}
    assert notion._transcript_language_from_raw(raw, "v2.0-base-tuned") == "ta"


def test_transcript_language_from_raw_falls_back_to_any_present():
    raw = {"v1": {"transcript_language": "en"}}
    assert notion._transcript_language_from_raw(raw, "v2.0-base-tuned") == "en"


def test_transcript_language_from_raw_none_when_absent():
    assert notion._transcript_language_from_raw({}, "v2.0") is None
    assert notion._transcript_language_from_raw({"v1": {}}, "v2.0") is None


def test_enrich_props_full():
    props = notion._enrich_props(
        title="5 Canva tricks", summary="Body text.", externals="[Tools]\n  Canva — x",
        tags=["tutorial", "ui-ux"], primary_topic="ui-ux",
        version="claude-sonnet/v2.0-enrich/Hustling")
    assert props["status"] == {"select": {"name": "Tagged"}}
    assert props["title"] == {"title": [{"text": {"content": "5 Canva tricks"}}]}
    assert [t["name"] for t in props["tags"]["multi_select"]] == ["tutorial", "ui-ux"]
    assert props["primary_topic"] == {"select": {"name": "ui-ux"}}
    assert props["enrich_version"]["rich_text"][0]["text"]["content"] == \
        "claude-sonnet/v2.0-enrich/Hustling"
    assert props["summary"]["rich_text"][0]["text"]["content"] == "Body text."


def test_enrich_props_omits_empty_externals_and_tags():
    props = notion._enrich_props(
        title="t", summary="s", externals="", tags=[], primary_topic=None, version="v")
    assert "externals" not in props
    assert "tags" not in props
    assert "primary_topic" not in props
    assert props["title"]["title"][0]["text"]["content"] == "t"


def test_write_enrichment_sends_primary_topic_when_given(monkeypatch):
    captured = {}

    class _Pages:
        def update(self, page_id, properties):
            captured["page_id"] = page_id
            captured["props"] = properties

    class _Client:
        def __init__(self, auth):
            self.pages = _Pages()

    monkeypatch.setattr(notion, "Client", _Client)
    monkeypatch.setattr(notion, "validate_notion", lambda env: None)

    env = types.SimpleNamespace(notion_token="t")
    notion.write_enrichment(env, "pg1", {
        "title": "T", "summary": "S", "externals": "", "tags": ["seo"],
        "primary_topic": "seo"}, "v")

    assert captured["props"]["primary_topic"] == {"select": {"name": "seo"}}


def test_write_enrichment_omits_primary_topic_when_none(monkeypatch):
    captured = {}

    class _Pages:
        def update(self, page_id, properties):
            captured["page_id"] = page_id
            captured["props"] = properties

    class _Client:
        def __init__(self, auth):
            self.pages = _Pages()

    monkeypatch.setattr(notion, "Client", _Client)
    monkeypatch.setattr(notion, "validate_notion", lambda env: None)

    env = types.SimpleNamespace(notion_token="t")
    notion.write_enrichment(env, "pg1", {
        "title": "T", "summary": "S", "externals": "", "tags": ["seo"],
        "primary_topic": None}, "v")

    assert "primary_topic" not in captured["props"]
