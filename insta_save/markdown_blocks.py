"""Markdown <-> Notion block conversion for the distribute stage. Pure — no I/O.

House convention: md `#` -> heading_1 (section), `##` -> heading_2 (topic), `###` -> heading_3,
`- ` -> bulleted_list_item, anything else -> paragraph.

Blocks that carry no text (table_of_contents, divider, images) render as nothing: they are
template furniture that lives above the content region and is never rewritten."""

import re

_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_HEADING_PREFIX = {"heading_1": "#", "heading_2": "##", "heading_3": "###"}


def _read_rich(spans) -> str:
    """Retrieved rich_text -> markdown. Read spans carry `plain_text`; constructed ones
    carry `text.content` (the shapes differ), so fall back to the write shape."""
    out = []
    for span in spans or []:
        text = span.get("plain_text")
        if text is None:
            text = (span.get("text") or {}).get("content", "")
        if not text:
            continue
        if (span.get("annotations") or {}).get("bold"):
            text = f"**{text}**"
        href = span.get("href") or ((span.get("text") or {}).get("link") or {}).get("url")
        if href:
            text = f"[{text}]({href})"
        out.append(text)
    return "".join(out)


def blocks_to_markdown(blocks) -> str:
    lines = []
    for block in blocks or []:
        btype = block.get("type")
        body = block.get(btype) or {}
        text = _read_rich(body.get("rich_text"))
        if btype in _HEADING_PREFIX:
            lines.append(f"{_HEADING_PREFIX[btype]} {text}")
        elif btype == "bulleted_list_item":
            lines.append(f"- {text}")
        elif btype == "paragraph":
            lines.append(text)
    return "\n".join(lines)
