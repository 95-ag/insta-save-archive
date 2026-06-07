# Tasks

## Active Plan

`/home/ag-95/.claude/plans/2026-06-06-ingest-sync-layer.md`
Branch: `feature-batch-ingest-phase3-enrich`

---

## Ingest Sync Layer — IN PROGRESS

### Cluster A — Observability foundation (generic, reusable) ✅ COMMITTED 37ab80d
- [x] `requirements.txt`: add `rich>=13.0`
- [x] `.gitignore`: add `logs/` (tmp/ already covers tmp/ingest/)
- [x] `pipeline/observability.py`: `setup_logging(stage_name)` + generic `StageProgress`
- [x] Manual check: bars + counters verified; log file written; zero terminal spam
- [x] Commit: `feat: add reusable rich progress and file-only logging for all stages`

### Cluster B — Hardened crawling + discovery ✅ COMMITTED 85c9a3b
- [x] `pipeline/crawler.py`: `scroll_harvest` (accumulate + incremental + bottom-detect + complete flag); `resolve_collection_url` from collections.json; `crawl_collection` returns (posts, complete). Removed index-scrape + COLLECTION_LINK_SELECTOR (dead-code item from E done here).
- [x] `pipeline/discovery.py`: hardened index crawl via scroll_harvest + additive collections.json merge + missing/complete flags
- [x] No-browser verify: imports, URL resolution, shortcode/href regex, additive merge (annotations preserved)
- [ ] Live verify deferred to Cluster E: `--discover-only` finds 43
- [x] Commit: `feat: harden crawler with accumulate-scroll and direct-URL discovery`

### Cluster C — Snapshots + bulk Notion state ✅ COMMITTED be08922
- [x] `pipeline/snapshots.py`: write/read/is_reusable/clear_snapshots; tmp/ingest/snapshots/
- [x] `pipeline/notion.py`: `bulk_load_state()` (one paginated pass → source_id→{page_id,collections}), `set_collections()` (idempotent absolute set)
- [x] Verify: snapshot round-trip + reuse policy (fresh/incomplete/stale/None); notion fns import-clean (live in E)
- [x] Commit: `feat: add collection snapshots and bulk Notion state load`

### Cluster D — Reconciliation (pure + tested) ✅ COMMITTED 895b6b7
- [x] `pipeline/reconcile.py`: pure diff + presence/absence safety gate (PostAction/Plan)
- [x] `tests/test_reconcile.py`: 9 invariant tests (added confirmed-removal + add-from-incomplete)
- [x] Run: `pytest tests/test_reconcile.py -v` → 9 passed; pytest added to requirements
- [x] Commit: `feat: add pure reconciliation with presence/absence safety gate`

### Cluster E — Orchestration + CLI + dead-code removal ✅ COMMITTED 8020ef1
- [x] `pipeline/extractor.py`: dropped `collection` param; tags set by reconcile
- [x] `pipeline/notion.py`: `_build_properties` uses `collections` list; removed dead `add_collection_if_missing`
- [x] `pipeline/ingest.py`: `sync()` orchestrating stages 0→4 with StageProgress
- [x] `scripts/ingest_batch.py`: flags `--dry-run --discover-only --fresh --max-snapshot-age --confirm-removed --headed`; StageProgress + file logging
- [x] `scripts/ingest.py`: single-collection sync via same path
- [x] Dead code removed; grep clean (add_collection_if_missing/ingest_with_context/old signatures gone)
- [x] LIVE verify: `--discover-only` → 43 found 0 missing complete=True (22s); single-collection `--dry-run` → crawl+bulk-load+reconcile+gate (247 unsafe skipped correctly) zero writes
- [x] Operational (user-driven): full 43-collection live run with writes ✅ (creates=0, 1354 backfills)
- [x] Commit: `feat: rebuild ingest as fail-safe collection sync with move handling`

### Cluster F — Docs ✅ COMMITTED b53799a
- [x] README ingest section rewritten (sync model, safety, flags, dry-run summary)
- [x] session.md, lessons.md, tasks.md updated
- [x] Commit: `docs: document fail-safe ingest sync layer and flags`

### Cluster H — yt-dlp metadata + self-healing backfill ✅ COMMITTED (f27a55f, b6b461a, 437e411)
Browser extraction walls at ~250/session (1363 created, 1104 blank). yt-dlp spike: 91% ok (→~100% with --ignore-no-formats-error), no wall.
- [x] `pipeline/extractor.py`: `extract_metadata_ytdlp` + `minimal_metadata` (--ignore-no-formats-error; type via vcodec/JSON-lines)
- [x] `pipeline/notion.py`: `update_metadata()` + `bulk_load_state` needs_metadata flag
- [x] `pipeline/ingest.py`: creates via yt-dlp; refresh phase; throttle 2-4s + wall-stop (5 consec)
- [x] Code review (general-purpose agent) — no blockers; fixes applied: N4 _iso_date int, M3 reel-at-/p/ type, N7 discovery 4s wait, N5 chunk warning, inv-8 dry-run persist=not dry_run, M1 comment
- [x] Verify: imports clean; reconcile 9 tests pass; yt-dlp read-only test recovered the 25 "no video" errors as Carousel
- [x] `_RETYPE_ALL_POSTS` removed — steady-state trigger = author OR posted_date missing
- [x] Browser fallback added to `_extract_meta`: yt-dlp first → if no author → `extract_post(context, url)`
- [x] Observability: notion_client/httpx warnings → log file only; StageProgress live current-item + `↻ retries: N`
- [x] Live ingest run: 1345 + 9 backfills; verified sample Notion pages correct ✅
- [x] Commit 1: `feat: add yt-dlp metadata extractor and Notion metadata update` (f27a55f)
- [x] Commit 2: `feat: use yt-dlp for ingest metadata with throttle, self-healing, browser fallback` (b6b461a)
- [x] Fix: `fix: delete cookies.txt after each yt-dlp session` (437e411)

### Cluster G — Migrate other stages ✅ COMMITTED 5eef58c
- [x] `pipeline/queue_runner.py` + `scripts/run_extraction.py` → StageProgress (run_queue takes progress)
- [x] `run_enrichment_local.py`, `run_enrichment.py`, `run_enrichment_claude_code.py` → StageProgress + setup_logging; ad-hoc ETA/DONE blocks removed
- [x] Verify: imports clean; --list-priority works; net -65 lines
- [x] Commit: `refactor: migrate extraction and enrichment to shared progress display`

---

## Verification (end-to-end)
- [ ] pytest reconcile invariants pass
- [ ] `--discover-only` → 43 found
- [ ] `--dry-run` → zero Notion writes
- [ ] live move test → add+remove on Notion page; partial crawl → no removal
- [ ] full 43-collection run; clean UI; full debug log
- [ ] idempotent re-run → near-zero writes, snapshots reused
- [ ] grep: no dead references

---

## Completed (prior work)

### Cluster 2 — Claude Code enrichment — COMPLETE (9fda9c3)
### Repo Restructure + Privacy Scrub — COMPLETE (31423f7)
### Split Enrichment — Cluster 1 COMPLETE
### Batch Ingest + Phase 3 Infra — COMPLETE

## Operational runs (pending, after ingest layer)
- [ ] O1: local enrichment full run (interrupt-safe, `-a` to append log)
- [ ] O2: enrichment_order set ✅ (15 collections)
- [ ] O3: Claude pass per priority collection
- [ ] O4: spot-check 5 Notion pages
