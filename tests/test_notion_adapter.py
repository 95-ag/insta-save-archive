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
