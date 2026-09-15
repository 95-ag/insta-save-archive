from insta_save import markdown_blocks as mb


def _read_block(btype, spans):
    return {"type": btype, btype: {"rich_text": spans}}


def _span(text, *, bold=False, href=None):
    span = {"plain_text": text, "annotations": {"bold": bold}}
    if href:
        span["href"] = href
    return span


def test_blocks_to_markdown_renders_heading_levels():
    blocks = [
        _read_block("heading_1", [_span("Workouts")]),
        _read_block("heading_2", [_span("Pelvic tilt")]),
        _read_block("heading_3", [_span("Detail")]),
    ]
    assert mb.blocks_to_markdown(blocks) == "# Workouts\n## Pelvic tilt\n### Detail"


def test_blocks_to_markdown_renders_bullets_and_paragraphs():
    blocks = [
        _read_block("bulleted_list_item", [_span("cue one")]),
        _read_block("paragraph", [_span("lead line")]),
    ]
    assert mb.blocks_to_markdown(blocks) == "- cue one\nlead line"


def test_blocks_to_markdown_round_trips_links_and_bold():
    blocks = [
        _read_block(
            "paragraph", [_span("Watch: ", bold=True), _span("Hip drills", href="https://ig/x")]
        )
    ]
    assert mb.blocks_to_markdown(blocks) == "**Watch: **[Hip drills](https://ig/x)"


def test_blocks_to_markdown_skips_template_furniture():
    blocks = [
        {"type": "table_of_contents", "table_of_contents": {}},
        {"type": "divider", "divider": {}},
        _read_block("heading_1", [_span("Workouts")]),
    ]
    assert mb.blocks_to_markdown(blocks) == "# Workouts"


def test_markdown_to_blocks_maps_heading_levels():
    blocks = mb.markdown_to_blocks("# Sec\n## Topic\n### Detail")
    assert [b["type"] for b in blocks] == ["heading_1", "heading_2", "heading_3"]
    assert blocks[0]["heading_1"]["rich_text"][0]["text"]["content"] == "Sec"


def test_markdown_to_blocks_maps_bullets_and_paragraphs():
    blocks = mb.markdown_to_blocks("- cue\nlead line")
    assert [b["type"] for b in blocks] == ["bulleted_list_item", "paragraph"]


def test_markdown_to_blocks_drops_blank_lines():
    assert len(mb.markdown_to_blocks("# A\n\n\n- b")) == 2


def test_markdown_to_blocks_builds_link_spans():
    spans = mb.markdown_to_blocks("see [Hip drills](https://ig/x) now")[0]["paragraph"]["rich_text"]
    assert [s["text"]["content"] for s in spans] == ["see ", "Hip drills", " now"]
    assert spans[1]["text"]["link"] == {"url": "https://ig/x"}
    assert "plain_text" not in spans[1]


def test_markdown_to_blocks_builds_bold_spans():
    spans = mb.markdown_to_blocks("**Watch:** go")[0]["paragraph"]["rich_text"]
    assert spans[0]["text"]["content"] == "Watch:"
    assert spans[0]["annotations"] == {"bold": True}
    assert spans[1]["text"]["content"] == " go"


def test_markdown_to_blocks_round_trips_through_blocks_to_markdown():
    md = "# Workouts\n## Pelvic tilt\n- cue one\n🔗 [Hip drills](https://ig/x)"
    assert mb.blocks_to_markdown(mb.markdown_to_blocks(md)) == md


def test_split_sections_splits_on_h1_only():
    md = "# One\n## Topic\n- a\n# Two\n- b"
    assert mb.split_sections(md) == [("One", "## Topic\n- a"), ("Two", "- b")]


def test_split_sections_ignores_content_before_the_first_h1():
    assert mb.split_sections("stray line\n# One\n- a") == [("One", "- a")]


def test_split_sections_returns_empty_for_no_headings():
    assert mb.split_sections("- just a bullet") == []


def test_merge_sections_replaces_a_matching_heading_in_place():
    current = "# One\n- old\n# Two\n- keep"
    assert mb.merge_sections(current, "# One\n- new") == "# One\n- new\n# Two\n- keep"


def test_merge_sections_appends_a_new_heading_at_the_end():
    assert mb.merge_sections("# One\n- a", "# Two\n- b") == "# One\n- a\n# Two\n- b"


def test_merge_sections_leaves_untouched_sections_byte_identical():
    current = "# One\n- a\n# Two\n-  odd   spacing"
    merged = mb.merge_sections(current, "# One\n- new")
    assert "-  odd   spacing" in merged
