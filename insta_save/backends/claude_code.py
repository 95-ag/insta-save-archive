# insta_save/backends/claude_code.py
"""claude-code enrich backend — the §5 file contract, first implementation.

prepare (in stages/enrich.py) accumulates a budget-bounded batch and calls
build_prompt; a human-driven Claude session writes results.json; apply calls
parse_results. No formal Backend protocol yet — backend #2 (cowork/api) will
extract the shared interface (see ARCHITECTURE D5)."""

import json
from pathlib import Path

from insta_save.config.tags import allowed_topics

PROMPT_VERSION = "enrich_v2.0"

# Conservative per-slide image-token estimate. Anthropic vision bills images as input
# tokens ~ (w*h)/750 capped; a typical ~1080-wide IG portrait slide lands ~1600-1950
# tokens. 1600 is a representative figure used only for batch sizing — cost is flat on a
# Claude Max session, and #5's api/local backends reuse this for their cost/batch budget.
PER_SLIDE_IMAGE_TOKENS = 1600


def image_token_estimate(item) -> int:
    return len(item.get("slide_images") or []) * PER_SLIDE_IMAGE_TOKENS


def _vocab_block(group, vocab) -> str:
    """Human-readable vocab the session must choose from: content-types (pick 1),
    the group's topics + cross-group (pick 0-3), each with its one-line definition."""
    lines = ["CONTENT-TYPE (choose exactly one):"]
    for ct in vocab.content_types:
        lines.append(f"  {ct} — {vocab.definitions.get(ct, '')}")
    lines.append("")
    lines.append("TOPICS (choose 0-3, only from this list):")
    for t in allowed_topics(vocab, group):
        lines.append(f"  {t} — {vocab.definitions.get(t, '')}")
    return "\n".join(lines)


def _header_lines(group, vocab, template) -> list[str]:
    header = template.replace("{vocab_block}", _vocab_block(group, vocab))
    return [header, "", f"Group: {group}", "=" * 60, ""]


def _item_block(item) -> str:
    sid = item.get("source_id") or item["page_id"]
    lines = [f"--- {sid} ---",
             f"page_id:    {item['page_id']}",
             f"source_id:  {sid}",
             f"Type:       {item.get('type') or '[none]'}",
             f"Author:     {item.get('author') or '[none]'}"]
    lang = item.get("transcript_language")
    if lang and lang != "en":
        lines.append(f"Language:   {lang}  (translate to English; note the original language)")
    if item.get("caption"):
        lines.append(f"Caption:    {item['caption']}")
    if item.get("transcript"):
        lines.append(f"Transcript: {item['transcript']}")
    if item.get("ocr_text"):
        lines.append(f"OCR text:   {item['ocr_text']}")
    images = item.get("slide_images")
    if images:
        lines.append("Slides (Read each image and extract ALL information shown):")
        for path in images:
            lines.append(f"  {path}")
    return "\n".join(lines)


def build_prompt(group, items, vocab, template) -> str:
    """Assemble the full prompt: instruction header (template, with {vocab_block}
    filled) + a per-item content section."""
    lines = _header_lines(group, vocab, template)
    for item in items:
        lines.append(_item_block(item))
        lines.append("")
    return "\n".join(lines)


def header_len(group, vocab, template) -> int:
    """Rendered length of the fixed prompt header (instructions + vocab + group line)."""
    return len("\n".join(_header_lines(group, vocab, template)))


def item_len(item) -> int:
    """Rendered length one item adds to the prompt: its block plus the two newlines
    it contributes in the final join. Invariant:
    header_len(...) + sum(item_len(i)) == len(build_prompt(group, items, ...))."""
    return len(_item_block(item)) + 2


def parse_results(path) -> list[dict]:
    """Read results.json (a JSON array of result items). Raises ValueError if the
    file isn't a JSON array."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"results.json must be a JSON array, got {type(data).__name__}")
    return data
