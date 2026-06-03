# Tasks

Plan: `/home/ag-95/.claude/plans/create-a-phase-2-ticklish-flamingo.md`

## Phase 2 — Deep Extraction — COMPLETE

### Validation (gates the build — all three must be signed off before T1)

- [x] V1: Transcript engine bake-off — faster-whisper base int8 locked; yt-dlp cookie access confirmed; findings in session.md
- [x] V2: OCR engine bake-off — RapidOCR locked; findings in session.md
- [x] V3: Carousel mechanics — DOM stepping confirmed; CDN filter + no-article-selector noted in session.md

### Build (only after V1–V3 signed off)

- [x] T1: `requirements.txt` — first real dep manifest; install chosen engines into `.venv`; document system prereqs
- [x] T2: Stage 2 fields added to Notion DB manually; property names/types verified
- [x] T3: `notion.py` — `query_by_status`, `_rich_text_chunked`, `write_extraction`; `raw_extraction` appended under version key on reprocess, never overwritten
- [x] T4: `extractor_deep.py` — `extract_transcript` + `transcribe` seam
- [x] T5: `extractor_deep.py` — `extract_ocr_frames` + `extract_carousel` + `ocr_image` seam
- [x] T6: `config.py` Phase 2 config; `.gitignore` + `tmp/`; temp-file cleanup
- [x] T7: `queue_runner.py` + `run_extraction.py` CLI (`--limit`, `--source_id`)
- [x] T8: End-to-end run on 10+ `Queued` items; acceptance criteria verified
- [x] T9: `README.md`, `session.md`, `tasks.md`, `lessons.md` updated; phase frozen

---

## Phase 1 — COMPLETE

- [x] Assumption 1: Playwright can access Instagram Saved collections with persisted session
- [x] T1: `.env.example` + `config.py` — env loading, validation, fail-fast on missing keys
- [x] T2: `session.py` — login, cookie persistence, health check, re-auth flow
- [x] T3: Manual session validation — cookie persistence and re-auth both confirmed
- [x] T4: `crawler.py` — navigate to target collection, enumerate saved items, yield post URLs
- [x] T4b: Split config validation — `IG_USERNAME` + `TARGET_COLLECTION` required always; Notion creds deferred to T7
- [x] T5: Manual crawler validation — 36 posts enumerated from "Job Hunt" collection
- [x] T6: `extractor.py` — all six Stage 1 fields extracted correctly on real posts; type detection validated on Reels and Carousel
- [x] T7: `notion.py` — all three operations verified; Notion API 2025-09-03 uses `data_sources.query`
- [x] T8: `ingest.py` — 36/36 created on first run, 0/36 on re-run (full dedup verified)
- [x] T9: `README.md` — setup, auth, run instructions, common failures documented
- [x] T10: Exit criteria — 36 items ingested, re-run=0 duplicates, restart=0 duplicates, null fields confirmed null
