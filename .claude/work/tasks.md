# Tasks

Plan: `/home/ag-95/.claude/plans/insta-save-archive-phase1.md`

## Completed

- [x] Assumption 1: Playwright can access Instagram Saved collections with persisted session
- [x] T1: `.env.example` + `config.py` — env loading, validation, fail-fast on missing keys
- [x] T2: `session.py` — login, cookie persistence, health check, re-auth flow
- [x] T3: Manual session validation — cookie persistence and re-auth both confirmed
- [x] T4: `crawler.py` — navigate to target collection, enumerate saved items, yield post URLs
- [x] T4b: Split config validation — `IG_USERNAME` + `TARGET_COLLECTION` required always; Notion creds deferred to T7
- [x] T5: Manual crawler validation — 36 posts enumerated from "Job Hunt" collection
- [x] T6: `extractor.py` — all six Stage 1 fields extracted correctly on real posts; type detection validated on Reels and Carousel

## Active

- [ ] T9: `README.md` — setup, auth steps, how to run, common failures
- [ ] T10: Exit criteria — ingest 20+ items, re-run produces zero duplicates, interruption + restart test

## Completed (continued)

- [x] T7: `notion.py` — all three operations verified; Notion API 2025-09-03 uses `data_sources.query`
- [x] T8: `ingest.py` — 36/36 created on first run, 0/36 created on re-run (full dedup verified)
