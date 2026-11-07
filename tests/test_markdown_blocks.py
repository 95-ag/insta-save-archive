import pytest

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


def _utf16_units(text):
    return len(text.encode("utf-16-le")) // 2


def test_markdown_to_blocks_splits_a_long_span_at_2000_units():
    line = "abcde" * 900
    [block] = mb.markdown_to_blocks(line)
    spans = block["paragraph"]["rich_text"]
    assert [len(s["text"]["content"]) for s in spans] == [2000, 2000, 500]
    assert "".join(s["text"]["content"] for s in spans) == line


def test_markdown_to_blocks_split_link_spans_all_carry_the_link():
    label = "x" * 2500
    spans = mb.markdown_to_blocks(f"[{label}](https://ig/x)")[0]["paragraph"]["rich_text"]
    assert len(spans) == 2
    assert all(s["text"]["link"] == {"url": "https://ig/x"} for s in spans)
    assert "".join(s["text"]["content"] for s in spans) == label


def test_markdown_to_blocks_split_bold_spans_keep_their_annotations():
    text = "y" * 2100
    spans = mb.markdown_to_blocks(f"**{text}**")[0]["paragraph"]["rich_text"]
    assert len(spans) == 2
    assert all(s["annotations"] == {"bold": True} for s in spans)


def test_markdown_to_blocks_never_splits_a_surrogate_pair():
    line = "\U0001f525" * 1500
    spans = mb.markdown_to_blocks(f"- {line}")[0]["bulleted_list_item"]["rich_text"]
    assert len(spans) == 2
    assert all(_utf16_units(s["text"]["content"]) <= 2000 for s in spans)
    for span in spans:
        span["text"]["content"].encode("utf-16-le", errors="strict")
        assert not any(0xD800 <= ord(ch) <= 0xDFFF for ch in span["text"]["content"])
    assert "".join(s["text"]["content"] for s in spans) == line


def test_markdown_to_blocks_round_trips_a_long_line():
    md = "# Workouts\n- " + "cue " * 1200 + "[Hip drills](https://ig/x)"
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


def test_merge_sections_rejects_duplicate_current_headings():
    with pytest.raises(ValueError, match="duplicate section heading 'One'"):
        mb.merge_sections("# One\n- a\n# One\n- b", "# One\n- replaced")


def _read_span(content, *, annotations=None, href=None):
    span = {"plain_text": content}
    if annotations:
        span["annotations"] = annotations
    if href:
        span["href"] = href
    return span


def test_read_rich_emits_italic_code_and_strikethrough():
    spans = [
        _read_span("i", annotations={"italic": True}),
        _read_span("c", annotations={"code": True}),
        _read_span("s", annotations={"strikethrough": True}),
    ]
    assert mb._read_rich(spans) == "*i*`c`~~s~~"


def test_read_rich_nests_code_inside_bold_so_the_writer_can_parse_it_back():
    spans = [_read_span("x", annotations={"bold": True, "code": True})]
    read = mb._read_rich(spans)
    assert read == "**`x`**"
    assert mb._write_rich(read) == [
        {"type": "text", "text": {"content": "x"}, "annotations": {"bold": True, "code": True}}
    ]


def test_read_rich_merges_a_styled_run_split_across_spans():
    """_split_span cuts a long run at the 2000-unit cap; wrapping each piece on its own would
    read back as `**a****b**` and change what the model is shown."""
    spans = [
        _read_span("a", annotations={"bold": True}),
        _read_span("b", annotations={"bold": True}),
    ]
    assert mb._read_rich(spans) == "**ab**"


def test_read_rich_merges_across_an_empty_span_between_two_styled_ones():
    spans = [
        _read_span("a", annotations={"bold": True}),
        _read_span(""),
        _read_span("b", annotations={"bold": True}),
    ]
    assert mb._read_rich(spans) == "**ab**"


def test_read_rich_keeps_differently_styled_neighbours_apart():
    spans = [
        _read_span("a", annotations={"bold": True}),
        _read_span("b", annotations={"italic": True}),
    ]
    assert mb._read_rich(spans) == "**a***b*"


def test_read_rich_merges_a_split_link_run():
    spans = [
        _read_span("lab", href="https://x/1"),
        _read_span("el", href="https://x/1"),
    ]
    assert mb._read_rich(spans) == "[label](https://x/1)"


def test_write_rich_makes_a_bold_wrapped_link_a_bold_link():
    """A bolded link used to leave literal `**` stranded around the label on the page."""
    spans = mb._write_rich("see **[title](https://x/1)** now")
    assert spans == [
        {"type": "text", "text": {"content": "see "}},
        {
            "type": "text",
            "text": {"content": "title", "link": {"url": "https://x/1"}},
            "annotations": {"bold": True},
        },
        {"type": "text", "text": {"content": " now"}},
    ]
    assert "**" not in "".join(s["text"]["content"] for s in spans)


def test_write_rich_still_reads_a_bare_link():
    assert mb._write_rich("[t](https://x/1)") == [
        {"type": "text", "text": {"content": "t", "link": {"url": "https://x/1"}}}
    ]


def test_write_plain_combines_nested_marks_into_one_span():
    assert mb._write_plain("~~*x*~~") == [
        {
            "type": "text",
            "text": {"content": "x"},
            "annotations": {"strikethrough": True, "italic": True},
        }
    ]


def test_write_plain_leaves_code_content_literal():
    """Backticks make their content literal, so `*x*` inside code stays text, not italic."""
    assert mb._write_plain("`*x*`") == [
        {"type": "text", "text": {"content": "*x*"}, "annotations": {"code": True}}
    ]


def test_inline_marks_round_trip_through_a_read_then_write():
    original = [
        _read_span("plain "),
        _read_span("b", annotations={"bold": True}),
        _read_span("i", annotations={"italic": True}),
        _read_span("s", annotations={"strikethrough": True}),
        _read_span("c", annotations={"code": True}),
    ]
    rewritten = mb._write_rich(mb._read_rich(original))
    assert [s["text"]["content"] for s in rewritten] == ["plain ", "b", "i", "s", "c"]
    assert [s.get("annotations") for s in rewritten] == [
        None,
        {"bold": True},
        {"italic": True},
        {"strikethrough": True},
        {"code": True},
    ]


def test_indented_bullets_reports_only_indented_list_lines():
    md = "# S\n- top\n  - nested\n\t- tabbed\n  not a bullet\n- another top"
    assert mb.indented_bullets(md) == ["  - nested", "\t- tabbed"]


def test_indented_bullets_is_empty_for_flat_markdown():
    assert mb.indented_bullets("# S\n- one\n- two") == []
