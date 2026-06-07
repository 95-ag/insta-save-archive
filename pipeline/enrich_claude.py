"""
Phase 3 enrichment — AI-generated summary and insights.

Uses the Anthropic API with tool_use to enforce structured output.
Prompt template loaded from prompts/enrichment_{version}.txt.

Title and extracted_externals are written by the local Ollama pass (enrich_local.py).
This module writes only expanded_summary and key_insights — sets status Summarised.

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
# Title and extracted_externals are handled by the local Ollama pass — not written here.
_SAVE_ENRICHMENT_TOOL = {
    "name": "save_enrichment",
    "description": "Write AI-generated summary and insights for a saved Instagram post",
    "input_schema": {
        "type": "object",
        "properties": {
            "expanded_summary": {
                "type": "string",
                "description": (
                    "Full content summary. 2-4 paragraphs. "
                    "Enough to replace rewatching. Capture the method, reasoning, and details."
                ),
            },
            "key_insights": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "3-7 transferable, actionable insights. "
                    "Reusable principles, not a recap of the content."
                ),
            },
        },
        "required": ["expanded_summary", "key_insights"],
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

    Returns dict with keys: expanded_summary (str), key_insights (list[str]).
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
