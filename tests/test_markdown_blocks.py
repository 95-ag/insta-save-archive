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
