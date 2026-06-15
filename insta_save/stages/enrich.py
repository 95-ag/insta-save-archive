# insta_save/stages/enrich.py
"""Enrich stage — one-shot title+summary+externals+tags via the claude-code
backend. prepare() builds a budget-bounded batch for one group; apply() validates
tags against the locked vocab and writes Notion (-> Tagged).

Batch-oriented (one Claude session per batch), mirroring legacy/scripts/summarize.py.
Per-item inline backends (local/api) will use orchestrator.runner instead (later)."""

import json
import logging
import time
from pathlib import Path

from insta_save import enrich_schema
from insta_save.adapters.notion import (get_page_content, query_by_status_and_priority,
                                        write_enrichment)
from insta_save.backends import prompt
from insta_save.backends.base import parse_results
from insta_save.config.tags import allowed_topics
from insta_save.orchestrator.runner import PRIORITY_BUCKETS

log = logging.getLogger(__name__)


def _enrich_dir(env) -> Path:
    d = Path(env.tmp_dir) / "enrich"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ordered_group_stubs(env, statuses, group, collections_cfg, kinds=None):
    """Stubs across input statuses, priority order, filtered to the group.

    kinds: optional set of type strings (e.g. {"Carousel", "Post"}) — when set,
    only stubs whose `type` is in the set are yielded. None admits all types."""
    for status in statuses:
        for bucket in PRIORITY_BUCKETS:
            for stub in query_by_status_and_priority(env, status, bucket):
                if kinds is not None and stub.get("type") not in kinds:
                    continue
                if any(collections_cfg.group_of(c) == group for c in stub.get("collections", [])):
                    yield stub


def prepare(env, *, group, collections_cfg, vocab, char_budget, max_items, statuses,
            prompt_template, kinds=None, image_token_budget=None, progress=None) -> int:
    """Build batch.json + prompt.txt for the highest-priority budget-worth of the
    group's items. Returns the batch size (0 = nothing left). Optional `progress`
    (StageProgress) shows a live per-item fetch bar.

    char_budget bounds the RENDERED prompt length (header + vocab + per-item
    scaffolding + content) — i.e. what the session actually reads — not just raw
    content. The first matching item is always admitted even if it alone is large.

    kinds: optional set of type strings — restricts which post types are admitted
    (useful to separate text-only and vision lanes in the same group).

    image_token_budget: optional cap on the total estimated image tokens in the batch
    (sum of slide_images * PER_SLIDE_IMAGE_TOKENS). The first item is always admitted;
    subsequent items break the loop when this budget would be exceeded."""
    items = []
    total = prompt.header_len(group, vocab, prompt_template)
    img_total = 0
    bar = progress.add_bar(f"Enrich prepare · {group}", total=max_items) if progress else None
    for stub in _ordered_group_stubs(env, statuses, group, collections_cfg, kinds=kinds):
        if max_items is not None and len(items) >= max_items:
            break
        content = get_page_content(env, stub["page_id"])
        block = prompt.item_len(content)
        img = prompt.image_token_estimate(content)
        over_chars = total + block > char_budget
        over_images = image_token_budget is not None and img_total + img > image_token_budget
        if items and (over_chars or over_images):
            break
        items.append(content)
        total += block
        img_total += img
        if progress:
            progress.set_current("fetch", content.get("source_id") or content["page_id"])
            progress.bump("fetched"); progress.advance(bar)

    if not items:
        log.info("enrich.prepare: no items left for group %s", group)
        return 0

    d = _enrich_dir(env)
    (d / "batch.json").write_text(
        json.dumps({"group": group, "items": items}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (d / "prompt.txt").write_text(
        prompt.build_prompt(group, items, vocab, prompt_template), encoding="utf-8")
    log.info("enrich.prepare: wrote %d items (~%d prompt chars) for group %s", len(items), total, group)
    return len(items)


def apply(env, *, vocab, model, progress=None) -> dict:
    """Read results.json, validate tags vs the locked vocab, write each to Notion.
    Reads batch.json for the group (-> vocab axis + enrich_version). Cleans tmp on
    full success. Returns {written, failed}. Optional `progress` (StageProgress)
    shows a live per-item bar."""
    d = _enrich_dir(env)
    batch_file, results_file = d / "batch.json", d / "results.json"
    if not results_file.exists():
        raise FileNotFoundError(
            f"{results_file} not found — have a Claude session write results from {d / 'prompt.txt'} first")

    batch = json.loads(batch_file.read_text(encoding="utf-8"))
    group = batch["group"]
    version = f"{model}/{env.enrich_version}/{group}"
    topics_allowed = allowed_topics(vocab, group)

    results = parse_results(results_file)
    counts = {"written": 0, "failed": 0}
    bar = progress.add_bar(f"Enrich → Tagged · {group}", total=len(results)) if progress else None
    for item in results:
        page_id = item.get("page_id")
        sid = item.get("source_id") or page_id
        if progress:
            progress.set_current("enrich", sid or "?")
        if not page_id or not item.get("summary"):
            log.warning("enrich.apply: %s missing page_id/summary — skipping", sid)
            counts["failed"] += 1
            if progress:
                progress.bump("failed"); progress.advance(bar)
            continue
        content_type, topics = enrich_schema.validate_item(
            item, vocab.content_types, topics_allowed)
        fields = {
            "title": item.get("title"),
            "summary": item.get("summary"),
            "externals": item.get("externals") or "",
            "tags": enrich_schema.tags_for(content_type, topics),
        }
        try:
            write_enrichment(env, page_id, fields, version)
            counts["written"] += 1
            if progress:
                progress.bump("written")
            time.sleep(env.notion_write_delay)
        except Exception as exc:
            log.error("enrich.apply: failed %s — %s", sid, exc)
            counts["failed"] += 1
            if progress:
                progress.bump("failed")
        if progress:
            progress.advance(bar)

    if counts["failed"] == 0 and counts["written"] > 0:
        for f in (batch_file, d / "prompt.txt", results_file):
            if f.exists():
                f.unlink()
        log.info("enrich.apply: cleaned tmp files")
    return counts
