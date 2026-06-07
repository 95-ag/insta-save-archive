# Session

## Current State

**Branch:** `feature-phase3-enrichment-runs`
**Last commit:** `981fc2c` — refactor: prepare summarize batches by priority bucket and drop collection grouping

### Per-item priority pipeline + shared stage runner ✅ COMMITTED (2026-06-07)
Priority moved OFF the collection (old `enrichment_order`/groups) and ONTO the item:
a manually-assigned Notion `processing_priority` select (`High`/`Medium`/`Low`; blank =
unprioritised, processed last). Every per-item stage now shares ONE bucketed runner.
Plan: `/home/ag-95/.claude/plans/2026-06-07-priority-stage-runner.md`

- `pipeline/notion.py` — `query_by_status_and_priority(status, priority)` (None → select.is_empty)
- `pipeline/runner.py` (NEW) — `PRIORITY_BUCKETS = ["High","Medium","Low",None]` +
  `run_priority_stage(...)`: reads buckets in order, owns progress/counters/error routing;
  `process_fn(config,item,ctx)` owns work + Notion write + status transition
- **Expand** (`queue_runner.run_queue`) + **Enrich-local** (`run_enrichment_local`) delegate to the runner
- **Summarize** (`run_enrichment_claude_code`) — `--prepare` auto-picks the highest non-empty
  Enriched bucket (High first); dropped `--collection`/`--list-priority` + grouping import.
  As items become Summarised, the next `--prepare` advances.
- Live read-only verify (2026-06-07): `processing_priority` confirmed (options exactly
  High/Med/Low). Bucket distribution: Queued H22/M50/L7, Expanded H105/M9, Enriched H14/M15/L20,
  all blank=0. reconcile 9/9 still pass.
- **Behaviour note:** `--source_id` now requires the item to be in the stage's read_status
  (it scans the status buckets) — slightly stricter than before, more correct.
- **Deferred:** grouping code in untouched files (`collections.py`, `queue_pilot.py`,
  `run_enrichment.py`) left for the end-of-Phase-3 refactor.

Commits: 1775259 (runner+query) · de2a2ed (expand) · f212810 (enrich-local) · 981fc2c (summarize).

---

### Prior — Ingest Sync Layer COMPLETE (all clusters A–H committed and verified)
Fail-safe collection sync with move handling, hardened discovery (43/43, was 12),
reconciliation safety gate, rich UI + file logging across ALL stages.
Plan: `/home/ag-95/.claude/plans/2026-06-06-ingest-sync-layer.md`

### Full dry-run verified (2026-06-06)
`python scripts/ingest_batch.py --dry-run` → 16m47s, zero writes:
- discovered 43, 0 new, 0 missing, **complete=True**
- **creates=1363 · retags=11 · unchanged=226 · skipped_unsafe=0**
- 1363 creates expected: old ingest never completed all collections (only ~12 reached
  via the broken index-scrape). Hardened crawler now reaches all 43.
- skipped_unsafe=0 confirms the gate: full run = every collection complete = nothing withheld.

### Cluster H — yt-dlp metadata + self-healing backfill ✅ COMMITTED (2026-06-06)
Browser metadata extraction rate-limited at ~250/session (live run: 1363 created, 1104 blank
+ mistyped "Post"). Switched metadata engine to `yt-dlp --dump-json` (spike: 91%→~100% with
--ignore-no-formats-error, no wall at 300). Ingest now self-heals: refresh phase re-extracts
metadata for pages flagged `needs_metadata`, throttled (2-4s) + wall-stop (5 consec fails).
Code-reviewed (no blockers); fixes applied (N4/M3/N7/N5/inv-8/M1).

- `needs_metadata` trigger = **author missing OR posted_date missing**
- **Browser fallback** for ~1% of image posts yt-dlp refuses ("There is no video in this post")
- notion_client/httpx warnings → log file only; StageProgress shows current item + `↻ retries: N`

**Live backfill runs (2026-06-06):**
- Run 1 (~15:38): creates=0, retags=1, unchanged=1599 → **1345 metadata backfills** → yt-dlp wall hit (5 consec image-only fails), deferred tail to next run
- Run 2 (~18:37): **9 more backfills** → manually interrupted
- Verified: sample Notion pages show correct author, posted_date, type ✅

