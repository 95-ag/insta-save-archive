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


def test_header_and_item_len_sum_to_rendered_prompt_length():
    # The budgeting invariant: header_len + sum(item_len) == len(build_prompt(...)),
    # so prepare can budget the rendered prompt without re-rendering each step.
    items = [
        {"page_id": "p1", "source_id": "s1", "type": "Reel", "author": "a",
         "caption": "cap one", "transcript": "spoken words", "ocr_text": "",
         "transcript_language": "en"},
        {"page_id": "p2", "source_id": "s2", "type": "Reel", "author": "b",
         "caption": "", "transcript": "", "ocr_text": "slide text",
         "transcript_language": "ta"},
    ]
    tmpl = "HEADER {vocab_block} END"
    rendered = backend.build_prompt("Hustling", items, _vocab(), tmpl)
    measured = backend.header_len("Hustling", _vocab(), tmpl) + sum(backend.item_len(i) for i in items)
    assert measured == len(rendered)


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


def test_item_block_lists_slide_images():
    item = {"page_id": "p1", "source_id": "s1", "type": "Carousel", "author": "a",
            "caption": "cap", "slide_images": ["tmp/slides/ab/slide1.jpg",
                                               "tmp/slides/ab/slide2.jpg"]}
    block = backend._item_block(item)
    assert "tmp/slides/ab/slide1.jpg" in block and "tmp/slides/ab/slide2.jpg" in block


def test_item_block_no_images_section_for_text_item():
    item = {"page_id": "p1", "source_id": "s1", "type": "Reel", "transcript": "spoken"}
    assert "IMAGES" not in backend._item_block(item) and "Slides" not in backend._item_block(item)


def test_image_token_estimate_counts_slides():
    assert backend.image_token_estimate({"slide_images": ["a", "b", "c"]}) == 3 * backend.PER_SLIDE_IMAGE_TOKENS
    assert backend.image_token_estimate({"transcript": "x"}) == 0
