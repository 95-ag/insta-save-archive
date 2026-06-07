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

from pipeline.config import Config

log = logging.getLogger(__name__)

# Output categories in display order. Each maps to a tool field name and section header.
_CATEGORIES = [
    ("tools",      "Tools"),
    ("brands",     "Brands"),
    ("creators",   "Creators"),
    ("links",      "Links"),
    ("techniques", "Techniques"),
    ("locations",  "Locations"),
]

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
                "tools": {
                    "type": "string",
                    "description": (
                        "Software, apps, or platforms explicitly mentioned. "
                        "One per line: name — what it does or how it is used. "
                        "Empty string if none.\n"
                        "Example:\nFigma — UI design tool\nCanva — quick graphics"
                    ),
                },
                "brands": {
                    "type": "string",
                    "description": (
                        "Companies, products, or services explicitly mentioned. "
                        "One per line: name — context. Empty string if none.\n"
                        "Example:\nDropbox — subject of the post\nAdobe — mentioned in comparison"
                    ),
                },
                "creators": {
                    "type": "string",
                    "description": (
                        "People or accounts referenced. "
                        "One per line: @handle or name — context. Empty string if none.\n"
                        "Example:\n@millmotion — animation style referenced"
                    ),
                },
                "links": {
                    "type": "string",
                    "description": (
                        "Websites or URLs mentioned. "
                        "One per line: url or site name — what it is. Empty string if none.\n"
                        "Example:\nbrand.dropbox.com — brand guidelines page"
                    ),
                },
                "techniques": {
                    "type": "string",
                    "description": (
                        "Methods, approaches, or frameworks explicitly shown or taught. "
                        "One per line: name — how it is used. Empty string if none.\n"
                        "Example:\ngrid overlay — used for layout alignment"
                    ),
                },
                "locations": {
                    "type": "string",
                    "description": (
                        "Physical places explicitly mentioned. "
                        "One per line: name — context. Empty string if none."
                    ),
                },
            },
            "required": ["title", "tools", "brands", "creators", "links", "techniques", "locations"],
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
    Build the grouped display string from per-category tool arguments.

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
                "extracted_externals": _assemble_externals(args),
            }

    raise RuntimeError(
        f"enrichment_local: model did not call tool for source_id={item.get('source_id')!r}. "
        f"Response: {response.message.content!r}"
    )
