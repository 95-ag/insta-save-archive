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
from collections import OrderedDict
from math import ceil
from pathlib import Path

from insta_save.adapters.notion import get_page_content, query_by_status_and_priority
from insta_save.orchestrator.runner import PRIORITY_BUCKETS

log = logging.getLogger(__name__)

_MIN_PER_COLL = 3
_MAX_PER_COLL = 10
_RATIO = 0.25
_GLOBAL_MAX = 40


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


def _balanced_sample(stubs, group, collections_cfg, *, cap, per_collection):
    """Round-robin across the group's collections so each is represented regardless of size.
    Dedup by page_id; a stub in several group-collections is filed under its first (the item's
    collection order). Priority order is preserved within each collection bucket.

    When `per_collection` is True, each bucket is sub-capped at
    ``n_c = clamp(ceil(len(bucket) * _RATIO), _MIN_PER_COLL, _MAX_PER_COLL)`` — so large
    collections don't crowd out small ones and the result scales with actual content volume.
    When False (explicit `limit` path), no per-bucket sub-cap is applied (old behaviour).
    `cap` is the global ceiling on the returned list."""
    buckets, seen = OrderedDict(), set()
    for stub in stubs:
        pid = stub["page_id"]
        if pid in seen:
            continue
        seen.add(pid)
        gcols = [c for c in stub.get("collections", []) if collections_cfg.group_of(c) == group]
        buckets.setdefault(gcols[0] if gcols else group, []).append(stub)

    # Per-collection sub-caps (size-aware path only).
    if per_collection:
        quotas = {k: min(len(v), max(_MIN_PER_COLL, min(_MAX_PER_COLL, ceil(len(v) * _RATIO))))
                  for k, v in buckets.items()}
    else:
        quotas = None

    out = []
    used = {k: 0 for k in buckets}
    while True:
        # One round: take one item from each bucket that still has quota and items.
        added_this_round = 0
        for k in list(buckets):
            if len(out) >= cap:
                return out
            if quotas is not None and used[k] >= quotas[k]:
                continue
            if not buckets[k]:
                continue
            out.append(buckets[k].pop(0))
            used[k] += 1
            added_this_round += 1
        if added_this_round == 0:
            # No bucket could contribute (all exhausted or at quota) — done.
            break
    return out


def sample(env, *, group, collections_cfg, limit, statuses, prompt_template, progress=None) -> int:
    """Collect a sample of items for the group across `statuses` (priority order),
    write sample.json + prompt.txt. Returns the sample size.

    `limit=None` — size-aware adaptive path: per-collection quota
    ``n_c = clamp(ceil(size × _RATIO), _MIN_PER_COLL, _MAX_PER_COLL)``
    with a global cap of `_GLOBAL_MAX`. Small collections are always represented; large
    collections contribute proportionally more samples for richer vocab reasoning.

    `limit=int` — explicit fixed cap (old behaviour, no per-collection quota); used by
    tests and any caller that wants deterministic sizing.

    Callers pass the extract output status (["Extracted"]); the param stays a list to keep
    the query seam flexible. Optional `progress` (StageProgress) shows a live sample bar."""
    if limit is None:
        ordered = _balanced_sample(
            _group_stubs(env, statuses, group, collections_cfg),
            group, collections_cfg,
            cap=_GLOBAL_MAX, per_collection=True,
        )
    else:
        ordered = _balanced_sample(
            _group_stubs(env, statuses, group, collections_cfg),
            group, collections_cfg,
            cap=limit, per_collection=False,
        )

    items = []
    bar = progress.add_bar(f"Calibrate sample · {group}", total=len(ordered)) if progress else None
    for stub in ordered:
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
