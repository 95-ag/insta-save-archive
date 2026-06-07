"""
Local enrichment — title and extracted_externals via Ollama.

Uses Ollama JSON schema format for constrained structured output — the model cannot
return prose or skip fields. Prompt is inline; title and entity extraction don't
need versioned prompts.

Exposes:
  validate_local_enrichment_config  — check Ollama is reachable
  enrich_local                      — returns {"title": str, "extracted_externals": str}
"""

import json
import logging

import ollama

from pipeline.config import Config

log = logging.getLogger(__name__)

# Output categories in display order. Each maps to a schema field name and section header.
_CATEGORIES = [
    ("tools",      "Tools"),
    ("brands",     "Brands"),
    ("creators",   "Creators"),
    ("links",      "Links"),
    ("techniques", "Techniques"),
    ("locations",  "Locations"),
]

# JSON schema passed to Ollama's `format` parameter. Constrained decoding forces the model
# to emit a JSON object matching this shape — compliance failures (prose output) are impossible.
_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "title":      {"type": "string"},
        "tools":      {"type": "string"},
        "brands":     {"type": "string"},
        "creators":   {"type": "string"},
        "links":      {"type": "string"},
        "techniques": {"type": "string"},
        "locations":  {"type": "string"},
    },
    "required": ["title", "tools", "brands", "creators", "links", "techniques", "locations"],
}

_SYSTEM_PROMPT = (
    "You are a data extraction tool. "
    "Output only valid JSON matching the requested schema. "
    "Never add explanatory text, markdown, or any content outside the JSON object."
)

_PROMPT_TEMPLATE = """Extract enrichment fields from this saved Instagram post.

Author: {author} | Type: {type} | Collections: {collections}

Caption: {caption}

Transcript: {transcript}

OCR/Slides: {ocr_text}

Return a JSON object with these fields:
1. title — concise and specific (not the caption), max 80 chars
2. tools — software, apps, or platforms explicitly mentioned. One per line: name — how used
3. brands — companies, products, or services mentioned. One per line: name — context
4. creators — people or accounts referenced. One per line: @handle or name — context
5. links — websites or URLs mentioned. One per line: url — what it is
6. techniques — methods or approaches shown. One per line: name — how used
7. locations — physical places mentioned. One per line: name — context
Leave any field as empty string if nothing in that category is mentioned.
"""


def _assemble_externals(args: dict) -> str:
    """
    Build the grouped display string from per-category fields.

    Format:
        [Category]
          name — context
          name — context
        [Category]
          name — context
    """
    sections = []
    for field, label in _CATEGORIES:
        value = (args.get(field) or "").strip()
        if not value:
            continue
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if not lines:
            continue
        sections.append(f"[{label}]")
        sections.extend(f"  {line}" for line in lines)
    return "\n".join(sections)


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

    Uses Ollama JSON schema format — constrained decoding prevents the model from
    returning prose instead of structured output.

    Returns {"title": str, "extracted_externals": str}.
    Raises RuntimeError on JSON parse failure (should not occur with constrained decoding).
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
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        format=_OUTPUT_SCHEMA,
    )

    try:
        result = json.loads(response.message.content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(
            f"enrichment_local: JSON parse failed for source_id={item.get('source_id')!r}. "
            f"Response: {response.message.content!r}"
        ) from exc

    log.debug("enrichment_local: ok for %s", item.get("source_id"))
    return {
        "title": str(result.get("title") or "").strip(),
        "extracted_externals": _assemble_externals(result),
    }
