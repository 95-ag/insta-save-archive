"""Deterministic branch (stage 5) — Imported → Tagged for extract=no collections,
with no transcript/OCR and no semantic LLM. Tags are the slugified union of the item's
collection names; title is either a pure template ({collection} — {author}) or, in
`llm` mode, generated from caption+collection+author via a thin parallel prepare/apply
that copies the claude-code file contract (it does NOT reuse enrich's vocab-coupled
prepare/apply). summary/externals stay None (Data Integrity)."""

import logging
import re

from insta_save.adapters.notion import write_deterministic
from insta_save.orchestrator.runner import run_priority_stage

log = logging.getLogger(__name__)

DETERMINISTIC_VERSION = "deterministic-v2.0"
PROMPT_VERSION = "deterministic_title_v2.0"


def slugify_collection(name: str) -> str:
    """Kebab-case a collection name into one tag: lowercase, runs of non-alphanumerics
    → '-', strip leading/trailing '-'. 'Plants & Pets' → 'plants-pets'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def deterministic_tags(collections) -> list[str]:
    """Sorted, de-duped union of slugified collection names; empties dropped."""
    return sorted({s for c in collections if (s := slugify_collection(c))})


def template_title(item) -> str:
    """Pure title: '{primary_collection} — {author}'. primary = alphabetically-first
    collection (deterministic + stable across reruns). Fallbacks: no author → collection;
    no collection → keep the existing placeholder title, else source_id, else ''."""
    collections = sorted(item.get("collections") or [])
    if not collections:
        return item.get("title") or item.get("source_id") or ""
    primary = collections[0]
    author = item.get("author")
    return f"{primary} — {author}" if author else primary


def _tag_item(env, item, collections_cfg) -> str:
    """Template-mode processor. Skip extract-path items (richer wins, D2); else write
    slug-tags + template title → Tagged."""
    if collections_cfg.is_extract_path(item.get("collections", [])):
        return "skipped_extract_path"
    tags = deterministic_tags(item.get("collections", []))
    write_deterministic(env, item["page_id"], template_title(item), tags, DETERMINISTIC_VERSION)
    return "tagged"


def run_deterministic_stage(env, collections_cfg, progress, *, limit=None, group=None) -> dict:
    """Drive the template (one-shot) deterministic branch over Imported items."""
    # ctx feeds collections_cfg to _tag_item; the separate collections_cfg= drives the
    # runner's --group membership filter. Both are needed.
    return run_priority_stage(
        env, "Imported", _tag_item, progress,
        ctx=collections_cfg, limit=limit, group=group, collections_cfg=collections_cfg,
        stage_key="deterministic", bar_label="Deterministic (Imported)")
