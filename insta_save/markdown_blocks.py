"""Markdown <-> Notion block conversion for the distribute stage. Pure — no I/O.

House convention: md `#` -> heading_1 (section), `##` -> heading_2 (topic), `###` -> heading_3,
`- ` -> bulleted_list_item, anything else -> paragraph.

Blocks that carry no text (table_of_contents, divider, images) render as nothing: they are
template furniture that lives above the content region and is never rewritten."""

import re
from itertools import groupby

_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
# A bold-wrapped link is matched before a bare one so the `**` become an annotation instead
# of literal asterisks in the label's surroundings.
_BOLD_LINK_RE = re.compile(r"\*\*\[([^\]]+)\]\((https?://[^)]+)\)\*\*")
_INLINE_LINK_RE = re.compile(f"{_BOLD_LINK_RE.pattern}|{_LINK_RE.pattern}")
# Inline marks, longest delimiter first so `**` never loses to `*`. Group names are Notion
# annotation keys, which is what lets the writer set them without a lookup table.
_MARK_RE = re.compile(
    r"\*\*(?P<bold>[^*]+)\*\*|~~(?P<strikethrough>[^~]+)~~|`(?P<code>[^`]+)`|\*(?P<italic>[^*]+)\*"
)
# Read-side wrappers, innermost first: code sits inside emphasis, so bold+code reads as
# **`x`** — the shape _MARK_RE parses back.
_MARK_WRAPPERS = (("code", "`"), ("strikethrough", "~~"), ("italic", "*"), ("bold", "**"))
_HEADING_PREFIX = {"heading_1": "#", "heading_2": "##", "heading_3": "###"}


def _span_content(span) -> str:
    """Read spans carry `plain_text`; constructed ones carry `text.content` (the shapes
    differ), so fall back to the write shape."""
    text = span.get("plain_text")
    if text is None:
        text = (span.get("text") or {}).get("content", "")
    return text


def _span_style(span) -> tuple:
    """The rendered identity of a span: which marks it carries and where it links. Two spans
    sharing this render as one run, so they can be merged before wrapping."""
    annotations = span.get("annotations") or {}
    href = span.get("href") or ((span.get("text") or {}).get("link") or {}).get("url")
    return tuple(bool(annotations.get(name)) for name, _ in _MARK_WRAPPERS), href


def _read_rich(spans) -> str:
    """Retrieved rich_text -> markdown.

    Adjacent spans of the same style are merged before wrapping: Notion (and _split_span, at
    the 2000-unit cap) can break one styled run into several spans, and wrapping each
    separately would read back as `**a****b**` instead of `**ab**`. Empty spans are dropped
    first so one sitting between two styled spans cannot break their merge."""
    out = []
    non_empty = [span for span in spans or [] if _span_content(span)]
    for (marks, href), group in groupby(non_empty, key=_span_style):
        text = "".join(_span_content(span) for span in group)
        for (_, delimiter), present in zip(_MARK_WRAPPERS, marks):
            if present:
                text = f"{delimiter}{text}{delimiter}"
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


_SPAN_UNIT_LIMIT = 2000  # Notion caps one rich_text span's content at 2000 UTF-16 code units


def _split_span(span, limit=_SPAN_UNIT_LIMIT) -> list:
    """Split a write-shape span into consecutive spans of at most `limit` UTF-16 units,
    each keeping the original link and annotations. Cuts only between code points, so a
    non-BMP character (2 units) is never split into lone surrogates."""
    content = span["text"]["content"]
    pieces, start, units = [], 0, 0
    for i, ch in enumerate(content):
        width = 2 if ord(ch) > 0xFFFF else 1
        if units + width > limit:
            pieces.append(content[start:i])
            start, units = i, 0
        units += width
    if not pieces:
        return [span]
    pieces.append(content[start:])
    return [{**span, "text": {**span["text"], "content": piece}} for piece in pieces]


def _marked_span(content, annotations) -> dict:
    span = {"type": "text", "text": {"content": content}}
    if annotations:
        span["annotations"] = dict(annotations)
    return span


