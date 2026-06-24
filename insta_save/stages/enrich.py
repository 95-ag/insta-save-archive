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
from insta_save.adapters.notion import (get_page_content, mark_failed,
                                        query_by_status_and_priority, write_enrichment)
from insta_save.backends import prompt
from insta_save.backends.base import (TerminalBackendError, is_terminal_error,
                                       parse_results)
from insta_save.backends.sanitize import scrub_fabricated
from insta_save.config.tags import allowed_topics, union_topics
from insta_save.helpers import observability
from insta_save.orchestrator import guardrails, run_control
from insta_save.orchestrator.runner import PRIORITY_BUCKETS

log = logging.getLogger(__name__)

_FILL_ATTEMPTS = 3       # 1 initial + 2 retries for a transient fill failure
_RETRY_BACKOFF_S = 2.0   # linear backoff: attempt N waits N * this before retrying


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
                if collections_cfg.enrich_group(stub.get("collections", [])) == group:
                    yield stub


def prepare(env, *, group, collections_cfg, vocab, char_budget, max_items, statuses,
            prompt_template, kinds=None, image_token_budget=None, output_language="english",
            progress=None) -> int:
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
    # batch_groups is computed incrementally as items are admitted; used for union vocab.
    # Start with a placeholder — header_len is re-computed once items are known.
    img_total = 0
    bar = progress.add_bar(f"Enrich prepare · {group}", total=max_items) if progress else None

    # Seed total with the single-group header length (conservative estimate).
    # For a cross-group batch the actual prompt.txt uses the union vocab header, which is
    # slightly longer — so total underestimates marginally (acceptable soft-cap; the rendered
    # prompt may marginally exceed char_budget, but raw_extraction is unaffected).
    total = prompt.header_len(group, vocab, prompt_template, output_language)

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

    # Compute the ordered union of extract groups across all admitted items (§7.3).
    # This drives the union vocab in the prompt. For a single-group batch this equals [group].
    seen_groups: set[str] = set()
    batch_groups: list[str] = []
    for g in collections_cfg.groups:
        for item in items:
            item_groups = collections_cfg.extract_groups_of(item.get("collections", []))
            if g in item_groups and g not in seen_groups:
                seen_groups.add(g)
                batch_groups.append(g)

    d = _enrich_dir(env)
    (d / "batch.json").write_text(
        json.dumps({"group": group, "items": items}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (d / "prompt.txt").write_text(
        prompt.build_prompt(group, items, vocab, prompt_template, output_language,
                            groups=batch_groups),
        encoding="utf-8")
    log.info("enrich.prepare: wrote %d items (~%d prompt chars) for group %s (batch_groups=%s)",
             len(items), total, group, batch_groups)
    return len(items)


def apply(env, *, vocab, model, collections_cfg, progress=None) -> dict:
    """Read results.json, validate tags vs the locked vocab, write each to Notion.
    Reads batch.json for the group (-> vocab axis + enrich_version). Cleans tmp on
    full success. Returns {written, failed}. Optional `progress` (StageProgress)
    shows a live per-item bar.

    collections_cfg: used to resolve each result item's extract groups for per-item
    union-vocab validation (§7.3). Cross-group items carry topics from multiple groups;
    validating only against the batch group's allowed_topics would strip valid tags."""
    d = _enrich_dir(env)
    batch_file, results_file = d / "batch.json", d / "results.json"
    if not results_file.exists():
        raise FileNotFoundError(
            f"{results_file} not found — have a Claude session write results from {d / 'prompt.txt'} first")

    batch = json.loads(batch_file.read_text(encoding="utf-8"))
    batch_source = {
        i["page_id"]: " ".join(filter(None, (i.get("caption"), i.get("transcript"),
                                             i.get("ocr_text"))))
        for i in batch.get("items", [])
    }
    group = batch["group"]
    version = f"{model}/{env.enrich_version}/{group}"

    # Build a page_id -> collections lookup from the batch so apply can resolve
    # each item's groups without an extra Notion read.
    batch_collections: dict[str, list[str]] = {
        i["page_id"]: i.get("collections", []) for i in batch.get("items", [])
    }

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

        # Per-item union vocab: a cross-group item's granular topics span multiple groups.
        # Fall back to single-group allowed_topics when the item isn't in the batch map
        # (defensive; shouldn't happen in normal operation).
        item_collections = batch_collections.get(page_id)
        if item_collections is not None:
            item_groups = collections_cfg.extract_groups_of(item_collections)
            topics_allowed = union_topics(vocab, item_groups) if item_groups else allowed_topics(vocab, group)
        else:
            topics_allowed = allowed_topics(vocab, group)

        content_type, topics = enrich_schema.validate_item(
            item, vocab.content_types, topics_allowed)
        src_text = batch_source.get(page_id, "")
        summary, sum_removed = scrub_fabricated(item.get("summary"), src_text)
        externals, ext_removed = scrub_fabricated(item.get("externals") or "", src_text)
        if sum_removed or ext_removed:
            log.info("enrich.apply: scrubbed fabricated tokens from %s — summary=%s externals=%s",
                     sid, sum_removed, ext_removed)
        fields = {
            "title": item.get("title"),
            "summary": summary,
            "externals": externals,
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


# ---------------------------------------------------------------------------
# Reusable automated drain — used by the sequencer (run_first_time/run_incremental)
# ---------------------------------------------------------------------------

def _fill_with_retry(env, run_cfg, backend, enrich_dir, group, lane, batch_no, *,
                     sleep=time.sleep) -> bool:
    """Run backend.fill, retrying transient failures up to _FILL_ATTEMPTS.

    Returns True on success, False when transient retries are exhausted. Raises
    TerminalBackendError immediately on a terminal failure (usage limit / auth) —
    retrying those within this run is pointless. `sleep` is injectable for tests."""
    name = getattr(backend, "NAME", "backend")
    for attempt in range(1, _FILL_ATTEMPTS + 1):
        run_control.checkpoint()
        try:
            backend.fill(env, run_cfg, enrich_dir)
            return True
        except Exception as exc:  # noqa: BLE001 — classify, then retry or stop
            if is_terminal_error(exc):
                raise TerminalBackendError(
                    f"{name} hit a terminal error during enrich "
                    f"(group {group}, lane {lane}): {exc}") from exc
            log.warning("drain_enrich_group: fill attempt %d/%d failed (transient) for "
                        "group %s lane=%s batch %d — %s",
                        attempt, _FILL_ATTEMPTS, group, lane, batch_no, exc)
            if attempt < _FILL_ATTEMPTS:
                sleep(_RETRY_BACKOFF_S * attempt)
    return False


def _mark_batch_failed(env, enrich_dir, *, reason) -> int:
    """Mark every item in the current batch.json as Failed so the next prepare advances
    past them (a persistently-malformed batch must not strand the whole lane). Returns
    the number of items marked. Recoverable later via `isa status --retry-failed`."""
    batch = json.loads((Path(enrich_dir) / "batch.json").read_text(encoding="utf-8"))
    items = batch.get("items", [])
    note = f"enrich fill failed after {_FILL_ATTEMPTS} attempts: {reason[:300]}"
    for it in items:
        mark_failed(env, it["page_id"], note)
    log.warning("drain_enrich_group: marked %d items Failed after exhausting fill retries "
                "(recover with: isa status --retry-failed)", len(items))
    return len(items)


def drain_enrich_group(env, run_cfg, collections_cfg, vocab, backend, group, *,
                       lanes: list | None = None,
                       progress_factory=None, sleep=time.sleep) -> dict:
    """Drain a single group via the automated backend.

    Loops prepare→fill→apply per lane until the lane is DRAINED (prepare returns 0)
    or no-progress (apply writes nothing). Only runs the vision lane when
    ``backend.VISION_CAPABLE`` is True.

    Args:
        env:             EnvConfig.
        run_cfg:         RunConfig (used for budgets, model, output_language).
        collections_cfg: CollectionsConfig.
        vocab:           Vocab with locked topics for the group.
        backend:         Backend module (must have .AUTOMATED True; callers should not
                         call this for agent-filled backends).
        group:           Group name to drain.
        lanes:           Optional list of lane names to run (e.g. ``["text"]``). None
                         (default) runs all applicable lanes (text always; vision if
                         ``backend.VISION_CAPABLE`` is True). Explicit list restricts
                         which lanes execute — useful when the CLI runs a single lane.
        progress_factory: Optional callable ``(label: str) -> context manager`` returning
                         a StageProgress. Pass None to skip live progress bars.

    Returns:
        dict with keys ``written`` (total items written), ``lanes`` (per-lane totals),
        and ``stop_reason`` ("drained" | "no_progress") for each lane.
    """
    budgets = backend.batch_budgets(run_cfg)
    enrich_dir = Path(env.tmp_dir) / "enrich"

    text_template = Path("prompts/enrich_v2.0.txt").read_text(encoding="utf-8")
    vision_template = Path("prompts/enrich_vision_v2.0.txt").read_text(encoding="utf-8") \
        if backend.VISION_CAPABLE else None

    all_lanes = [
        {
            "name": "text",
            "kinds": {"Reel", "IGTV"},
            "template": text_template,
            "image_budget": None,
        },
    ]
    if backend.VISION_CAPABLE:
        all_lanes.append({
            "name": "vision",
            "kinds": {"Carousel", "Post"},
            "template": vision_template,
            "image_budget": budgets.image_token_budget,
        })

    # Filter to the requested subset; None means "all applicable lanes" (default).
    lane_filter = set(lanes) if lanes is not None else None
    active_lanes = [l for l in all_lanes if lane_filter is None or l["name"] in lane_filter]

    totals: dict[str, object] = {"written": 0, "failed": 0, "lanes": {}}

    for lane in active_lanes:
        lane_name = lane["name"]
        lane_written = 0
        lane_failed = 0
        stop_reason = "drained"

        n_batch = 0
        while True:
            run_control.checkpoint()
            n_batch += 1
            progress_ctx = progress_factory(f"Enrich prepare · {lane_name}") \
                if progress_factory else _NullContext()
            with progress_ctx as progress:
                n = prepare(env, group=group, collections_cfg=collections_cfg, vocab=vocab,
                            char_budget=budgets.char_budget, max_items=budgets.max_items,
                            statuses=["Extracted"], prompt_template=lane["template"],
                            kinds=lane["kinds"], image_token_budget=lane["image_budget"],
                            output_language=run_cfg.output_language, progress=progress)
            if n == 0:
                log.info("drain_enrich_group: %s lane=%s DRAINED", group, lane_name)
                stop_reason = "drained"
                break

            # Per-batch spend gate: estimate cost from the rendered prompt before calling
            # the model. Only reads prompt.txt when the api backend is active and a cap is
            # set — no-op (and no file read) for all other backends. Gates EACH batch against
            # the cap (conservative per-batch pre-fill estimate), not cumulative run spend —
            # consistent with the soft-cap philosophy.
            if (getattr(run_cfg.enrich, "backend", None) == "api"
                    and getattr(run_cfg, "guardrails_max_spend_usd", None) is not None):
                char_total = len((enrich_dir / "prompt.txt").read_text(encoding="utf-8"))
                guardrails.check_spend_cap(char_total, run_cfg)

            with observability.spinner(
                f"Enrich fill · {group} · {lane_name} · batch {n_batch} · {lane_written} written"
            ):  # TTY-guarded: animates live, silent under pytest
                ok = _fill_with_retry(env, run_cfg, backend, enrich_dir, group, lane_name,
                                      n_batch, sleep=sleep)
            if not ok:
                # Transient retries exhausted: mark this batch Failed and advance the lane
                # (the next prepare won't re-select Failed items). Never abandon the lane.
                n_failed = _mark_batch_failed(env, enrich_dir, reason="malformed model output")
                lane_failed += n_failed
                totals["failed"] = totals["failed"] + n_failed
                continue

            progress_ctx = progress_factory(f"Enrich apply · {lane_name}") \
                if progress_factory else _NullContext()
            with progress_ctx as progress:
                counts = apply(env, vocab=vocab, model=run_cfg.enrich.model,
                               collections_cfg=collections_cfg, progress=progress)

            lane_written += counts["written"]
            totals["written"] = totals["written"] + counts["written"]

            if counts["written"] == 0:
                # No-progress guard: items stay Extracted, next prepare re-selects them.
                # Break to avoid an infinite loop; caller/logs should surface the failure.
                log.warning(
                    "drain_enrich_group: no items applied for group %s lane=%s — "
                    "stopping to avoid a no-progress loop (check logs; resolve failures, "
                    "then re-run)",
                    group, lane_name,
                )
                stop_reason = "no_progress"
                break

        totals["lanes"][lane_name] = {"written": lane_written, "failed": lane_failed,
                                      "stop_reason": stop_reason}

    return totals


class _NullContext:
    """No-op context manager returned when progress_factory is None."""
    def __enter__(self):
        return None
    def __exit__(self, *a):
        return False
