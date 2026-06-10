# tests/adapters/test_notion_enrich.py
from insta_save.adapters import notion


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
        tags=["tutorial", "ui-ux"], version="claude-sonnet/v2.0-enrich/Hustling")
    assert props["status"] == {"select": {"name": "Tagged"}}
    assert props["title"] == {"title": [{"text": {"content": "5 Canva tricks"}}]}
    assert [t["name"] for t in props["tags"]["multi_select"]] == ["tutorial", "ui-ux"]
    assert props["enrich_version"]["rich_text"][0]["text"]["content"] == \
        "claude-sonnet/v2.0-enrich/Hustling"
    assert props["summary"]["rich_text"][0]["text"]["content"] == "Body text."


def test_enrich_props_omits_empty_externals_and_tags():
    props = notion._enrich_props(
        title="t", summary="s", externals="", tags=[], version="v")
    assert "externals" not in props
    assert "tags" not in props
    assert props["title"]["title"][0]["text"]["content"] == "t"
