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


def batch_full(batch_len, total_chars, next_size, char_budget, max_items) -> bool:
    """True if the batch already has >=1 item AND adding next_size would breach a cap.
    Checked BEFORE adding the candidate, so the first item is always admitted.
    max_items=None means no item-count cap — char_budget still bounds the batch."""
    if batch_len < 1:
        return False
    if max_items is not None and batch_len >= max_items:
        return True
    return total_chars + next_size > char_budget


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


def build_prompt(group, items, vocab, template) -> str:
    """Assemble the full prompt: instruction header (template, with {vocab_block}
    filled) + a per-item content section."""
    header = template.replace("{vocab_block}", _vocab_block(group, vocab))
    lines = [header, "", f"Group: {group}", "=" * 60, ""]
    for item in items:
        sid = item.get("source_id") or item["page_id"]
        lines.append(f"--- {sid} ---")
        lines.append(f"page_id:    {item['page_id']}")
        lines.append(f"source_id:  {sid}")
        lines.append(f"Type:       {item.get('type') or '[none]'}")
        lines.append(f"Author:     {item.get('author') or '[none]'}")
        lang = item.get("transcript_language")
        if lang and lang != "en":
            lines.append(f"Language:   {lang}  (translate to English; note the original language)")
        if item.get("caption"):
            lines.append(f"Caption:    {item['caption']}")
        if item.get("transcript"):
            lines.append(f"Transcript: {item['transcript']}")
        if item.get("ocr_text"):
            lines.append(f"OCR text:   {item['ocr_text']}")
        lines.append("")
    return "\n".join(lines)


def parse_results(path) -> list[dict]:
    """Read results.json (a JSON array of result items). Raises ValueError if the
    file isn't a JSON array."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"results.json must be a JSON array, got {type(data).__name__}")
    return data
