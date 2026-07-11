# insta_save/backends/prompt.py
"""Backend-agnostic enrich prompt + budget assembly.

Shared by all enrich backends: build the full session prompt from a group's
items + vocab template, and measure header/item rendered lengths so prepare can
budget the prompt without re-rendering. Moved out of backends.claude_code so the
api/local/cowork backends (#5) reuse the same contract (ARCHITECTURE D5)."""


PROMPT_VERSION = "enrich_v2.0"

# Conservative per-slide image-token estimate. Anthropic vision bills images as input
# tokens ~ (w*h)/750 capped; a typical ~1080-wide IG portrait slide lands ~1600-1950
# tokens. 1600 is a representative figure used only for batch sizing — cost is flat on a
# Claude Max session, and #5's api/local backends reuse this for their cost/batch budget.
PER_SLIDE_IMAGE_TOKENS = 1600


def image_token_estimate(item) -> int:
    return len(item.get("slide_images") or []) * PER_SLIDE_IMAGE_TOKENS


def _vocab_block(group, vocab, groups=None) -> str:
    """Human-readable vocab the session must choose from: content-types (pick 1),
    then the group's granular topics (pick 3-4) and cross-group topics (pick 1-2),
    each with its one-line definition.

    groups: when provided (cross-group batch), the granular section is the union of all
    listed groups' granular topics (§7.3). None falls back to the single-group
    group_topics(group) path — identical for a single-group batch.
    """
    lines = ["CONTENT-TYPE (choose exactly one):"]
    for ct in vocab.content_types:
        lines.append(f"  {ct} — {vocab.definitions.get(ct, '')}")
    lines.append("")

    # Granular group topics: union across all groups (deduped, first-occurrence), or single group.
    if groups:
        seen: set[str] = set()
        granular: list[str] = []
        for g in groups:
            for t in vocab.group_topics(g):
                if t not in seen:
                    seen.add(t)
                    granular.append(t)
    else:
        granular = vocab.group_topics(group)

    lines.append("GROUP TOPICS (pick 3-4):")
    for t in granular:
        lines.append(f"  {t} — {vocab.definitions.get(t, '')}")
    lines.append("")

    # Cross-group topics are always the same regardless of groups= path.
    lines.append("CROSS-GROUP TOPICS (pick 1-2 if applicable):")
    for t in vocab.cross_group_topics:
        lines.append(f"  {t} — {vocab.definitions.get(t, '')}")

    return "\n".join(lines)


def translate_directive(output_language: str, fields: str = "the title, summary, and tags") -> str:
    """Shared by enrich + deterministic-title: emit the output fields in output_language
    and translate non-English source. Raw transcript/OCR are NOT rewritten (Data Integrity).
    `fields` lets the title-only path narrow the wording to just the title."""
    lang = output_language or "english"
    return (f"OUTPUT LANGUAGE: Write {fields} in {lang}. "
            f"If the caption, transcript, or OCR text is in another language, translate "
            f"the meaning into {lang} and note the original language. "
            f"Do not rewrite the raw transcript/OCR — only the output fields are in {lang}.")


def _header_lines(group, vocab, template, output_language="english", groups=None) -> list[str]:
    header = template.replace("{vocab_block}", _vocab_block(group, vocab, groups=groups))
    return [header, "", translate_directive(output_language), "", f"Group: {group}", "=" * 60, ""]


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


def build_prompt(group, items, vocab, template, output_language="english",
                 groups=None) -> str:
    """Assemble the full prompt: instruction header (template, with {vocab_block}
    filled, plus the output-language directive) + a per-item content section.

    groups: when provided (cross-group batch), the vocab block renders the union of all
    listed groups' granular topics. None falls back to the single-group group_topics(group) path."""
    lines = _header_lines(group, vocab, template, output_language, groups=groups)
    for item in items:
        lines.append(_item_block(item))
        lines.append("")
    return "\n".join(lines)


def header_len(group, vocab, template, output_language="english", groups=None) -> int:
    """Rendered length of the fixed prompt header (instructions + vocab + group line).

    groups: mirrors the groups= param of build_prompt (cross-group batch path) so that
    header_len(..., groups=G) + Σitem_len == len(build_prompt(..., groups=G))."""
    return len("\n".join(_header_lines(group, vocab, template, output_language, groups=groups)))


def item_len(item) -> int:
    """Rendered length one item adds to the prompt: its block plus the two newlines
    it contributes in the final join. Invariant:
    header_len(...) + sum(item_len(i)) == len(build_prompt(group, items, ...))."""
    return len(_item_block(item)) + 2
