"""Builds a recipe page body as Notion blocks directly.

NOT markdown: the body is a two-column `column_list`, which `markdown_to_blocks` cannot express
and `blocks_to_markdown` cannot read back. A recipe is a brand-new page, so there is nothing to
merge and no round-trip is needed — the model returns structured fields and this assembles them.
Notion requires the column_list's `column` children in the same create call, each carrying its
own children inline.
"""

TIP_SECTIONS = ("Technique", "Prep & Storage", "Serving", "Substitutions", "Variations", "Notes")


def _rich(text):
    return [{"type": "text", "text": {"content": text}}]


def _heading(level, text):
    key = f"heading_{level}"
    return {"object": "block", "type": key, key: {"rich_text": _rich(text)}}


def _bullet(text):
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": _rich(text)},
    }


def _divider():
    return {"object": "block", "type": "divider", "divider": {}}


def _section(title, lines, level=2):
    return [_heading(level, title), _divider()] + [_bullet(line) for line in lines]


def _column(children):
    return {"object": "block", "type": "column", "column": {"children": children}}


def _instructions(steps) -> list:
    """INSTRUCTIONS, either as bare bullets or as labelled methods.

    A source offering two COMPLETE alternatives for one dish (Instant Pot and stovetop, oven and
    air-fryer) gets a labelled subsection each, in the reply's own order. Flattening them loses
    which step belongs to which method, and parking the second in a Variations tip produced a
    single 333-character line on the first live row."""
    if isinstance(steps, dict):
        out = [_heading(2, "INSTRUCTIONS"), _divider()]
        for name, lines in steps.items():
            out += _section(name, lines, level=3)
        return out
    return _section("INSTRUCTIONS", steps)


def recipe_blocks(*, ingredients, steps, tips) -> list:
    """The body for one recipe. No embed: the source URL lives in the Video Link property."""
    if not steps or (isinstance(steps, dict) and not all(steps.values())):
        raise ValueError("recipe_blocks: steps are required — a recipe needs a method")
    unknown = [k for k in tips if k not in TIP_SECTIONS]
    if unknown:
        raise ValueError(f"recipe_blocks: unknown tip section(s) {sorted(unknown)}")

    left = _instructions(steps)
    filled = [(name, tips.get(name) or []) for name in TIP_SECTIONS]
    filled = [(name, lines) for name, lines in filled if lines]
    if filled:
        left += [_heading(2, "TIPs"), _divider()]
        for name, lines in filled:
            left += _section(name, lines, level=3)
    right = _section("INGREDIENTS", ingredients)
    return [
        {
            "object": "block",
            "type": "column_list",
            "column_list": {"children": [_column(left), _column(right)]},
        }
    ]
