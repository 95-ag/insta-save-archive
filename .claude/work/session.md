# Session

## Current State

**Branch:** `feature-phase3-enrichment-runs`
**Last commit:** `83f14e3` — fix: instruct summary prompt to use paragraph breaks for readable Notion output

### Pipeline naming refactor ✅ COMPLETE (Clusters A–E committed + E docs done)

Full rename of all pipeline fields, statuses, module names, and script names. Zero behaviour change — pure naming fix to end status machine confusion.

**Commits this session:**
| Commit | Cluster | Message |
|---|---|---|
| cde96c7 | A | refactor: rename pipeline fields, statuses, and notion write functions |
| 461ec71 | B | refactor: rename pipeline modules to titler.py and extract_runner.py |
| 89721f1 | C | refactor: rename enrichment/extraction scripts; title pass reads Queued and Extracted |
| 83f14e3 | D | fix: instruct summary prompt to use paragraph breaks for readable Notion output |
| (Cluster E) | E | docs: update project docs with renamed pipeline and flow diagram — **PENDING COMMIT** |
| (Cluster F) | F | chore: record naming refactor and update session state — **PENDING COMMIT** |

**Naming changes (code is live):**
| Old | New |
|---|---|
| `pipeline_status` | `status` |
| `processing_priority` | `priority` |
| `expanded_summary` | `summary` |
| `extracted_externals` | `externals` |
| `Expanded` | `Extracted` |
| `Enriched` | *(removed — title pass no longer gates Claude)* |
| `Summarised` | `Summarized` |
| `pipeline/enrich_local.py` | `pipeline/titler.py` |
| `pipeline/queue_runner.py` | `pipeline/extract_runner.py` |
| `scripts/run_enrichment_local.py` | `scripts/title.py` |
| `scripts/run_enrichment_claude_code.py` | `scripts/summarize.py` |
| `scripts/run_extraction.py` | `scripts/extract.py` |
| `scripts/queue_pilot.py` | `scripts/queue.py` |
| `write_local_enrichment` | `write_title` (no status write) |
| `write_enrichment` | `write_summary` |

**Title pass decoupled:** `scripts/title.py` runs on Queued AND Extracted; writes `title` only; does NOT change status. Summary pass reads `Extracted` directly — no `Enriched` gate.

**Summary line break fix:** `_build_prompt` now instructs Claude to use blank lines between topic sections (Cluster D, `83f14e3`).

### Notion manual steps (user action required, after code is deployed)
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

### Move extracted_externals from local → Claude ✅ COMMITTED (2026-06-07)
Local pass produces **title only**. Claude pass produces `summary` + `externals`.

- **Cluster A** (`61735f7`): local pass reduced to title schema only
- **Cluster B** (`474fdf5`): Claude pass extended with full externals extraction

### O1 validation run ✅ (2026-06-07)
`--limit 20 --force`: enriched=20, failed=0, elapsed 8m19s (~25s/item). 0% failure rate (was 40%).

### Local enrichment JSON schema fix ✅ (bb6a04b)
DX15O2dIWz0: 26 min → 42s (37× speedup). Constrained decoding eliminates compliance failures.

---

### Per-item priority pipeline + shared stage runner ✅ COMMITTED (2026-06-07)
Priority moved OFF the collection onto the item (`priority` select; High/Medium/Low; blank = last).
Every per-item stage shares one bucketed runner (`pipeline/runner.py`).
Plan: `/home/ag-95/.claude/plans/2026-06-07-priority-stage-runner.md`

---

## Pipeline Flow (current)

```
Imported → (manual: Queued + priority) → Extracted → Summarized → Tagged* → Routed*
title.py runs on Queued + Extracted anytime, no status change
*not yet implemented
```

## Operational Status

| Stage | Status | Notes |
|---|---|---|
| A3 Batch ingest | ✅ COMPLETE | 43 collections, re-run = 0 creates |
| B2 Pilot extraction | ✅ COMPLETE | 155 items now Extracted, 0 failed |
| Local title pass | ✅ IMPLEMENTED | scripts/title.py — verified |
| Claude summarize pass | ✅ IMPLEMENTED | scripts/summarize.py — verified |
| Pipeline naming refactor | ✅ COMMITTED (A–D) | Cluster E–F pending commit |
| O1 full remaining run | ⏳ PENDING | Run after Notion manual steps complete |
| O3 Claude summarize pass | ⏳ PENDING | After O1 — cycle --prepare → Claude → --upload |

