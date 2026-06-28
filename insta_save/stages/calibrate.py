# insta_save/stages/calibrate.py
"""Calibrate stage (ARCHITECTURE §7.2) — per group, first-time. Sample the group's
content so a Claude session can PROPOSE a tag vocabulary; the human refines and locks
it into the private config/tags.json. LLM proposes, human disposes (D18).

Backend-independent by design: calibrate does NOT use the enrich Backend protocol or
backend.fill(). Whatever session runs it (claude-code, Cowork, or the operator) proposes
the vocab — `run_cfg.enrich.backend` selects the enrich fill engine, not the calibrate
proposer, so calibrate behaves identically under every backend."""

import json
import logging
from pathlib import Path

from insta_save.adapters.notion import get_page_content, query_by_status_and_priority
from insta_save.orchestrator.runner import PRIORITY_BUCKETS

log = logging.getLogger(__name__)


def _calibrate_dir(env) -> Path:
    d = Path(env.tmp_dir) / "calibrate"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _build_prompt(template, group, items, collection_names) -> str:
    cols = ", ".join(sorted(collection_names)) or "(none)"
    header = template.replace("{group}", group).replace("{collections}", cols)
    lines = [header, "", "=" * 60, ""]
    for item in items:
        sid = item.get("source_id") or item["page_id"]
        lines.append(f"--- {sid} (page_id: {item['page_id']}) ---")
        for label, key in (("Caption", "caption"), ("Transcript", "transcript"),
                           ("OCR", "ocr_text")):
            if item.get(key):
                lines.append(f"{label}: {item[key]}")
        lines.append("")
    return "\n".join(lines)


def _group_stubs(env, statuses, group, collections_cfg):
    """Stubs across the given input statuses, priority order, filtered to the group."""
    for status in statuses:
        for bucket in PRIORITY_BUCKETS:
            for stub in query_by_status_and_priority(env, status, bucket):
                if any(collections_cfg.group_of(c) == group for c in stub.get("collections", [])):
                    yield stub


def sample(env, *, group, collections_cfg, limit, statuses, prompt_template, progress=None) -> int:
    """Collect up to `limit` items of the group across `statuses` (priority order),
    write sample.json + prompt.txt. Returns the sample size. Callers pass the extract
    output status (["Extracted"]); the param stays a list to keep the query seam flexible.
    Optional `progress` (StageProgress) shows a live per-item sample bar."""
    items = []
    bar = progress.add_bar(f"Calibrate sample · {group}", total=limit) if progress else None
    for stub in _group_stubs(env, statuses, group, collections_cfg):
        if len(items) >= limit:
            break
        content = get_page_content(env, stub["page_id"])
        items.append(content)
        if progress:
            progress.set_current("sample", content.get("source_id") or content["page_id"])
            progress.bump("sampled"); progress.advance(bar)

    if not items:
        log.info("calibrate.sample: no items in group %s (statuses=%s)", group, statuses)
        return 0

    d = _calibrate_dir(env)
    (d / "sample.json").write_text(
        json.dumps({"group": group, "items": items}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    names = collections_cfg.collections_in_group(group)
    (d / "prompt.txt").write_text(_build_prompt(prompt_template, group, items, names), encoding="utf-8")
    log.info("calibrate.sample: wrote %d items for group %s", len(items), group)
    return len(items)
