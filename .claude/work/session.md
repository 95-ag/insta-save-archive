# Session

## Current State

**Branch:** `feature-phase3-enrichment-runs` — closing for PR
**Last commit:** `c5b9ca1` — fix: remove transcript_available writes; update README and stale stage names

### Pending commits (branch closure)

| Cluster | Files | Message |
|---|---|---|
| J | `pipeline/config.py`, `pipeline/extractor_deep.py`, `scripts/title.py`, `scripts/summarize.py` | `feat: add post OCR extraction, rate limit delay, and title scope fix` |
| K | `scripts/promote.py`, `README.md`, `.claude/docs/PROJECT.md`, `.claude/docs/IMPLEMENTATION_PLAN.md` | `fix: rename queue.py to promote.py to avoid shadowing stdlib queue module` |
| L | `.claude/work/session.md`, `.claude/work/tasks.md` | `chore: finalize work docs for branch closure` |

---

## Branch summary — feature-phase3-enrichment-runs

All Phase 3 enrichment work complete. Pipeline is operational and tested end-to-end.

### Commits on this branch (18 ahead of main)
| Commit | Message |
|---|---|
| 1775259 | feat: add priority-bucketed stage runner and status+priority query |
| de2a2ed | refactor: drive extraction through the priority-bucketed runner |
| f212810 | refactor: drive local enrichment through the priority-bucketed runner |
| 981fc2c | refactor: prepare summarize batches by priority bucket and drop collection grouping |
| ada84d6 | docs: record priority-stage runner model and deferred refactor tasks |
| 30e2be0 | fix: trust notion type in phase 2 extraction; surface no_data when nothing extracted |
| aa4eaec | refactor: group extracted_externals by category with section headers |
| bb6a04b | fix: switch local enrichment to ollama json schema format for reliable structured output |
| 8ae2f4e | refactor: drop key_insights and add dynamic content batching to claude pass |
| 61735f7 | refactor: local enrichment produces title only; externals move to claude pass |
| 474fdf5 | feat: claude pass generates extracted_externals alongside expanded_summary |
| 89721f1 | refactor: rename enrichment/extraction scripts; title pass reads Queued and Extracted |
| 461ec71 | refactor: rename pipeline modules to titler.py and extract_runner.py |
| cde96c7 | refactor: rename pipeline fields, statuses, and notion write functions |
| 83f14e3 | fix: instruct summary prompt to use paragraph breaks for readable Notion output |
| 0081f6d | docs: update project docs with renamed pipeline and flow diagram |
| 0556640 | chore: record naming refactor and update session state |
| c5b9ca1 | fix: remove transcript_available writes; update README and stale stage names |
| (J–L) | pending |

---

## O-Runs Status (ongoing — DB not yet complete)

| Run | Action | Status |
|---|---|---|
| Title — Extracted + Imported | `python scripts/title.py` | ✅ done |
| Summarize — High Extracted (set 1) | `--prepare` → Claude → `--upload` | ✅ done |
| Summarize — Medium Extracted (set 1) | same | ✅ done |
| Summarize — Low Extracted (set 1) | same | ✅ done |
| Extract — all Queued (set 2) | `python scripts/extract.py` | 🔄 running overnight (115 items) |
| Summarize — set 2 | cycle `--prepare` → Claude → `--upload` | ⏳ daytime, after set 2 extraction |
| Spot-check O4 | 5 Notion pages: title, externals, summary | ⏳ after all Summarized |

---

## Pipeline Flow (current)

```
Imported → (manual: Queued + priority) → Extracted → Summarized → Tagged* → Routed*

title.py: run after extraction (Extracted + Imported, no Queued), no status change
*not yet implemented — future branches
```

## Repo Structure (current)

```
pipeline/
  config.py             load_config(), Config dataclass, validate_notion_config()
  notion.py             all Notion API calls
  collections.py        ordered_for_ingestion(), pilot_collections()
  session.py            ensure_authenticated()
  crawler.py            scroll_harvest()
  extractor.py          basic post metadata extraction (Phase 1)
  extractor_deep.py     extract_transcript(), extract_carousel(), extract_ocr_frames(), extract_post()
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
  promote.py            promote Imported → Queued
  extract.py            deep extraction (Queued → Extracted)
  title.py              Ollama title generation (Extracted + Imported, no status change)
  summarize.py          Claude Code summary + externals (--prepare / --upload)
```

## Environment

- WSL2 Ubuntu, Windows host (Taiga), GPU: RTX 3050 Ti 4GB VRAM
- Branch: `feature-phase3-enrichment-runs`
- Venv: `.venv/` → `source .venv/bin/activate`
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
| Post OCR scope | `img` (single image, not in ul) |
| CDN domain | instagram.fblr22-*.fna.fbcdn.net |
| Cookie format for yt-dlp | JSON → Netscape at runtime → tmp/cookies.txt |
| Venv binaries in subprocess | `Path(sys.executable).parent / "binary-name"` |
| Extract inter-item delay | 3–7s randomised (EXTRACT_DELAY_MIN/MAX env), in finally block |
| Content guard | if transcript + OCR + carousel_slides all null → skip write_extraction, item stays Queued |
