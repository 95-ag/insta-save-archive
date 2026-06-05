"""
Local enrichment — title and extracted_externals via Ollama.

Uses ollama Python library with tool_use for structured output.
Prompt is inline — title and entity extraction don't need versioned prompts.

Exposes:
  validate_local_enrichment_config  — check Ollama is reachable
  enrich_local                      — returns {"title": str, "extracted_externals": str}
"""

import logging
import re

import ollama

from config import Config

log = logging.getLogger(__name__)

# Valid types for extracted_externals lines.
_KNOWN_TYPES = frozenset(
    {"tool", "app", "brand", "creator", "website", "link", "location", "technique", "ref"}
)

# Matches lines already in correct format: "[type] ..."
_BRACKET_RE = re.compile(r"^\[([a-zA-Z]+)\]\s*\S")

# Matches "type name" or "type: name" where type is a known word
_PREFIX_RE = re.compile(r"^([a-zA-Z]+)[:\s]\s*(.+)$")

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
                        "Every tool, app, brand, creator, website, link, or location explicitly mentioned. "
                        "REQUIRED FORMAT — one entry per line, exactly: [type] name — context\n"
                        "Valid types: tool, app, brand, creator, website, link, location, technique\n"
                        "Examples:\n"
                        "[tool] Figma — UI design tool used\n"
                        "[brand] Dropbox — subject of the post\n"
                        "[creator] @millmotion — animator referenced\n"
                        "[website] brand.dropbox.com — brand guidelines\n"
                        "[technique] grid layout — design method shown\n"
                        "If nothing is mentioned, return an empty string."
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
1. title — concise and specific (not the caption), max 80 chars
2. extracted_externals — every tool, app, brand, creator, website, link, or location explicitly mentioned.
   Each entry MUST follow this exact format (one per line):
   [type] name — context
   Valid types: tool, app, brand, creator, website, link, location, technique
   Example:
   [tool] Figma — UI design tool used in tutorial
   [brand] Dropbox — subject of the post
   [creator] @millmotion — animator referenced
"""


def _format_externals_line(line: str) -> str:
    """
    Normalise one extracted_externals line to [type] name — context format.

    Passes through lines already in correct format.
    Converts "type name" or "type: name" patterns where type is a known word.
    Tags unrecognised lines as [ref] so they are still stored and parseable.
    """
    line = line.strip(" ,;-•*")
    if not line:
        return ""

    # Already correct: starts with [type] followed by non-whitespace
    if _BRACKET_RE.match(line):
        return line

    # "type name" or "type: name" where first word is a known type
    m = _PREFIX_RE.match(line)
    if m and m.group(1).lower() in _KNOWN_TYPES:
        type_word = m.group(1).lower()
        rest = m.group(2).strip()
        return f"[{type_word}] {rest}"

    # Unrecognised format — preserve content, tag as [ref] for downstream parsing
    return f"[ref] {line}"


def _normalize_externals(value) -> str:
    """
    Two-stage normalisation for extracted_externals:
    1. Type coercion — convert dict/list model outputs to a list of strings.
    2. Line format — ensure every non-empty line is in [type] name — context format.
    """
    if value is None:
        return ""

    # Stage 1: coerce to list of raw lines
    if isinstance(value, str):
        raw_lines = value.splitlines()
    elif isinstance(value, list):
        raw_lines = [str(item) for item in value if item]
    elif isinstance(value, dict):
        # e.g. {"Figma": "tool"} → already produces correct format
        raw_lines = [f"[{str(v).lower()}] {k}" for k, v in value.items()]
    else:
        raw_lines = str(value).splitlines()

    # Stage 2: normalise each line
    formatted = [_format_externals_line(line) for line in raw_lines]
    return "\n".join(line for line in formatted if line)


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
