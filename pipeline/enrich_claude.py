"""
Phase 3 enrichment — AI-generated title, summary, insights, and externals.

Uses the Anthropic API with tool_use to enforce structured output.
Prompt template loaded from prompts/enrichment_{version}.txt.

Exposes:
  validate_enrichment_config  — check ANTHROPIC_API_KEY is set
  enrich_item                 — call Claude, return enrichment dict
"""

import logging
from pathlib import Path

import anthropic

from pipeline.config import Config

log = logging.getLogger(__name__)

# Tool definition — Claude must call this; tool_use enforces structured output.
_SAVE_ENRICHMENT_TOOL = {
    "name": "save_enrichment",
    "description": "Write enrichment fields for a saved Instagram post",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Concise, descriptive title. Not the caption. Max 80 chars.",
            },
            "expanded_summary": {
                "type": "string",
                "description": (
                    "Full content summary. 2-4 paragraphs. "
                    "Sufficient to understand the content without watching the original."
                ),
            },
            "key_insights": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3-7 distilled, transferable, actionable insights.",
            },
            "extracted_externals": {
                "type": "string",
                "description": (
                    "Every tool, app, product, brand, creator, technique, or location mentioned. "
                    "One per line: [type] name — context."
                ),
            },
        },
        "required": ["title", "expanded_summary", "key_insights", "extracted_externals"],
    },
}


def validate_enrichment_config(config: Config) -> None:
    """Raise RuntimeError if ANTHROPIC_API_KEY is not set."""
    if not config.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set in .env. "
            "Required for Phase 3 enrichment."
        )


def _load_prompt_template(version: str) -> str:
    """Load prompt template from prompts/enrichment_{version}.txt."""
    path = Path(__file__).parent.parent / "prompts" / f"enrichment_{version}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


def enrich_item(config: Config, item: dict) -> dict:
    """
    Call Claude to generate enrichment fields for one Notion item.

    item keys used: author, type, collection (list), caption, transcript, ocr_text.

    Returns dict with keys: title, expanded_summary, key_insights (list), extracted_externals.
    Raises RuntimeError if Claude does not return a valid tool call.
    """
    template = _load_prompt_template(config.enrichment_version)

    prompt = template.format(
        author=item.get("author") or "[unknown]",
        type=item.get("type") or "[unknown]",
        collections=", ".join(item.get("collection") or []) or "[unclassified]",
        caption=item.get("caption") or "[none]",
        transcript=item.get("transcript") or "[none]",
        ocr_text=item.get("ocr_text") or "[none]",
    )

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    response = client.messages.create(
        model=config.enrichment_model,
        max_tokens=2048,
        tools=[_SAVE_ENRICHMENT_TOOL],
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": prompt}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "save_enrichment":
            log.debug("enrichment: tool_use returned for source_id=%s", item.get("source_id"))
            return block.input

    raise RuntimeError(
        f"enrichment: Claude did not call save_enrichment for source_id={item.get('source_id')!r}. "
        f"Stop reason: {response.stop_reason}. Content: {response.content}"
    )
