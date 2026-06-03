# Session

## Current state

**Phase 2 COMPLETE.** All exit criteria met. Phase 3 may begin.

## Phase 2 exit criteria (verified 2026-06-03)

- 10 items processed end-to-end (expanded=9 failed=0 on batch run + 1 from T7 smoke test)
- `raw_extraction` populated as valid JSON for all 10 items
- 5 reels with audible speech have usable transcripts; `transcript_available=true`
- 1 music-only reel correctly flagged `transcript_available=false`
- 4 carousels have structured slide-ordered OCR text
- No `Failed` items in batch run
- Idempotency verified: re-running appends new version key, prior keys preserved
- Full transcripts stored untruncated (chunked rich_text properties)
- `processing_version` and `last_processed_at` set on every item

## Locked decisions (post-validation, permanent)

| Concern | Decision |
|---|---|
| Transcript engine | yt-dlp + faster-whisper `base` int8 |
| OCR engine | RapidOCR (`rapidocr-onnxruntime==1.4.4`) |
| Carousel slide acquisition | `button[aria-label='Next']` stepping + `page.query_selector_all("img")` filtered on `/t51.82787-15/` or `/t51.71878-15/` path segments |
| Long-text storage | `_rich_text_chunked` into property (no page-body blocks) |
| Cookie format for yt-dlp | JSON → Netscape at runtime into `tmp/cookies.txt` |
| `article` selector | Dead — do not use |

## Phase 2 — V1 findings (transcript engine, 2026-06-03)

**Decision: yt-dlp + faster-whisper `base` int8. Locked. whisper.cpp not needed.**

- yt-dlp downloads private saved-reel audio via `session_cookies.json` ✓
- Cookie format: JSON → Netscape conversion required (one-time, baked into pipeline via `tmp/cookies.txt`)
- faster-whisper pip-installed clean on WSL2 (ctranslate2 manylinux wheels, no build step)
- ffmpeg installed as system dep (`apt install ffmpeg`); shared by yt-dlp + faster-whisper

**Reel results (base model, int8 CPU):**

| Shortcode | Author | Words | Pass/Fail | Notes |
|---|---|---|---|---|
| DYet7HfCwpj | themodernhenry | 0 | Expected empty | Music/ambient only; lang_p=0.42; `transcript_available=false` correct |
| DYUjq6US1Dg | cyborggirll | 151 | Pass | ATS/resume workflow reel; message fully conveyed |
| DWrYjRjDE5T | louyi.ux | 4 | Pass | Short spoken clip; VAD trimmed correctly |
| DVbTnzPkgJ3 | mahimahans111 | 238 | Pass | LinkedIn optimization; clear narrative |
| DV-tBdJE923 | byjoeym | 153 | Pass | Figma/AI workflow; accurate |

Minor transcription errors (e.g. "clawed" for "Claude") are acceptable at `base`; `small` available as upgrade if Phase 3 shows quality issues. No hallucination/looping observed.

**`transcript_available` logic confirmed:** empty output + lang_p < ~0.5 = false. Music-only reels work as designed.

## Phase 2 — V2 findings (OCR engine, 2026-06-03)

**Decision: RapidOCR (`rapidocr-onnxruntime`). Locked. Tesseract not used.**

Tested on 16 slides across 4 carousels (4 slides each from DYggAooGB8g, DYHgVqjjEv6, DWD7CQeiLsA, DVd3X6-DDcz).

- RapidOCR wins on stylized/overlaid/low-contrast text (the dominant carousel type)
- Tesseract wins only on clean body-text slides — but RapidOCR is adequate there too
- Both engines fail on complex photo-text composites; this is expected and acceptable — raw output preserved for future prompt/preprocessing improvements
- RapidOCR is pip-only (onnxruntime), no system dep, clean WSL2 install

**V3 confirmed simultaneously (carousel mechanics):**
- `CAROUSEL_NEXT_SEL` (`button[aria-label='Next']`) steps all carousels correctly
- Slide image URLs pull cleanly from DOM without screenshots
- `article` selector returns nothing in current IG DOM — use `page.query_selector_all("img")` filtered by content path markers
- CDN domain is `instagram.fblr22-*.fna.fbcdn.net` (not `cdninstagram`) — filter on `/t51.82787-15/` and `/t51.71878-15/` path segments to identify content images vs profile pics
- Downloads via `urllib.request` with session cookies work cleanly

## Phase 1 exit criteria (verified)

- 36 posts ingested from "Job Hunt" collection
- Re-run produces 0 new rows (full dedup via `source_id`)
- Restart after interrupt produces 0 duplicates
- Null fields stored as null (no empty strings)
- 36 unique entries confirmed in Notion

## Open issues / technical debt

- **Title is a placeholder** — `{author} — {shortcode}`. Intentional per plan; AI-generated titles deferred to Phase 3.
- **`data_source_id` fetched per-run** — `query_by_source_id` calls `databases.retrieve` on every invocation to resolve the data_source_id. Could be cached once per run. Not a correctness issue; acceptable at current scale.
- **No retry logic** — failed extractions or Notion writes are logged and skipped. Plan explicitly defers retry logic to a future phase.
- **`all-posts` collection untested** — catch-all view not validated. Intentional; must only be used after all named collections are ingested.

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
