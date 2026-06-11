"""Deterministic branch (stage 5) — Imported → Tagged for extract=no collections,
with no transcript/OCR and no semantic LLM. Tags are the slugified union of the item's
collection names; title is either a pure template ({collection} — {author}) or, in
`llm` mode, generated from caption+collection+author via a thin parallel prepare/apply
that copies the claude-code file contract (it does NOT reuse enrich's vocab-coupled
prepare/apply). summary/externals stay None (Data Integrity)."""

import json
import logging
import re
import time
from pathlib import Path

from insta_save.adapters import notion
from insta_save.adapters.notion import get_page_content, write_deterministic
from insta_save.backends import claude_code as backend
from insta_save.orchestrator.runner import PRIORITY_BUCKETS, run_priority_stage

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


# ---------------------------------------------------------------------------
# llm title mode — thin prepare/apply copying the claude-code file contract
# (does NOT reuse enrich's vocab-coupled code)
# ---------------------------------------------------------------------------

def _det_dir(env) -> Path:
    d = Path(env.tmp_dir) / "deterministic"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _title_item_block(item) -> str:
    lines = [f"--- {item.get('source_id') or item['page_id']} ---",
             f"page_id:    {item['page_id']}",
             f"source_id:  {item.get('source_id') or '[none]'}",
             f"Author:     {item.get('author') or '[none]'}",
             f"Collection: {', '.join(item.get('collections') or []) or '[none]'}"]
    if item.get("caption"):
        lines.append(f"Caption:    {item['caption']}")
    return "\n".join(lines)


def build_title_prompt(items, template, language) -> str:
    """Title-only prompt: header (with {language} filled) + per-item content. No vocab."""
    lines = [template.replace("{language}", language), "=" * 60, ""]
    for item in items:
        lines.append(_title_item_block(item))
        lines.append("")
    return "\n".join(lines)


def _deterministic_stubs(env, group, collections_cfg):
    """Imported items in priority order, restricted to the deterministic branch (all
    collections extract=no) and optionally to one group."""
    for bucket in PRIORITY_BUCKETS:
        for stub in notion.query_by_status_and_priority(env, "Imported", bucket):
            cols = stub.get("collections", [])
            if collections_cfg.is_extract_path(cols):
                continue
            if group is not None and not any(collections_cfg.group_of(c) == group for c in cols):
                continue
            yield stub


def prepare(env, *, group, collections_cfg, language, max_items=None, progress=None) -> dict:
    """llm-mode prepare. Caption-bearing items → batch.json (tags precomputed) + prompt.txt
    for a Claude session. Caption-less items need no LLM → finalized immediately with a
    template title. Returns {batched, finalized_template}."""
    d = _det_dir(env)
    template = Path(f"prompts/{PROMPT_VERSION}.txt").read_text(encoding="utf-8")
    batch_items, finalized = [], 0
    for stub in _deterministic_stubs(env, group, collections_cfg):
        if max_items is not None and len(batch_items) >= max_items:
            break
        content = get_page_content(env, stub["page_id"])
        tags = deterministic_tags(content.get("collections", []))
        if content.get("caption"):
            batch_items.append({
                "page_id": content["page_id"],
                "source_id": content.get("source_id"),
                "author": content.get("author"),
                "collections": content.get("collections", []),
                "caption": content.get("caption"),
                "tags": tags,
            })
        else:
            write_deterministic(env, content["page_id"], template_title(content), tags,
                                DETERMINISTIC_VERSION)
            finalized += 1
    if batch_items:
        (d / "batch.json").write_text(
            json.dumps({"group": group, "language": language, "items": batch_items},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "prompt.txt").write_text(
            build_title_prompt(batch_items, template, language), encoding="utf-8")
    log.info("deterministic.prepare: %d batched, %d finalized (no caption) for group %s",
             len(batch_items), finalized, group)
    return {"batched": len(batch_items), "finalized_template": finalized}


def apply(env, *, progress=None) -> dict:
    """Read results.json, write each batch item → Tagged (LLM title, or template title
    when a result is missing — no item left behind). Tags come precomputed from
    batch.json. Cleans tmp on full success. Returns {written, failed}."""
    d = _det_dir(env)
    batch_file, results_file = d / "batch.json", d / "results.json"
    if not results_file.exists():
        raise FileNotFoundError(
            f"{results_file} not found — have a Claude session write results from {d / 'prompt.txt'} first")
    batch = json.loads(batch_file.read_text(encoding="utf-8"))
    results_by_id = {r.get("page_id"): r for r in backend.parse_results(results_file)}
    counts = {"written": 0, "failed": 0}
    bar = progress.add_bar(f"Deterministic → Tagged · {batch.get('group')}",
                           total=len(batch["items"])) if progress else None
    for item in batch["items"]:
        page_id = item["page_id"]
        result = results_by_id.get(page_id)
        title = (result or {}).get("title") or template_title(item)
        if progress:
            progress.set_current("deterministic", item.get("source_id") or page_id)
        try:
            write_deterministic(env, page_id, title, item.get("tags") or [], DETERMINISTIC_VERSION)
            counts["written"] += 1
            if progress:
                progress.bump("written")
            time.sleep(env.notion_write_delay)
        except Exception as exc:
            log.error("deterministic.apply: failed %s — %s", item.get("source_id") or page_id, exc)
            counts["failed"] += 1
            if progress:
                progress.bump("failed")
        if progress:
            progress.advance(bar)
    if counts["failed"] == 0 and counts["written"] > 0:
        for f in (batch_file, d / "prompt.txt", results_file):
            if f.exists():
                f.unlink()
        log.info("deterministic.apply: cleaned tmp files")
    return counts