## Operational Runbook (current names)

### O1 — Title pass (run after Notion manual steps)
```bash
source .venv/bin/activate
python scripts/title.py 2>&1 | tee /tmp/title.log
```
Processes all Queued + Extracted items without real title. Does NOT change status.

### O3 — Claude summarize pass
```bash
# 1. Prepare the next non-empty Extracted bucket (High → Medium → Low → blank)
python scripts/summarize.py --prepare

# 2. In a Claude Code session, say:
#    "Read tmp/enrichment_prompt.txt and write results to tmp/enrichment_results.json"

# 3. Upload
python scripts/summarize.py --upload

# Repeat 1-3 until --prepare reports no Extracted items remain
```

### O4 — Spot-check
5 Notion pages: title real, externals grouped, summary substantive with paragraph breaks.

---

## Repo Structure (current)

```
pipeline/
  config.py             load_config(), Config dataclass, validate_notion_config()
  notion.py             all Notion API calls
  collections.py        ordered_for_ingestion(), pilot_collections()
  session.py            ensure_authenticated()
  crawler.py            scroll_harvest()
  extractor.py          extract_post()
  extractor_deep.py     extract_transcript(), extract_carousel(), extract_ocr_frames()
  ingest.py             ingest_with_context() — no CLI
  runner.py             run_priority_stage() — shared priority-bucketed stage loop
  extract_runner.py     run_extract_stage(), run_extract_item()
  titler.py             generate_title(), validate_title_config()
  observability.py      StageProgress, setup_logging()
  display.py            ensure_display(), close_display()

scripts/
  ingest.py             single-collection ingest
  ingest_batch.py       all collections in priority order
  list_collections.py   discover + --update → config/collections.json
  queue.py              promote Imported → Queued
  extract.py            deep extraction (Queued → Extracted)
  title.py              Ollama title generation (Queued + Extracted, no status change)
  summarize.py          Claude Code summary + externals (--prepare / --upload)
```

## Environment

- WSL2 Ubuntu, Windows host (Taiga), GPU: RTX 3050 Ti 4GB VRAM
- Branch: `feature-phase3-enrichment-runs`
- Venv: `.venv/` → `source .venv/bin/activate`
- Editable install: `pip install -e .` already done
- Ollama: system-wide systemd service, qwen2.5:7b pulled
- Sensitive: `session_cookies.json`, `.env`, `config/collections.json` — gitignored, never stage

## Locked Technical Decisions

| Concern | Decision |
|---|---|
| Transcript engine | yt-dlp + faster-whisper base int8 |
| OCR engine | RapidOCR (rapidocr-onnxruntime==1.4.4) |
| Local enrichment engine | Ollama + qwen2.5:7b (fallback: 3b) |
| Claude enrichment mechanism | Claude Code session (no API; Claude Max only) |
| Ollama schema format | `format=<JSON schema dict>` in client.chat() — constrained decoding, no tool_use |
| Title pass status | No status write — decoupled from status machine |
| Claude pass input status | Extracted |
| Claude pass output status | Summarized |
| summary format | Paragraph-separated prose; blank lines between sections |
| externals format | Grouped by category: `[Category]\n  name — context` |
| Collection names | Gitignored config/collections.json — NEVER hardcode in Python |
| priority field | Per-item Notion `priority` select (High/Med/Low), set manually |
| Carousel img scope | `ul img` not `img` |
| CDN domain | instagram.fblr22-*.fna.fbcdn.net |
| Cookie format for yt-dlp | JSON → Netscape at runtime → tmp/cookies.txt |
| Venv binaries in subprocess | `Path(sys.executable).parent / "binary-name"` |

## Key Notion API Findings (permanent)

- notion-client 3.1.0, API 2025-09-03
- `databases.query` removed → `data_sources.query(data_source_id, ...)`
- `data_source_id` ≠ `database_id` → resolve via `databases.retrieve()`['data_sources'][0]['id']
- Schema property creation: `data_sources.update(ds_id, properties={"name": {"rich_text": {}}})` — NOT `databases.update`
- Phase 3 properties already created in Notion DB (as old names — rename via Notion UI per manual steps above)
