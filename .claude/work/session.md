# Session

## Current state

Phase 1 complete. All exit criteria met against real Instagram content.

## Phase 1 exit criteria (verified)

- 36 posts ingested from "Job Hunt" collection
- Re-run produces 0 new rows (full dedup via `source_id`)
- Restart after interrupt produces 0 duplicates
- Null fields stored as null (no empty strings)
- 36 unique entries confirmed in Notion

## Key findings (Notion API — permanent)

- notion-client 3.1.0 uses Notion API version `2025-09-03`
- `databases.query` removed — use `data_sources.query(data_source_id, ...)`
- `data_source_id` ≠ `database_id` — resolve via `databases.retrieve()`['data_sources'][0]['id']
- Schema inspection requires `data_sources.retrieve(data_source_id)` not `databases.retrieve`
- Property rename requires `data_sources.update(data_source_id, properties={...})`

## Validation findings (Assumption 1)

**Display setup (permanent)**
- WSLg broken for window interaction on this machine
- All Playwright runs: `DISPLAY=172.22.48.1:1.0`
- VcXsrv must be running on `:1` (`vcxsrv.exe :1 -multiwindow -ac -noclipboard` from Windows)

**Instagram page structure**
- Collections index: `/{username}/saved/` — named collections as `<a>` links
- Collection URL pattern: `/{username}/saved/{slug}/{numeric-id}/`
- Post links on collection page: `a[href="/p/{shortcode}/"]`, `a[href="/reel/{shortcode}/"]`
- Scroll required for large collections; small collections fully load without scroll
- Cookie persistence confirmed — session loads on second run without re-login
- Saved index requires 4s wait after domcontentloaded for collection links to render

**Type detection (validated)**
- `/reel/` in URL → Reel
- `/p/` + `button[aria-label='Next']` → Carousel
- `/p/` + `video` + `button[aria-label='Toggle audio']` → Reel (cross-posted to feed)
- `/p/` + no video → Post

**Environment**
- Playwright 1.60.0, notion-client 3.1.0, python-dotenv installed
- Username: `aishwarya_heartfilia`
- `session_cookies.json` exists and valid
