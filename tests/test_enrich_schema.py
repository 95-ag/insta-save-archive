# tests/test_enrich_schema.py
from insta_save import enrich_schema as es


def test_valid_item_passes_through():
    ct, topics = es.validate_item(
        {"content_type": "tool", "topics": ["seo", "web-dev"]},
        allowed_content_types=["tool", "tutorial"],
        allowed_topics=["seo", "web-dev", "ai"],
    )
    assert ct == "tool"
    assert topics == ["seo", "web-dev"]


def test_out_of_vocab_content_type_is_blanked():
    ct, topics = es.validate_item(
        {"content_type": "meme", "topics": ["seo"]},
        allowed_content_types=["tool"], allowed_topics=["seo"],
    )
    assert ct is None
    assert topics == ["seo"]


def test_topics_deduped_dropped_and_clamped():
    ct, topics = es.validate_item(
        {"content_type": "tool",
         "topics": ["seo", "seo", "bogus", "web-dev", "ai", "design"]},
        allowed_content_types=["tool"],
        allowed_topics=["seo", "web-dev", "ai", "design"],
    )
    # deduped (one seo), bogus dropped, clamped to 6, order preserved
    assert ct == "tool"
    assert topics == ["seo", "web-dev", "ai", "design"]


def test_missing_or_empty_topics():
    ct, topics = es.validate_item(
        {"content_type": "tool"}, allowed_content_types=["tool"], allowed_topics=["seo"])
    assert ct == "tool" and topics == []


def test_tags_for_composes_content_type_first():
    assert es.tags_for("tool", ["seo", "web-dev"]) == ["tool", "seo", "web-dev"]


def test_tags_for_blank_content_type_contributes_nothing():
    assert es.tags_for(None, ["seo"]) == ["seo"]


def test_max_topics_is_six():
    from insta_save import enrich_schema
    assert enrich_schema.MAX_TOPICS == 6


def test_validate_item_keeps_up_to_six_topics():
    from insta_save import enrich_schema
    item = {"content_type": "tool", "topics": ["a", "b", "c", "d", "e", "f", "g"]}
    allowed_ct = {"tool"}
    allowed_topics = {"a", "b", "c", "d", "e", "f", "g"}
    ct, topics = enrich_schema.validate_item(item, allowed_ct, allowed_topics)
    assert len(topics) == 6 and ct == "tool"
