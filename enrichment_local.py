"""
Local enrichment — title and extracted_externals via Ollama.

Uses ollama Python library with tool_use for structured output.
Prompt is inline — title and entity extraction don't need versioned prompts.

Exposes:
  validate_local_enrichment_config  — check Ollama is reachable
  enrich_local                      — returns {"title": str, "extracted_externals": str}
"""

import logging

import ollama

from config import Config

log = logging.getLogger(__name__)

_LOCAL_TOOL = {
    "type": "function",
    "function": {
        "name": "save_local_enrichment",
        "description": "Save extracted enrichment fields for an Instagram post",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "Concise, descriptive, specific title. NOT the caption. Max 80 chars. "
                        "E.g. '5 Canva tricks for faster graphics' not 'Canva tips'."
                    ),
                },
                "extracted_externals": {
                    "type": "string",
                    "description": (
                        "Every tool, app, brand, creator, website, link, or location mentioned. "
                        "One per line: [type] name — context. "
                        "Types: tool, app, brand, creator, website, link, location, technique."
                    ),
                },
            },
            "required": ["title", "extracted_externals"],
        },
    },
}

_PROMPT_TEMPLATE = """Extract enrichment fields from this saved Instagram post.

Author: {author} | Type: {type} | Collections: {collections}

Caption: {caption}

Transcript: {transcript}

OCR/Slides: {ocr_text}

Call save_local_enrichment with:
1. title — concise and specific, not the caption, max 80 chars
2. extracted_externals — every tool, app, brand, creator, website, link, location mentioned, one per line
"""


def _normalize_externals(value) -> str:
    """
    Coerce whatever the model returns for extracted_externals into a plain string.
    qwen2.5 occasionally returns a list or dict instead of the expected string.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        # e.g. ["[tool] Figma — design", "[app] Notion — notes"]
        return "\n".join(str(item).strip() for item in value if item)
    if isinstance(value, dict):
        # e.g. {"Figma": "tool", "Notion": "app"}  (name → type mapping)
        return "\n".join(f"[{v}] {k}" for k, v in value.items())
    return str(value).strip()


def validate_local_enrichment_config(config: Config) -> None:
    """Raise RuntimeError if Ollama is not reachable."""
    try:
        client = ollama.Client(host=config.ollama_base_url)
        client.list()
    except Exception as exc:
        raise RuntimeError(
            f"Ollama not reachable at {config.ollama_base_url}. "
            f"Start with: ollama serve\nError: {exc}"
        ) from exc


def enrich_local(config: Config, item: dict) -> dict:
    """
    Call Ollama to generate title and extracted_externals for one item.
    Returns {"title": str, "extracted_externals": str}.
    Raises RuntimeError if the model does not call the tool.
    """
    prompt = _PROMPT_TEMPLATE.format(
        author=item.get("author") or "[unknown]",
        type=item.get("type") or "[unknown]",
        collections=", ".join(item.get("collection") or []) or "[unclassified]",
        caption=item.get("caption") or "[none]",
        transcript=item.get("transcript") or "[none]",
        ocr_text=item.get("ocr_text") or "[none]",
    )

    client = ollama.Client(host=config.ollama_base_url)
    response = client.chat(
        model=config.ollama_model,
        messages=[{"role": "user", "content": prompt}],
        tools=[_LOCAL_TOOL],
    )

    for tool_call in (response.message.tool_calls or []):
        if tool_call.function.name == "save_local_enrichment":
            args = tool_call.function.arguments
            log.debug("enrichment_local: tool call ok for %s", item.get("source_id"))
            return {
                "title": str(args.get("title") or "").strip(),
                "extracted_externals": _normalize_externals(args.get("extracted_externals")),
            }

    raise RuntimeError(
        f"enrichment_local: model did not call tool for source_id={item.get('source_id')!r}. "
        f"Response: {response.message.content!r}"
    )
