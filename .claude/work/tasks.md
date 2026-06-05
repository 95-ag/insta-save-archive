# Tasks

## Active Plan

`/home/ag-95/.claude/plans/2026-06-05-split-enrichment-local-claude.md`

---

## Split Enrichment — Local + Claude Code

### Pre-execution: Ollama setup
- [ ] Install Ollama system-wide (`curl -fsSL https://ollama.com/install.sh | sh`)
- [ ] Pull qwen2.5:7b + test GPU inference (nvidia-smi to watch VRAM)
- [ ] Fallback if OOM: `ollama rm qwen2.5:7b` → `ollama pull qwen2.5:3b` → set `OLLAMA_MODEL=qwen2.5:3b` in `.env`

### Cluster 1 — Local enrichment pass
- [ ] T1: `config.py` — add `ollama_model`, `ollama_base_url` to Config + load_config; `requirements.txt` add `ollama>=0.3.0`; `pip install ollama`
- [ ] T2: `enrichment_local.py` — Ollama client, tool_use, returns `{title, extracted_externals}`
- [ ] T3: `notion.py` — add `write_local_enrichment(config, page_id, title, extracted_externals)`; remove title + extracted_externals from existing `write_enrichment`
- [ ] T4: `run_enrichment_local.py` — per-item CLI; skip if title not placeholder; `--limit`, `--source_id`, `--dry-run`, `--force`
- [ ] T5: Verify — `--dry-run --limit 3` prints correctly; `--limit 1` writes to Notion without touching expanded_summary
- [ ] T6: Commit cluster 1

### Cluster 2 — Claude Code enrichment pass
- [ ] T7: `collections_config.py` — add `ENRICHMENT_HUSTLING_ORDER` (Coding AI first, Branding & Logo last), `pilot_collections_by_enrichment_priority()`
- [ ] T8: `enrichment.py` — narrow `_SAVE_ENRICHMENT_TOOL` to `expanded_summary + key_insights` only
- [ ] T9: `run_enrichment_claude_code.py` — `--prepare` (batch read → prompt file), `--upload` (results → Notion), `--list-priority`
- [ ] T10: `run_enrichment.py` — add `--collection` flag; order by enrichment priority
- [ ] T11: Verify — `--list-priority` shows correct order; `--prepare --collection "Coding - AI"` creates `tmp/enrichment_prompt.txt`
- [ ] T12: Commit cluster 2
- [ ] T13: Commit docs (tasks.md, session.md)

### Operational runs (after plan complete)
- [ ] O1: `python run_enrichment_local.py` — overnight, all 155 items, title + externals
- [ ] O2: Per Hustling collection: `--prepare --collection X` → Claude reads prompt → `--upload`
  - Coding - AI
  - Coding - Web Design
  - Website Handling
  - Inspo - Website
  - Job Hunt
  - Branding & Logo (last)
- [ ] O3: Spot-check 5 enriched Notion pages — title real, summary substantive, insights actionable, raw_extraction untouched

---

## Completed This Session

### Batch Ingest + Phase 3 Enrichment Infra — COMPLETE
Plan: `/home/ag-95/.claude/plans/2026-06-04-batch-ingest-extract-enrich.md`

- [x] A1: `collections_config.py` — 43 collections, groups, pilot flags, ordering functions
- [x] A2: `ingest.py` refactor (`ingest_with_context`) + `ingest_batch.py`
- [x] A3: [Operational] Batch ingest all 43 collections — COMPLETE
- [x] B1: `queue_pilot.py` + `notion.py` additions
- [x] B2: [Operational] Queue + extract pilot — COMPLETE (155 Expanded, 0 failed)
- [x] C1: `config.py` + `requirements.txt` (anthropic fields)
- [x] C2: `prompts/enrichment_v1.0-enrich.txt`
- [x] C3: `enrichment.py` (API-based, will be narrowed by split plan)
- [x] C4: `notion.py` additions (`get_page_content`, `write_enrichment`)
- [x] C5: `run_enrichment.py` (will be updated by split plan)
- [ ] C6: [Operational] Enrichment run — BLOCKED → resolved by split enrichment plan above

---

## Phase 2 — COMPLETE
- [x] V1–V3: Engine validation (faster-whisper, RapidOCR, carousel mechanics)
- [x] T1–T9: Full build, 155 items extracted

## Phase 1 — COMPLETE
- [x] T1–T10: Full ingestion pipeline, 36 Job Hunt items
