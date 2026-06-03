# Tasks

Plan: `/home/ag-95/.claude/plans/insta-save-archive-phase1.md`

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