**Next:** O1 local enrichment → O3 Claude pass → O4 spot-check.
NOTE: run ingest and O1 SEQUENTIALLY (both write Notion).

### Commit log this session (newest first)
| Commit | Cluster | |
|---|---|---|
| 437e411 | H | fix: delete cookies.txt after each yt-dlp session |
| b6b461a | H | use yt-dlp for ingest metadata with throttle, self-healing, browser fallback |
| f27a55f | H | add yt-dlp metadata extractor with browser fallback and Notion metadata update |
| 5eef58c | G | migrate extraction+enrichment to shared StageProgress (−65 lines) |
| b53799a | F | docs: ingest sync layer + flags |
| 8020ef1 | E | rebuild ingest as fail-safe sync; dead code removed |
| 895b6b7 | D | pure reconcile + 9 invariant tests |
| be08922 | C | snapshots + bulk_load_state/set_collections |
| 85c9a3b | B | hardened crawler scroll_harvest + discovery |
| 37ab80d | A | reusable rich progress + file-only logging |
| (earlier) | — | crawler/notion fixes, ETA, Cluster 2, collections.json |

### Ingest Sync Layer — architecture (NEW)
Stages: discover → crawl → bulk-load-Notion → reconcile → apply.
Principle: **presence reliable, absence not** — adds always apply; removals only when a
collection's crawl `complete`; whole-collection strip needs `--confirm-removed`.
- `pipeline/observability.py` — generic `StageProgress` + `setup_logging` (file-only); reusable by all stages
- `pipeline/discovery.py` — hardened `/saved/` index crawl, additive collections.json merge
- `pipeline/crawler.py` — `scroll_harvest` (accumulate + bottom-detect + complete flag); URL from collections.json
- `pipeline/snapshots.py` — durable per-collection snapshots (tmp/ingest/snapshots/), age-based reuse
- `pipeline/reconcile.py` — pure diff + safety gate (9 unit tests in tests/test_reconcile.py)
- `pipeline/ingest.py` — `sync()` orchestration
- `scripts/ingest_batch.py` / `scripts/ingest.py` — CLIs (--dry-run/--discover-only/--fresh/--max-snapshot-age/--confirm-removed)
Flags verified live: --discover-only → 43/43 complete; single-collection --dry-run → reconcile + gate, zero writes.
Logs → `logs/` (gitignored). Snapshots → `tmp/ingest/` (gitignored).

---

## Repo Structure

```
pipeline/               ← importable library (pip install -e . already done)
  config.py             load_config(), Config dataclass, validate_notion_config()
  notion.py             all Notion API calls
  collections.py        loads config/collections.json; ordered_for_ingestion(), pilot_collections(),
                        pilot_collections_by_enrichment_priority()
  session.py            ensure_authenticated()
  crawler.py            crawl_collection()
  extractor.py          extract_post()
  extractor_deep.py     extract_transcript(), extract_carousel(), extract_ocr_frames()
  ingest.py             ingest_with_context() — no CLI
  queue_runner.py       run_queue(), run_item()
  runner.py             run_priority_stage() — shared priority-bucketed stage loop
  enrich_claude.py      enrich_item() — Anthropic API, summary+insights only (no title/externals)
  enrich_local.py       enrich_local() — Ollama tool_use, title+extracted_externals
  display.py            ensure_display(), close_display() — VcXsrv / X11

scripts/                ← CLI entry points (python scripts/<script>.py)
  ingest.py             single-collection ingest
  ingest_batch.py       all collections in priority order
  list_collections.py   discover + --update → config/collections.json (smart merge)
  queue_pilot.py        promote Imported → Queued
  run_extraction.py     Phase 2 deep extraction
  run_enrichment.py     Claude API enrichment (--collection, queries Enriched items)
  run_enrichment_local.py   Ollama enrichment CLI — DONE, VERIFIED
  run_enrichment_claude_code.py  Claude Code session enrichment — DONE, VERIFIED

config/
  collections.json      gitignored — your real data (43 entries)
  collections.example.json   committed — 2-entry placeholder template

prompts/
  enrichment_v1.0-enrich.txt
```

