# tests/backends/test_prompt.py
from insta_save.backends import prompt
from insta_save.config.tags import Vocab


def _vocab():
    return Vocab(content_types=["tool"], cross_group_topics=["ai"],
                 _group_topics={"Hustling": ["seo"]},
                 definitions={"tool": "App.", "ai": "AI.", "seo": "Search."})


def test_invariant_header_plus_items_equals_rendered():
    items = [{"page_id": "p1", "source_id": "s1", "type": "Reel", "caption": "c",
              "transcript": "t", "transcript_language": "en"}]
    tmpl = "H {vocab_block} E"
    rendered = prompt.build_prompt("Hustling", items, _vocab(), tmpl)
    measured = prompt.header_len("Hustling", _vocab(), tmpl) + sum(prompt.item_len(i) for i in items)
    assert measured == len(rendered)


def test_translate_directive_mentions_language_and_translate():
    block = prompt.translate_directive("english")
    assert "english" in block.lower() and "translate" in block.lower()


def test_translate_directive_fields_param():
    block = prompt.translate_directive("english", fields="the title")
    assert "the title" in block and "summary" not in block


def test_build_prompt_includes_output_language_directive():
    items = [{"page_id": "p1", "source_id": "s1", "type": "Reel",
              "caption": "c", "transcript": "t", "transcript_language": "ta"}]
    text = prompt.build_prompt("Hustling", items, _vocab(), "H {vocab_block} E",
                               output_language="english")
    assert "translate" in text.lower() and "english" in text.lower()


def test_invariant_holds_with_output_language():
    items = [{"page_id": "p1", "source_id": "s1", "type": "Reel", "caption": "c",
              "transcript": "t", "transcript_language": "en"}]
    tmpl = "H {vocab_block} E"
    rendered = prompt.build_prompt("Hustling", items, _vocab(), tmpl, output_language="french")
    measured = prompt.header_len("Hustling", _vocab(), tmpl, output_language="french") \
        + sum(prompt.item_len(i) for i in items)
    assert measured == len(rendered)


def test_image_token_estimate():
    assert prompt.image_token_estimate({"slide_images": ["a", "b"]}) == 2 * prompt.PER_SLIDE_IMAGE_TOKENS
    assert prompt.image_token_estimate({"transcript": "x"}) == 0
