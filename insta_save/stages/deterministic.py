"""Deterministic branch (stage 5) — Imported → Tagged for extract=no collections,
with no transcript/OCR and no semantic LLM. Tags are the slugified union of the item's
collection names; title is either a pure template ({collection} — {author}) or, in
`llm` mode, generated from caption+collection+author via a thin parallel prepare/apply
that copies the claude-code file contract (it does NOT reuse enrich's vocab-coupled
prepare/apply). summary/externals stay None (Data Integrity)."""

import re

DETERMINISTIC_VERSION = "deterministic-v2.0"
PROMPT_VERSION = "deterministic_title_v2.0"


def slugify_collection(name: str) -> str:
    """Kebab-case a collection name into one tag: lowercase, runs of non-alphanumerics
    → '-', strip leading/trailing '-'. 'Plants & Pets' → 'plants-pets'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def deterministic_tags(collections) -> list[str]:
    """Sorted, de-duped union of slugified collection names; empties dropped."""
    return sorted({s for c in collections if (s := slugify_collection(c))})
