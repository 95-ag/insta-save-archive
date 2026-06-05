# Tasks

## Active Plan

`/home/ag-95/.claude/plans/2026-06-05-split-enrichment-local-claude.md`
(file paths in plan are pre-restructure — use paths in session.md Cluster 2 section)

---

## Cluster 2 — Claude Code enrichment pass

### Pre-condition: set enrichment_order in collections.json
- [ ] Manually add `"enrichment_order": N` (integer) to priority collection entries in `config/collections.json`
      (null or absent = not in Claude scope; 1 = first, 2 = second, etc.)

### Implementation
- [ ] T7: `pipeline/collections.py` — add `pilot_collections_by_enrichment_priority()` (reads enrichment_order from JSON)
- [ ] T8: `pipeline/enrich_claude.py` — narrow `_SAVE_ENRICHMENT_TOOL` to `expanded_summary + key_insights` only
- [ ] T9: `pipeline/notion.py` — remove title + extracted_externals from `write_enrichment`; update docstring
- [ ] T10: `scripts/run_enrichment_claude_code.py` — new file: `--prepare`, `--upload`, `--list-priority`; queries Enriched items
- [ ] T11: `scripts/run_enrichment.py` — add `--collection` flag; change queries from Expanded → Enriched; add priority ordering
- [ ] Verify T12: `--list-priority` shows correct order; `--prepare --collection X` creates tmp/enrichment_prompt.txt
- [ ] Verify T13: hand-craft 1-item results.json → `--upload` writes summary+insights; title+externals untouched
- [ ] Commit: `feat: add Claude Code enrichment pass for summary and insights`
      Files: `pipeline/collections.py`, `pipeline/enrich_claude.py`, `pipeline/notion.py`,
             `scripts/run_enrichment_claude_code.py`, `scripts/run_enrichment.py`
- [ ] Commit docs: `docs: update tasks and session after cluster 2`
      Files: `.claude/work/tasks.md`, `.claude/work/session.md`, `.claude/work/lessons.md`

---

## Operational runs

- [ ] O1: `python scripts/run_enrichment_local.py 2>&1 | tee /tmp/local_enrichment.log`
      All 155 Expanded items → title + extracted_externals → status: Enriched
      Run overnight. Re-runnable (skips Enriched items).
- [ ] O2: Set `enrichment_order` in `config/collections.json` for priority collections
- [ ] O3: Per priority collection (in enrichment_order): `--prepare --collection X` → Claude reads prompt → `--upload`
- [ ] O4: Spot-check 5 Notion pages — title real, externals formatted, summary substantive, insights actionable

---

## Completed

### Repo Restructure + Privacy Scrub — COMPLETE
Plan: `/home/ag-95/.claude/plans/2026-06-05-repo-restructure.md`
- [x] Baseline verify (all scripts working pre-restructure)
- [x] Commit 0: enrichment format + status transitions
- [x] Commit 1: pyproject.toml + pipeline scaffold
- [x] Commit 2: 12 library modules → pipeline/
- [x] Commit 3: 7 CLI scripts → scripts/
- [x] Commit 4: collections JSON + gitignore
- [x] Commit 5: git rm 18 old root files
- [x] Post-refactor verify (all scripts pass, 43 collections, privacy clean)
- [x] Commit 6: docs scrub, privacy clean, README updated

### Split Enrichment — Cluster 1 COMPLETE
Plan: `/home/ag-95/.claude/plans/2026-06-05-split-enrichment-local-claude.md`
- [x] Ollama setup (qwen2.5:7b, GPU verified, ~2.5GB VRAM)
- [x] config.py + requirements.txt (ollama fields)
- [x] pipeline/enrich_local.py (Ollama tool_use, two-stage normalization, _normalize_externals)
- [x] pipeline/notion.py (write_local_enrichment, status Enriched; write_enrichment status Summarised)
- [x] scripts/run_enrichment_local.py (per-item, skip logic, dry-run, force)
- [x] Verified: dry-run + live write + skip logic + format check
- [x] Committed (faa4703 pre-restructure, 0584f8c enrichment improvements)
- [x] Restructured to pipeline/enrich_local.py + scripts/run_enrichment_local.py

### Batch Ingest + Phase 3 Infra — COMPLETE
- [x] A1: pipeline/collections.py — 43 collections loaded from JSON
- [x] A2: pipeline/ingest.py + scripts/ingest_batch.py
- [x] A3: [Operational] All 43 collections ingested
- [x] B1: scripts/queue_pilot.py + notion.py additions
- [x] B2: [Operational] 155 items Expanded
- [x] C1-C5: Enrichment infra built (API-based structure remains in pipeline/enrich_claude.py)