---

## Commit Log (this branch, newest first)

| Commit | Message | Key files |
|---|---|---|
| 9fda9c3 | feat: add Claude Code enrichment pass for summary and insights | pipeline/collections.py, pipeline/enrich_claude.py, pipeline/notion.py, scripts/run_enrichment_claude_code.py, scripts/run_enrichment.py |
| 31423f7 | docs: scrub private collection names and update paths | README, session.md, tasks.md, lessons.md |
| e6e887d | refactor: remove root-level files superseded | git rm 18 root .py files |
| 12c78a6 | feat: move collections data to gitignored JSON | config/collections.example.json, .gitignore |
| 486ff5c | refactor: move CLI scripts into scripts directory | scripts/ (7 files) |
| 7d91431 | refactor: move library modules into pipeline package | pipeline/ (12 files) |
| a9770db | chore: add pyproject.toml for editable install | pyproject.toml, pipeline/__init__.py |
| 0584f8c | feat: enforce extracted_externals line format and add pipeline status transitions | pipeline/enrich_local.py, pipeline/notion.py |
| faa4703 | feat: add local Ollama enrichment pass for title and extracted_externals | (pre-restructure) |

---

## Operational Status

| Stage | Status | Notes |
|---|---|---|
| A3 Batch ingest | ✅ COMPLETE | 43 collections, re-run = 0 creates |
| B2 Pilot extraction | ✅ COMPLETE | 155 items Expanded, 0 failed |
| Cluster 1 implementation | ✅ COMPLETE | enrich_local.py + run_enrichment_local.py |
| Cluster 2 implementation | ✅ COMPLETE | enrich_claude_code.py + updates (9fda9c3) |
| O1 Local enrichment run | ⏳ IN PROGRESS | 59/162 done before WSL restart — re-run appends |
| O2 enrichment_order set | ✅ COMPLETE | 15 collections in config/collections.json |
| O3–O4 Claude pass | ❌ NOT DONE | After O1 completes |

---

## Enrichment Strategy (FINAL)

| Field | Engine | Script | Status |
|---|---|---|---|
| `title` | Ollama local | `scripts/run_enrichment_local.py` | ✅ implemented, verified |
| `extracted_externals` | Ollama local | `scripts/run_enrichment_local.py` | ✅ implemented, verified |
| `expanded_summary` | Claude Code | `scripts/run_enrichment_claude_code.py` | ✅ implemented, verified |
| `key_insights` | Claude Code | `scripts/run_enrichment_claude_code.py` | ✅ implemented, verified |

### Pipeline status flow
`Imported` → (set Queued + processing_priority manually in Notion) → `Queued`
→ (run_extraction) → `Expanded` → (run_enrichment_local) → `Enriched`
→ (run_enrichment_claude_code) → `Summarised`
Every per-item stage processes in priority order: High → Medium → Low → unprioritised (blank).

### Local pass behaviour
- Queries `Expanded` items from Notion
- Skip condition: title is NOT a placeholder (`{author} — {shortcode}` pattern)
- On write: sets `pipeline_status = Enriched`
- Interrupt-safe — re-run picks up where it left off
- `--force` to overwrite already-enriched items

### Claude Code pass behaviour
- `--prepare`: fetches the highest-priority non-empty `Enriched` bucket (High → Medium → Low →
  unprioritised) → writes `tmp/enrichment_batch.json` + `tmp/enrichment_prompt.txt`. Advances to
  the next bucket on each run as items become Summarised.
- `--upload`: reads `tmp/enrichment_results.json` → writes `expanded_summary + key_insights` → status: `Summarised`
- Does NOT touch `title` or `extracted_externals`
- Cleans up tmp files on successful upload

---

## Operational Runbook (O1 → O4)

### O1 — Local enrichment (overnight)
```bash
source .venv/bin/activate
python scripts/run_enrichment_local.py 2>&1 | tee /tmp/local_enrichment.log
```
Processes all `Expanded` items → title + extracted_externals → status: `Enriched`.
Re-runnable. Safe to interrupt and re-run.