def _write_plain(text, annotations=None) -> list:
    """Inline markdown -> write-shape rich_text spans, the inverse of _read_rich's wrapping.

    Recurses into each mark's content so nested marks combine into one span's annotations
    (**`x`** -> bold + code). Code is terminal: backticks make their content literal, so its
    inner text is never re-parsed."""
    base = annotations or {}
    spans, pos = [], 0
    for m in _MARK_RE.finditer(text):
        if m.start() > pos:
            spans.append(_marked_span(text[pos : m.start()], base))
        mark = m.lastgroup
        inner = m.group(mark)
        marked = {**base, mark: True}
        spans += [_marked_span(inner, marked)] if mark == "code" else _write_plain(inner, marked)
        pos = m.end()
    if pos < len(text):
        spans.append(_marked_span(text[pos:], base))
    return [s for s in spans if s["text"]["content"]]


def _write_rich(text) -> list:
    """Inline markdown -> write-shape rich_text. A link wins over marks *inside* its label,
    but a link wrapped in `**` becomes a bold link rather than leaving literal asterisks
    stranded around it."""
    spans, pos = [], 0
    for m in _INLINE_LINK_RE.finditer(text):
        if m.start() > pos:
            spans += _write_plain(text[pos : m.start()])
        bold_label, bold_url, label, url = m.groups()
        if bold_label is not None:
            spans.append(
                {
                    "type": "text",
                    "text": {"content": bold_label, "link": {"url": bold_url}},
                    "annotations": {"bold": True},
                }
            )
        else:
            spans.append({"type": "text", "text": {"content": label, "link": {"url": url}}})
        pos = m.end()
    if pos < len(text):
        spans += _write_plain(text[pos:])
    spans = [piece for span in spans for piece in _split_span(span)]
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


def indented_bullets(md) -> list:
    """Bullet lines carrying leading indentation.

    markdown_to_blocks flattens them — Notion nesting is not something this stage writes — so
    the structure a nested list implies is lost on the way in. Returning the lines lets the
    review say so before an apply, instead of the page quietly coming out flat."""
    return [
        line
        for line in (md or "").splitlines()
        if line[:1] in (" ", "\t") and line.lstrip().startswith("- ")
    ]


def split_sections(md) -> list:
    """Split a page body into [(h1_heading_text, section_markdown)].

    Content before the first H1 is dropped: below the content anchor there should be
    none, and above it lives template furniture this stage never rewrites."""
    sections, heading, buf = [], None, []
    for line in (md or "").splitlines():
        if line.startswith("# "):
            if heading is not None:
                sections.append((heading, "\n".join(buf).strip("\n")))
            heading, buf = line[2:].strip(), []
        elif heading is not None:
            buf.append(line)
    if heading is not None:
        sections.append((heading, "\n".join(buf).strip("\n")))
    return sections


def merge_sections(current_md, proposed_md) -> str:
    """Apply a proposal's sections onto the current body and return the FULL body.

    Heading text is the join key: a heading already present is replaced in place, a new
    one is appended at the end. Sections the proposal omits come through untouched.
    Raises ValueError if the current body has duplicate H1 headings, since heading text
    is the join key and duplicates make the merge ambiguous.
    Used to chain over-budget fill calls, where each call needs the running full body."""
    current_sections = split_sections(current_md)
    # Detect duplicate headings in current_md
    seen_headings = set()
    for heading, _ in current_sections:
        if heading in seen_headings:
            raise ValueError(
                f"page body has duplicate section heading {heading!r} — "
                f"rename one on the page before distributing"
            )
        seen_headings.add(heading)

    proposed = dict(split_sections(proposed_md))
    merged, seen = [], set()
    for heading, body in current_sections:
        body = proposed.get(heading, body)
        if heading in proposed:
            seen.add(heading)
        merged.append((heading, body))
    for heading, body in split_sections(proposed_md):
        if heading not in seen:
            merged.append((heading, body))
    lines = []
    for heading, body in merged:
        lines.append(f"# {heading}")
        if body:
            lines.append(body)
    return "\n".join(lines)
