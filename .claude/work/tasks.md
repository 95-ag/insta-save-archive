# Tasks

## Active Plan

`/home/ag-95/.claude/plans/2026-06-05-repo-restructure.md`

---

## Repo Restructure + Privacy Scrub

### Pre-refactor baseline
- [ ] P1: Baseline check — import/help on each working script
- [ ] P2: Commit 0 — pending enrichment_local.py, notion.py, run_enrichment_local.py

### Commit 1 — pyproject + scaffold
- [ ] T1: Create `pyproject.toml`
- [ ] T2: Create `pipeline/__init__.py`
- [ ] T3: `pip install -e .` — verify `from pipeline.config import load_config` works
- [ ] Commit 1: `chore: add pyproject.toml for editable install and pipeline package scaffold`

### Commit 2 — library modules
- [ ] T4: `pipeline/config.py`
- [ ] T5: `pipeline/notion.py`
- [ ] T6: `pipeline/session.py`
- [ ] T7: `pipeline/crawler.py`
- [ ] T8: `pipeline/extractor.py`
- [ ] T9: `pipeline/extractor_deep.py`
- [ ] T10: `pipeline/ingest.py` — ingest_with_context only, strip run() + argparse
- [ ] T11: `pipeline/queue_runner.py`
- [ ] T12: `pipeline/enrich_claude.py` (was enrichment.py)
- [ ] T13: `pipeline/enrich_local.py` (was enrichment_local.py)
- [ ] T14: `pipeline/display.py`
- [ ] T15: `pipeline/collections.py` — loads JSON, keeps all functions
- [ ] Commit 2: `refactor: move library modules into pipeline package`

### Commit 3 — scripts
- [ ] T16: `scripts/ingest.py` — run() + argparse, imports from pipeline.*
- [ ] T17: `scripts/ingest_batch.py`
- [ ] T18: `scripts/list_collections.py` — add --update flag
- [ ] T19: `scripts/queue_pilot.py`
- [ ] T20: `scripts/run_extraction.py`
- [ ] T21: `scripts/run_enrichment.py`
- [ ] T22: `scripts/run_enrichment_local.py`
- [ ] Commit 3: `refactor: move CLI scripts into scripts directory`

### Commit 4 — collections JSON + gitignore
- [ ] T23: `config/collections.example.json`
- [ ] T24: `config/collections.json` (gitignored, from current COLLECTIONS)
- [ ] T25: Add `config/collections.json` to `.gitignore`
- [ ] T26: Verify `python scripts/list_collections.py --update` merges without clobbering
- [ ] Commit 4: `feat: move collections data to gitignored JSON, add example template`

### Commit 5 — delete old root files
- [ ] T27: git rm all 18 original root .py files
- [ ] Commit 5: `refactor: remove root-level files superseded by pipeline/ and scripts/`

### Post-refactor verification
- [ ] V1: `python scripts/ingest_batch.py --dry-run` — correct order
- [ ] V2: `python scripts/run_enrichment_local.py --help`
- [ ] V3: `python scripts/run_enrichment_local.py --dry-run --limit 1`
- [ ] V4: `python scripts/queue_pilot.py --dry-run --all-pilot`
- [ ] V5: `python scripts/run_extraction.py --help`
- [ ] V6: `python -c "from pipeline.collections import ordered_for_ingestion; print(len(ordered_for_ingestion()))"` → 43

### Commit 6 — docs scrub
- [ ] D1: README.md — replace collection names, update paths to scripts/
- [ ] D2: .claude/work/session.md — remove collection registry
- [ ] D3: .claude/work/tasks.md — remove collection names from operational section
- [ ] D4: .claude/work/lessons.md — add collections JSON lesson
- [ ] PV1: `git grep "<collection name>"` → zero results
- [ ] PV2: `git check-ignore -v config/collections.json` → confirmed gitignored
- [ ] Commit 6: `docs: scrub private collection names and update paths for new structure`

---

## Previously Completed

### Split Enrichment — Cluster 1 DONE, Cluster 2 DEFERRED until after restructure
Plan: `/home/ag-95/.claude/plans/2026-06-05-split-enrichment-local-claude.md`
- [x] Ollama setup (qwen2.5:7b, GPU confirmed, ~2.5GB VRAM)
- [x] T1: config.py + requirements.txt (ollama fields)
- [x] T2: enrichment_local.py (prompt, normalization, tool_use)
- [x] T3: notion.py (write_local_enrichment, status transitions Enriched/Summarised)
- [x] T4: run_enrichment_local.py
- [x] T5: Verified dry-run + live write + skip logic
- [x] T6: Commit cluster 1 (faa4703)
- [ ] T7–T13: Cluster 2 (Claude Code pass) — implement AFTER restructure in scripts/

### Batch Ingest + Phase 3 Infra — COMPLETE
- [x] A1–A3: Collections registry, batch ingest, all 43 collections ingested
- [x] B1–B2: Queue pilot, 155 items Expanded
- [x] C1–C5: Enrichment infra built
