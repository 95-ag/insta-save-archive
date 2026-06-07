# Tasks

## Active Plan

`/home/ag-95/.claude/plans/2026-06-07-priority-stage-runner.md`
Branch: `feature-phase3-enrichment-runs`

---

## Per-item priority pipeline + shared stage runner — DONE (pending user sign-off)

### Cluster 1 — shared foundation ✅ 1775259
- [x] `pipeline/notion.py`: `query_by_status_and_priority(config, status, priority)` (None → select.is_empty)
- [x] `pipeline/runner.py` (new): `PRIORITY_BUCKETS` + `run_priority_stage(...)`
- [x] Verify: `import pipeline.runner` ok; pytest reconcile 9 passed
- [x] Commit: `feat: add priority-bucketed stage runner and status+priority query`

### Cluster 2 — expand on runner ✅ de2a2ed
- [x] `pipeline/queue_runner.py`: `run_queue` delegates to `run_priority_stage` (signature preserved)
- [x] `scripts/run_extraction.py` unchanged (confirmed)
- [x] Verify: `run_extraction.py --help` ok; import clean
- [x] Commit: `refactor: drive extraction through the priority-bucketed runner`

### Cluster 3 — enrich-local on runner ✅ f212810
- [x] `scripts/run_enrichment_local.py`: `run` delegates to runner; placeholder-skip/`--force` in process_fn
- [x] Verify: `--help` ok; live read-only bucket probe (distribution below)
- [x] Commit: `refactor: drive local enrichment through the priority-bucketed runner`

### Cluster 4 — summarize by priority bucket ✅ 981fc2c
- [x] `scripts/run_enrichment_claude_code.py`: `prepare` picks next non-empty Enriched bucket (High first); dropped `--collection`/`--list-priority` + grouping import
- [x] Verify: `--help` shows only `--prepare`/`--upload`; grep clean of `pilot_collections`
- [x] Commit: `refactor: prepare summarize batches by priority bucket and drop collection grouping`

### Cluster 5 — docs + backlog ⏳ (this commit)
- [x] `.claude/work/{session,tasks,lessons}.md`: state + deferred tasks
- [ ] Commit: `docs: record priority-stage runner model and deferred refactor tasks`

---

## Verification (end-to-end)
- [x] `processing_priority` confirmed: options exactly High/Medium/Low (no Notion 400)
- [x] imports resolve; three scripts `--help`
- [x] `pytest tests/test_reconcile.py -v` → 9 passed
- [x] grep: no grouping import in `run_enrichment_claude_code.py`
- [x] live read-only bucket probe: Queued H22/M50/L7 · Expanded H105/M9 · Enriched H14/M15/L20 · blank=0
- [ ] dry-run bucket ordering on a live processing run (user-driven, with Ollama up)
- [ ] summarize `--prepare` selects High bucket on a real run

---

## Backlog (future work — not in this change)
- [ ] T-refactor (end of Phase 3): remove dead grouping code (collections.py/queue_pilot.py/run_enrichment.py); restructure flat pipeline/ folder into stage-separated + helper layout
- [ ] T-orchestrator: single full-pipeline run file (ingest → expand → enrich → summarize) for incremental adds/removals
- [ ] O-runs: expand (79 queued) → enrich-local (114 expanded) → summarize (49 enriched), highest priority first · then spot-check 5 pages

---

## Completed (prior work)
- Ingest Sync Layer (Clusters A–H) — COMPLETE, verified
- Cluster 2 Claude Code enrichment — COMPLETE (9fda9c3)
- Repo restructure + privacy scrub — COMPLETE (31423f7)
- Split enrichment Cluster 1 — COMPLETE
