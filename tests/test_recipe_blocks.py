import pytest

from insta_save.recipe_blocks import TIP_SECTIONS, recipe_blocks


def _text(block):
    t = block["type"]
    return "".join(s["text"]["content"] for s in block[t].get("rich_text", []))


def _cols(out):
    return out[0]["column_list"]["children"]


def test_recipe_blocks_builds_one_column_list_with_two_columns():
    out = recipe_blocks(ingredients=["a"], steps=["s"], tips={})
    assert len(out) == 1 and out[0]["type"] == "column_list"
    cols = _cols(out)
    assert len(cols) == 2 and all(c["type"] == "column" for c in cols)


def test_left_column_holds_instructions_and_the_right_holds_ingredients():
    out = recipe_blocks(ingredients=["1 cup rice"], steps=["Rinse."], tips={})
    left, right = _cols(out)
    assert _text(left["column"]["children"][0]) == "INSTRUCTIONS"
    assert _text(right["column"]["children"][0]) == "INGREDIENTS"
    assert _text(right["column"]["children"][2]) == "1 cup rice"


def test_steps_and_ingredients_are_bulleted_not_paragraphs():
    """The old hand-made rows used paragraphs; bullets were chosen for everything new."""
    out = recipe_blocks(ingredients=["1 cup rice"], steps=["Rinse."], tips={})
    left, right = _cols(out)
    assert left["column"]["children"][2]["type"] == "bulleted_list_item"
    assert right["column"]["children"][2]["type"] == "bulleted_list_item"


def test_each_section_heading_is_followed_by_a_divider():
    out = recipe_blocks(ingredients=["a"], steps=["s"], tips={})
    left, _ = _cols(out)
    assert left["column"]["children"][1]["type"] == "divider"


def test_tips_render_as_heading_3_subsections_in_a_fixed_order():
    out = recipe_blocks(
        ingredients=["a"], steps=["s"], tips={"Serving": ["Hot."], "Technique": ["Low heat."]}
    )
    left, _ = _cols(out)
    kinds = [(b["type"], _text(b)) for b in left["column"]["children"]]
    assert ("heading_2", "TIPs") in kinds
    assert kinds.index(("heading_3", "Technique")) < kinds.index(("heading_3", "Serving"))


def test_an_empty_tip_subsection_is_omitted_entirely():
    """No placeholder headings - an absent subsection renders nothing (data-integrity)."""
    out = recipe_blocks(ingredients=["a"], steps=["s"], tips={"Serving": [], "Notes": None})
    left, _ = _cols(out)
    assert not any(_text(b) in ("Serving", "Notes") for b in left["column"]["children"])


def test_the_tips_heading_is_omitted_when_there_are_no_tips_at_all():
    out = recipe_blocks(ingredients=["a"], steps=["s"], tips={})
    left, _ = _cols(out)
    assert not any(_text(b) == "TIPs" for b in left["column"]["children"])


def test_no_embed_is_emitted():
    """The old template embedded the source in column 2; the agreed new shape does not - the
    URL lives in the Video Link property instead."""
    out = recipe_blocks(ingredients=["a"], steps=["s"], tips={})
    kinds = {b["type"] for col in _cols(out) for b in col["column"]["children"]}
    assert "embed" not in kinds and "video" not in kinds


def test_an_unknown_tip_section_is_rejected():
    with pytest.raises(ValueError, match="unknown tip section"):
        recipe_blocks(ingredients=["a"], steps=["s"], tips={"Nonsense": ["x"]})


def test_a_recipe_with_no_steps_is_rejected():
    """A row with no method is not a recipe; the stage must Misroute it, not write an empty body."""
    with pytest.raises(ValueError, match="steps"):
        recipe_blocks(ingredients=["a"], steps=[], tips={})


def test_every_tip_section_name_is_accepted():
    tips = {name: [f"{name} line"] for name in TIP_SECTIONS}
    out = recipe_blocks(ingredients=["a"], steps=["s"], tips=tips)
    left, _ = _cols(out)
    rendered = [_text(b) for b in left["column"]["children"] if b["type"] == "heading_3"]
    assert rendered == list(TIP_SECTIONS)


def test_named_methods_render_as_labelled_subsections_under_instructions():
    """A source giving two COMPLETE methods must not flatten them, nor cram the second into a
    Variations tip — the Congee's whole stovetop method arrived as one 333-char line."""
    out = recipe_blocks(
        ingredients=["1 cup rice"],
        steps={"Instant Pot Method": ["Pressure cook 30 min."],
               "Stovetop Method": ["Simmer 30 min.", "Stir."]},
        tips={},
    )
    left, _ = _cols(out)
    kinds = [(b["type"], _text(b)) for b in left["column"]["children"]]
    assert kinds[0] == ("heading_2", "INSTRUCTIONS")
    assert ("heading_3", "Instant Pot Method") in kinds
    assert ("heading_3", "Stovetop Method") in kinds
    assert kinds.index(("heading_3", "Instant Pot Method")) < kinds.index(
        ("heading_3", "Stovetop Method")), "method order is the reply's order"
    assert ("bulleted_list_item", "Simmer 30 min.") in kinds


def test_a_single_method_list_still_renders_bare_bullets():
    out = recipe_blocks(ingredients=["a"], steps=["Rinse."], tips={})
    left, _ = _cols(out)
    assert not any(b["type"] == "heading_3" for b in left["column"]["children"])


def test_a_named_method_with_no_steps_is_rejected():
    with pytest.raises(ValueError, match="steps"):
        recipe_blocks(ingredients=["a"], steps={"Oven Method": []}, tips={})


def test_named_methods_with_every_group_empty_is_rejected():
    with pytest.raises(ValueError, match="steps"):
        recipe_blocks(ingredients=["a"], steps={}, tips={})
