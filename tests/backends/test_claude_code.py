# tests/backends/test_claude_code.py
import json
from insta_save.backends import claude_code as backend
from insta_save.config.tags import Vocab


def _vocab():
    return Vocab(
        content_types=["tool", "tutorial"],
        cross_group_topics=["ai", "design"],
        _group_topics={"Hustling": ["seo", "web-dev"]},
        definitions={"tool": "An app/site.", "tutorial": "A how-to.",
                     "ai": "AI theme.", "design": "Design theme.",
                     "seo": "Search.", "web-dev": "Web."},
    )


def test_batch_full_first_item_never_full():
    # an empty batch is never full, even if the item is huge
    assert backend.batch_full(0, 0, 999999, char_budget=100, max_items=5) is False


def test_batch_full_on_max_items():
    assert backend.batch_full(5, 10, 1, char_budget=100, max_items=5) is True


def test_batch_full_on_char_budget():
    assert backend.batch_full(2, 90, 20, char_budget=100, max_items=5) is True
    assert backend.batch_full(2, 90, 5, char_budget=100, max_items=5) is False


def test_build_prompt_includes_vocab_and_items_and_language():
    items = [{
        "page_id": "p1", "source_id": "s1", "title": "t", "author": "a",
        "type": "Reel", "caption": "cap", "transcript": "spoken",
        "ocr_text": None, "transcript_language": "ta",
    }]
    text = backend.build_prompt("Hustling", items, _vocab(), template="HEADER {vocab_block} END")
    assert "Hustling" in text
    assert "tool" in text and "seo" in text and "ai" in text     # vocab axes present
    assert "p1" in text and "s1" in text and "spoken" in text     # item content present
    assert "ta" in text                                           # language surfaced for translation


def test_parse_results_reads_array(tmp_path):
    p = tmp_path / "results.json"
    p.write_text(json.dumps([
        {"page_id": "p1", "source_id": "s1", "title": "T", "summary": "S",
         "externals": "", "content_type": "tool", "topics": ["seo"]}
    ]), encoding="utf-8")
    out = backend.parse_results(p)
    assert out[0]["page_id"] == "p1" and out[0]["content_type"] == "tool"


def test_parse_results_rejects_non_list(tmp_path):
    p = tmp_path / "results.json"
    p.write_text('{"page_id": "p1"}', encoding="utf-8")
    try:
        backend.parse_results(p)
        assert False, "expected ValueError"
    except ValueError:
        pass
