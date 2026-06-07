# Tasks

## Active Plan

`/home/ag-95/.claude/plans/2026-06-07-rename-pipeline.md`
Branch: `feature-phase3-enrichment-runs`

---

## Pipeline naming refactor — IN PROGRESS

### Cluster A — notion.py: field names, status strings, function renames ✅ cde96c7
Files: `pipeline/notion.py`
Message: `refactor: rename pipeline fields, statuses, and notion write functions`

### Cluster B — pipeline module renames ✅ 461ec71
Files: `pipeline/titler.py` (was enrich_local.py), `pipeline/extract_runner.py` (was queue_runner.py)
Message: `refactor: rename pipeline modules to titler.py and extract_runner.py`

### Cluster C — script renames + title pass decoupling ✅ 89721f1
Files: `scripts/title.py`, `scripts/summarize.py`, `scripts/extract.py`, `scripts/queue.py`
Message: `refactor: rename enrichment/extraction scripts; title pass reads Queued and Extracted`

### Cluster D — summary line break fix ✅ 83f14e3
Files: `scripts/summarize.py`
Message: `fix: instruct summary prompt to use paragraph breaks for readable Notion output`

### Cluster E — docs ✅ (uncommitted)
Files: `.claude/docs/PROJECT.md`, `.claude/docs/IMPLEMENTATION_PLAN.md`
Message: `docs: update project docs with renamed pipeline and flow diagram`

### Cluster F — work docs ⏳
Files: `.claude/work/session.md`, `.claude/work/tasks.md`, `.claude/work/lessons.md`
Message: `chore: record naming refactor and update session state`

### Notion manual steps (user, after code is deployed) ⏳
1. Rename: `pipeline_status` → `status`
2. Rename: `processing_priority` → `priority`
3. Rename: `expanded_summary` → `summary`
4. Rename: `extracted_externals` → `externals`
5. Rename status option: `Expanded` → `Extracted`
6. Rename status option: `Summarised` → `Summarized`
7. Bulk-change: filter `status = Enriched` → select all → change to `Extracted` (~49 items)
8. Delete the `Enriched` status option
9. Delete the `transcript_available` property

---

## Move extracted_externals from local → Claude — DONE ✅

### Cluster A — simplify local pass to title only ✅ 61735f7
### Cluster B — add externals to Claude pass ✅ 474fdf5

---

## JSON schema format + system prompt — DONE ✅ bb6a04b
## O1 validation run — DONE ✅
## Claude pass redesign — DONE ✅ 8ae2f4e
## Bugfix: Phase 2 type detection + no_data counter — DONE ✅ 30e2be0
## Per-item priority pipeline + shared stage runner — DONE ✅

---

## Backlog (future work — not in this change)
- [ ] O-runs: after Notion steps — run title.py (title Queued+Extracted) → then cycle summarize.py --prepare/upload for Extracted items, highest priority first → spot-check 5 pages
- [ ] T-refactor (end of Phase 3): remove dead code (old `enrich_claude.py`, `run_enrichment.py`; dead grouping in `collections.py`); restructure flat `pipeline/` into stage-separated layout; remove any remaining old-name references
- [ ] T-orchestrator: single full-pipeline run file for incremental add/remove cycles
- [ ] Tagging stage: embedding clusters for Summarized items; generic/collection tag for others
- [ ] Routing stage: config-driven route_target assignment from collection name
