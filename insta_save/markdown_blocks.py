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


def _write_plain(text) -> list:
    """Plain markdown (bold only) -> write-shape rich_text spans."""
    spans, pos = [], 0
    for m in _BOLD_RE.finditer(text):
        if m.start() > pos:
            spans.append({"type": "text", "text": {"content": text[pos : m.start()]}})
        spans.append(
            {
                "type": "text",
                "text": {"content": m.group(1)},
                "annotations": {"bold": True},
            }
        )
        pos = m.end()
    if pos < len(text):
        spans.append({"type": "text", "text": {"content": text[pos:]}})
    return [s for s in spans if s["text"]["content"]]


def _write_rich(text) -> list:
    """Inline markdown -> write-shape rich_text. Links take precedence over bold inside
    a label, matching the pilot: a bolded link label is not a shape we emit."""
    spans, pos = [], 0
    for m in _LINK_RE.finditer(text):
        if m.start() > pos:
            spans += _write_plain(text[pos : m.start()])
        spans.append({"type": "text", "text": {"content": m.group(1), "link": {"url": m.group(2)}}})
        pos = m.end()
    if pos < len(text):
        spans += _write_plain(text[pos:])
    return spans or [{"type": "text", "text": {"content": ""}}]


def _block(btype, text) -> dict:
    return {"type": btype, btype: {"rich_text": _write_rich(text)}}


def markdown_to_blocks(md) -> list:
    blocks = []
    for raw in (md or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("### "):
            blocks.append(_block("heading_3", line[4:]))
        elif line.startswith("## "):
            blocks.append(_block("heading_2", line[3:]))
        elif line.startswith("# "):
            blocks.append(_block("heading_1", line[2:]))
        elif line.lstrip().startswith("- "):
            blocks.append(_block("bulleted_list_item", line.lstrip()[2:]))
        else:
            blocks.append(_block("paragraph", line))
    return blocks