**Note:** `DYh5E80ssPU` is already `Summarised` (used as T13 test upload). Its title/externals are real (set by local pass). Summary/insights contain test text. Reset to `Enriched` in Notion manually if you want it in the real Claude queue.

### O2 — Set enrichment priority ✅ DONE
All 15 extract collections have `enrichment_order` set in `config/collections.json`:
Coding-AI(1) > Coding-Web(2) > Website Handling(3) > Inspo-Website(4) > Job Hunt(5) >
Branding(6) > Digital Content(7) > Tips-Content(8) > Inspo-Quotes(9) > Hustle Ideas(10) >
Side Hustle(11) > BoI Biz(12) > Foodie(13) > Fitness(14) > Quotes(15)

### O3 — Claude Code pass (by priority bucket, highest first)
```bash
# 1. Prepare the next non-empty Enriched bucket (auto-picks High → Medium → Low → blank)
python scripts/run_enrichment_claude_code.py --prepare

# 2. In this Claude Code session, say:
#    "Read tmp/enrichment_prompt.txt and write results JSON to tmp/enrichment_results.json"

# 3. Upload
python scripts/run_enrichment_claude_code.py --upload

# Repeat 1-3 until --prepare reports no Enriched items remain
```

### O4 — Spot-check
5 Notion pages: title real, externals formatted, summary substantive, insights actionable.

---

## Locked Technical Decisions

| Concern | Decision |
|---|---|
| Transcript engine | yt-dlp + faster-whisper base int8 |
| OCR engine | RapidOCR (rapidocr-onnxruntime==1.4.4) |
| Local enrichment engine | Ollama + qwen2.5:7b (fallback: 3b) |
| Claude enrichment mechanism | Claude Code session (no API; Claude Max only) |
| Claude enrichment scope | Per-item `processing_priority` select (High/Med/Low); highest non-empty Enriched bucket first |
| Notion write (local pass) | Per-item sequential (interrupt-safe); status → Enriched |
| Notion write (Claude pass) | Collection-batch read → one Claude turn → per-item upload; status → Summarised |
| write_enrichment fields | expanded_summary + key_insights only — no title, no extracted_externals |
| extracted_externals format | String, one per line: `[type] name — context` |
| Collection names | Gitignored `config/collections.json` — NEVER hardcode in Python |
| processing priority | Per-item Notion `processing_priority` select (High/Med/Low), manual. Shared `run_priority_stage` (pipeline/runner.py) processes High→Med→Low→blank for every stage. Old collection `enrichment_order`/groups deprecated (dead code, cleaned end of Phase 3) |
| detected_entities field | REMOVED — redundant with extracted_externals |
| Carousel img scope | `ul img` not `img` |
| CDN domain | instagram.fblr22-*.fna.fbcdn.net |
| Cookie format for yt-dlp | JSON → Netscape at runtime → tmp/cookies.txt |
| Venv binaries in subprocess | `Path(sys.executable).parent / "binary-name"` |
| Editable install | `pip install -e .` done — `pipeline.*` importable from scripts/ |

## Key Notion API Findings (permanent)

- notion-client 3.1.0, API 2025-09-03
- `databases.query` removed → `data_sources.query(data_source_id, ...)`
- `data_source_id` ≠ `database_id` → resolve via `databases.retrieve()`['data_sources'][0]['id']
- Schema property creation: `data_sources.update(ds_id, properties={"name": {"rich_text": {}}})` — NOT `databases.update`
- Phase 3 properties (`expanded_summary`, `key_insights`, `extracted_externals`) already created in Notion DB

## Environment

- WSL2 Ubuntu, Windows host (Taiga), GPU: RTX 3050 Ti 4GB VRAM
- Branch: `feature-phase3-enrichment-runs`
- Venv: `.venv/` → `source .venv/bin/activate`
- Editable install: `pip install -e .` already done (pyproject.toml present)
- Run scripts as: `python scripts/<script>.py` from project root
- Playwright: headless default; `--headed` for visible browser; `DISPLAY=172.22.48.1:1.0`
- Ollama: system-wide systemd service (`systemctl status ollama`), qwen2.5:7b pulled
- Sensitive: `session_cookies.json`, `.env`, `config/collections.json` — gitignored, never stage
