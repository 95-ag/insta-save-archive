from insta_save.adapters import notion


def test_notion_truncate_counts_utf16_units():
    # 'a' + emoji (2 units) — limit 2 keeps only 'a'
    assert notion._notion_truncate("a\U0001F600b", limit=2) == "a"


def test_rich_text_chunked_splits_long_text():
    text = "x" * 4500
    chunks = notion._rich_text_chunked(text)["rich_text"]
    assert len(chunks) == 3 and sum(len(c["text"]["content"]) for c in chunks) == 4500


def test_raw_extraction_merge_preserves_other_versions():
    existing = {"v1.0-base": {"transcript": "old"}}
    merged = notion._merge_raw(existing, "v2.0-base-tuned",
                               {"transcript": "new", "ocr_text": None,
                                "carousel_slides": None, "last_processed_at": "t"})
    assert merged["v1.0-base"] == {"transcript": "old"}
    assert merged["v2.0-base-tuned"]["transcript"] == "new"


def test_ocr_text_synth_from_slides():
    slides = [{"slide": 1, "text": "A"}, {"slide": 2, "text": None}, {"slide": 3, "text": "C"}]
    assert notion._synth_ocr_text(slides) == "[Slide 1]\nA\n\n[Slide 3]\nC"


def test_schema_property_additions_only_missing():
    existing = {"tags": {}, "title": {}}  # tags already present
    add = notion._schema_property_additions(existing)
    assert "tags" not in add
    assert add["route_target"] == {"select": {}}
    assert add["extract_version"] == {"rich_text": {}}
    assert add["enrich_version"] == {"rich_text": {}}


def test_status_option_additions_appends_missing_keeping_existing():
    existing = ["Imported", "Queued", "Extracted", "Summarized", "Failed"]
    opts = notion._status_option_additions(existing)
    names = [o["name"] for o in opts]
    assert "Tagged" in names and "Routed" in names
    # existing options preserved (Notion replaces the full option list on update)
    assert {"Imported", "Queued", "Extracted", "Summarized", "Failed"} <= set(names)


def test_status_option_additions_noop_when_all_present():
    existing = ["Imported", "Queued", "Extracted", "Tagged", "Routed", "Failed"]
    assert notion._status_option_additions(existing) is None


from insta_save.adapters.notion import _build_ingest_properties, _url


def test_ingest_props_title_uses_handle_and_shortcode():
    props = _build_ingest_properties({
        "source_id": "ABC", "author": "natgeo", "ig_link": "https://x/reel/ABC/",
        "type": "Reel", "caption": "hi", "posted_date": "2026-01-01",
        "collections": ["Dev"]})
    assert props["title"]["title"][0]["text"]["content"] == "natgeo — ABC"
    assert props["status"]["select"]["name"] == "Imported"
    assert props["author"]["rich_text"][0]["text"]["content"] == "natgeo"
    assert props["collection"]["multi_select"] == [{"name": "Dev"}]


def test_ingest_props_omit_nulls_and_title_falls_back_to_shortcode():
    props = _build_ingest_properties({"source_id": "ABC", "author": None})
    assert props["title"]["title"][0]["text"]["content"] == "ABC"
    assert "author" not in props and "caption" not in props


def test_url_builder():
    assert _url("https://x/")["url"] == "https://x/"


def test_row_includes_author():
    page = {"id": "p1", "properties": {
        "source_id": {"rich_text": [{"text": {"content": "abc"}}]},
        "author": {"rich_text": [{"text": {"content": "playconveyor"}}]},
        "collection": {"multi_select": [{"name": "Makeup"}]},
    }}
    row = notion._row(page)
    assert row["author"] == "playconveyor"
    assert row["collections"] == ["Makeup"]


def test_row_author_none_when_absent():
    page = {"id": "p2", "properties": {}}
    assert notion._row(page)["author"] is None


def test_write_deterministic_builds_minimal_props(monkeypatch):
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

    env = type("E", (), {"notion_token": "t"})()
    notion.write_deterministic(env, "pg", "Makeup — dinarakasko", ["makeup"], "deterministic-v2.0")

    props = captured["props"]
    assert captured["page_id"] == "pg"
    assert props["status"]["select"]["name"] == "Tagged"
    assert props["title"]["title"][0]["text"]["content"] == "Makeup — dinarakasko"
    assert props["tags"]["multi_select"] == [{"name": "makeup"}]
    assert props["enrich_version"]["rich_text"][0]["text"]["content"] == "deterministic-v2.0"
    # no summary/externals written
    assert "summary" not in props and "externals" not in props


def test_write_deterministic_omits_empty_tags(monkeypatch):
    captured = {}
    monkeypatch.setattr(notion, "Client",
                        lambda auth: type("C", (), {"pages": type("P", (), {
                            "update": lambda self, page_id, properties: captured.update(properties=properties)})()})())
    monkeypatch.setattr(notion, "validate_notion", lambda env: None)
    notion.write_deterministic(type("E", (), {"notion_token": "t"})(), "pg", "T", [], "v")
    assert "tags" not in captured["properties"]
