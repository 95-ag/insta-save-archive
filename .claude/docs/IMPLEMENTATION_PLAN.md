# IMPLEMENTATION_PLAN.md

## Overview

Solo developer implementation plan for the Instagram → Notion Knowledge Ingestion Pipeline. Optimized for iterative development, operational simplicity, and debuggability. Each phase is independently shippable and validated before the next begins.

**Stack:** Claude Max, Claude Code, Playwright (Python), Notion API (Python client), local filesystem.

**Working assumption:** No infrastructure beyond a local machine and a Notion workspace. Scripts are run manually or via simple CLI invocation. No daemons, workers, queues, or external services in Phase 1 or 2.

---

## Phase 1 — Ingestion MVP

### Goal

Crawl one Instagram saved collection, extract post metadata, and write deduplicated entries to Notion. Prove the end-to-end pipeline path is viable before building anything else.

### Deliverables

- `session.py` — Instagram login, session persistence (cookie storage), health check, and re-auth flow
- `crawler.py` — Traverse a named collection, enumerate saved items, extract URL + metadata per item
- `extractor.py` — Parse raw Playwright page state into structured metadata (shortcode, author, caption, type, collection, posted date)
- `notion_client.py` — Thin wrapper over Notion API: create page, query by `source_id`, update status
- `ingest.py` — Orchestration entry point: crawl → extract → deduplicate → write to Notion
- `config.py` — Environment-based config (Notion token, database ID, target collection name, batch size)
- `.env.example` — Template for required environment variables
- `README.md` — Setup, authentication steps, how to run, how to debug common failures

**Notion database:** Create the Stage 1 schema manually in Notion before first run. Do not automate database creation.

### Data Flow

```
ingest.py
  → session.py      (load/validate cookies, re-auth if needed)
  → crawler.py      (Playwright: enumerate collection items)
  → extractor.py    (parse page state → structured dict)
  → notion_client.py (dedup check → write or skip)
```

No intermediate persistence layer. Results go directly to Notion. Interruption recovery relies on deduplication: re-running from the start is safe.

### Schema Populated in Phase 1

| Field | Notes |
|---|---|
| `title` | Placeholder: `{author} — {shortcode}`. AI-generated titles are Phase 2+. |
| `source_id` | IG shortcode. Deduplication key. |
| `ig_link` | Canonical post URL (`https://www.instagram.com/p/{shortcode}/`) |
| `author` | IG handle without @. |
| `type` | Reel / Post / Carousel / Unknown |
| `collection` | Multi-select. Collection name(s). |
| `caption` | Raw caption text. Null if unavailable. |
| `posted_date` | From IG metadata. Null if unavailable. |
| `status` | Set to `Imported` on creation. Never overwritten by ingestion. |

### Deduplication Logic

1. Extract shortcode from URL using regex against `/reel/`, `/p/`, `/tv/` patterns.
2. Before any Notion write: query `source_id` property for that shortcode.
3. If match exists: skip silently. Log that it was skipped.
4. If no match: create new page.
5. Never modify user-generated fields during ingestion runs. Pipeline-managed fields (e.g. `collection`, `caption`) may be refreshed in future metadata update runs, but fields the user may have edited manually must never be overwritten by automation.

### Session Management

- Store session cookies in a local JSON file (e.g., `session_cookies.json`). Exclude from version control.
- On each run: load cookies, navigate to Instagram, check for a known authenticated DOM element.
- If health check fails: run headful login flow, prompt for 2FA if required, save new cookies.
- Do not attempt to automate 2FA. Pause and wait for manual input.
- Log session state at startup: valid / expired / re-authed.

### Non-Goals for Phase 1

- OCR, transcript extraction, or any media processing
- AI-generated titles, summaries, insights, or tags
- Routing, similarity detection, embeddings, or downstream databases
- Processing queue automation or status transitions beyond setting `Imported`
- Multi-collection bulk crawl
- Headless browser hardening or anti-detection measures
- Error retry logic beyond logging
- Any persistence layer beyond Notion and local cookie file

### Success Criteria

- Can log in to Instagram, persist session, and validate health on subsequent runs without re-logging in
- Can enumerate all saved items in a specified collection
- Can extract `source_id`, `ig_link`, `author`, `caption`, `type`, `posted_date` for each item
- Can create Notion entries with correct field mapping for each extracted item
- Re-running the same collection produces zero new Notion entries (deduplication holds)
- Interrupting mid-run and restarting does not create duplicates or corrupt existing entries
- Null fields are stored as null, not as empty strings or placeholder text (except `title`)
- A row written with only `source_id + ig_link + author + collection` is treated as a valid success (partial data is acceptable)

### Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Instagram DOM changes break selectors | High | Isolate all selectors in one file. Log raw HTML on extraction failure. |
| Login flow blocked by Instagram (bot detection) | Medium | Run headful. Use real delays. Do not parallelize. |
| 2FA required and blocks automation | Medium | Detect 2FA prompt, pause, accept manual input, continue. |
| Notion API rate limits | Low | Add `time.sleep(0.4)` between page creates. Tune batch size via config. |
| Incomplete metadata (null caption, missing date) | Expected | Treat nulls as valid. Never block on missing optional fields. |
| Session expires mid-run | Medium | Detect auth failure during crawl, stop, prompt re-auth, resume from last successfully processed item. |

### Exit Criteria

Phase 1 is complete and Phase 2 may begin when:

- At least one full collection (20+ items) has been successfully ingested end-to-end
- A re-run of that same collection produces zero new Notion entries
- Notion rows contain correct data with no placeholder strings in nullable fields
- A deliberate interruption mid-run followed by a restart completes without duplicates
- Session re-auth has been tested at least once (manually expire cookies and re-run)

---

## Phase 2 — Deep Extraction

### Goal

For items manually promoted to `Queued` status in Notion, perform deep extraction: pull transcript, OCR text, and carousel slide text. Store raw outputs in Notion. Enable reprocessing without re-scraping.

### Deliverables

- `extract_runner.py` — Query Notion for `Queued` items, run extraction per item, update status to `Extracted` or `Failed`
- `extractor_deep.py` — Per-item deep extraction: transcript (from video), OCR (from frames), carousel text (slide-by-slide)
- `extractor_deep.py` is modular: each extraction type (transcript / OCR / carousel) is a separate callable function, not a monolith
- Carousel extraction may use OCR, vision models, or a hybrid approach depending on content structure — many carousel slides are layouts and structured graphics rather than simple OCR targets; the approach should be chosen per-item type during Phase 2 implementation
- Updated `notion.py` — Support for writing Stage 2 fields, including `raw_extraction` (JSON string), `last_processed_at`, `processing_version`
- `extract.py` — CLI: accepts `--limit N` and `--source_id X` flags for targeted runs

**No AI calls in Phase 2.** Transcript = spoken word captured from video audio. OCR = text detected in frames. Neither requires Claude at this stage. AI enrichment is Phase 3.

### Data Flow

```
extract.py
  → notion.py          (query Queued items, paginate)
  → session.py         (reuse Phase 1 session management)
  → extractor_deep.py  (navigate to ig_link, extract media)
    → transcript()
    → ocr_frames()
    → carousel_text()
  → notion.py          (write fields, set status → Extracted)
```

### Fields Populated in Phase 2

| Field | Source |
|---|---|
| `transcript` | Extracted from video audio |
| `ocr_text` | Text detected from video frames / image overlays |
| `raw_extraction` | Full JSON: all extraction outputs, timestamps, method used. Never overwritten on reprocess — append with version key. |
| `last_processed_at` | Timestamp of this extraction run |
| `processing_version` | e.g., `v1.0-base`. Increment minor for prompt/method changes. |
| `status` | `Extracted` on success. `Failed` + `failure_notes` on any error. |

**Dropped:** `transcript_available` — always equals `transcript is not None`; removed from schema and code.

### Non-Goals for Phase 2

- AI summarization, insight extraction, or tagging (Phase 3)
- `expanded_summary` and `key_insights` fields (Phase 3)
- Routing or downstream database writes
- Automated queue promotion (manual `Queued` selection only)
- Bulk reprocessing tooling (single-item targeted reprocessing is sufficient)

### Success Criteria

- Can query all `Queued` items from Notion and process them sequentially
- Transcript extraction quality is sufficient to support summarization for a meaningful subset of Reel content (audio quality, spoken content density, and language vary significantly across saves — evaluate empirically against the first real batch)
- OCR extraction captures visible on-screen text for at least one carousel item end-to-end
- `raw_extraction` is populated as valid JSON for every processed item, regardless of output quality
- Failed items have `status = Failed` and a human-readable message in `failure_notes`
- Re-running extraction on an already-`Extracted` item does not overwrite `raw_extraction` (appends under new version key)
- `processing_version` is updated on every run

### Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Video audio extraction is unreliable without dedicated tools | High | Evaluate yt-dlp + whisper.cpp locally before committing. Gate on quality threshold. |
| OCR accuracy poor without preprocessing | Medium | Store raw OCR output. Prompt improvements are free with `raw_extraction` preserved. |
| Instagram re-auth required mid-extraction batch | Medium | Reuse Phase 1 session health check before each batch. |
| `raw_extraction` grows large (carousel + transcript + OCR) | Low | Notion long text limit is generous. Monitor; defer archiving decision until Phase 3. |

### Exit Criteria

Phase 2 is complete and Phase 3 may begin when:

- A batch of 10+ `Queued` items has been processed end-to-end
- `raw_extraction` is populated for all processed items
- At least one Reel with audible speech has a usable transcript
- At least one Carousel has structured slide text extracted
- Re-running extraction on processed items preserves existing `raw_extraction` data
- Human review of 5+ `Extracted` items confirms extraction quality is sufficient to support summarization

---

## Phase 3 — AI Enrichment

### Goal

Run title generation (Ollama, automated) and Claude-powered summarization over `Extracted` items. Populate `title`, `summary`, and `externals`. Support iterative reprocessing as prompts improve.

### Deliverables

- `titler.py` — Ollama title generation. Reads Queued + Extracted items; writes `title` only; no status change.
- `summarize.py` — `--prepare` (fetch Extracted batch → write prompt) + `--upload` (write Claude results to Notion).
- `prompts/` — Versioned prompt templates. One file per extraction type.
- Updated `notion.py` — `write_title` (title only, no status), `write_summary` (summary + externals → status: `Summarized`).

**Title pass is decoupled from the status machine.** `scripts/title.py` reads both `Queued` and `Extracted` items and writes only the title field — no status change. Title generation does not gate Claude summarization.

**Claude Code mechanism (locked).** `scripts/summarize.py` operates as a two-step CLI: `--prepare` writes a prompt file; the user runs a Claude Code session to fill it; `--upload` writes the results. No API key required — Claude Max only. No LangChain, no agent frameworks, no orchestration.

### Fields Populated in Phase 3

| Field | Engine | Notes |
|---|---|---|
| `title` | Ollama local (automated) | Runs on Queued + Extracted items. Caption is primary input. Does NOT change status. |
| `summary` | Claude Code session | Full content extraction as clean prose — filler stripped, all information preserved. Paragraph-separated sections. |
| `externals` | Claude Code session | Same session as summary. Grouped by category: Tools, Brands, Creators, Links, Techniques, Locations. |
| `processing_version` | Code | Updated on each enrichment run. |
| `last_processed_at` | Code | Updated on each enrichment run. |

**Dropped fields:** `key_insights` removed — key takeaways are embedded in `summary`. `detected_entities` removed — merged into `externals`. `transcript_available` removed — redundant with `transcript is not None`.

### Non-Goals for Phase 3

- Semantic tagging (Phase 4 — embedding cluster batch job; defer)
- `route_target` (Phase 5 — deterministic from collection config; defer)
- Downstream database writes
- Cross-item operations (dedup clustering, similarity)

### Success Criteria

- `summary` is sufficient to understand the content without watching/reading the original
- `externals` captures tools, links, brands, creators visible in the content — links in caption are not missed
- Reprocessing an already-summarized item with an updated prompt overwrites `summary` + `externals` but not `raw_extraction`
- Cost per item is known and acceptable before running on full backlog

### Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Claude Max usage limits hit during bulk summarization | Medium | Tune `_CONTENT_BUDGET` in summarize.py. Run in small batches. Schedule non-peak. |
| Prompt quality poor on first pass | Expected | `raw_extraction` preserved. Reprocess is free. Treat first pass as calibration. |
| Summary fields overwritten on accidental re-run | Low | `--upload` skips items if `summary` already set unless `--force` is passed. |

### Exit Criteria

Phase 3 is complete when:

- All priority items have `title`, `summary`, and `externals` populated
- Human review confirms `summary` replaces watching/reading the original, and `externals` captures links/tools visible in content
- Reprocessing with an updated prompt has been tested at least once without data loss
- The cost and operational overhead of the Claude Code session mechanism is understood

---

## Phase 4 — Collection Reorganisation *(After Phase 3)*

### Goal

Audit and restructure the saved collection taxonomy. Some collections are too narrow (near-duplicates), some too broad (catch-all), and some candidates for retirement. Produce a cleaner, more useful set of collections before any downstream routing or distribution work.

### Trigger

Begin after Phase 3 enrichment is complete and spot-checked. Do not reorganise during active enrichment — collection names are live tags on Notion rows.

### Scope

- **Audit:** review all 43 collections against actual content (now that items are enriched and titled). Identify: duplicates, near-duplicates, overly broad, overly narrow, empty/retired.
- **Decide:** produce a target collection list — merges, renames, retirements, new collections.
- **Migrate Notion:** bulk-update `collection` multi-select on affected pages (rename old → new, merge tags, remove retired).
- **Update config:** update `config/collections.json` — group assignments, extract flags, enrichment_order.
- **Retire:** mark retired Instagram collections as archived in `collections.json` (extract=false, group="Retired"). Do not delete Notion pages.

### Constraints

- Collection renames must be reflected on all Notion pages before `collections.json` is updated — otherwise query filters by collection name break mid-pipeline.
- New collections added to Instagram after Phase 3 should be discovered via `list_collections.py --update` (smart merge).
- Any collection that already has `Summarized` items must not be renamed without updating those pages first.

### Deliverables

- Updated `config/collections.json` with revised groups, extract flags, enrichment_order
- Bulk migration script (one-off, not committed) to rename/merge collection tags in Notion
- Updated `README.md` collection section if group structure changes significantly

---

## Phase 5 — Distribution *(Future)*

Deferred. Do not design or implement until Phase 3 is validated and the save-to-downstream relationship (one-to-one vs. one-to-many, schema transformation approach) is resolved.

Trigger for Phase 5 planning: at least 50 items have reached `Summarized` status and downstream routing targets are confirmed.

### Architectural Direction (decided Phase 3)

**`route_target` is deterministic from collection — not AI-generated.** Collection membership maps to destination via config:
- Recipe collections → Recipe Notion DB (fields: name, ingredients, steps, time, difficulty)
- Coding / Web / Job Hunt → Learning Notion page (fields: concept, steps, tools, resources)
- Biz / Clothing → Market research Notion DB (fields: brand, observation, opportunity, target demo)

**Collection-typed Claude prompts.** The Claude summarize pass selects an extraction template based on collection → route_target. Different destinations get different structured output — a recipe gets recipe-shaped extraction, market research gets brand/opportunity extraction. The generic `summary` serves as fallback for uncategorised collections.

**`suggested_next_step` not needed.** The route_target IS the next step. Knowing the destination encodes the action.

**`tags` via embedding clusters.** Not per-item LLM. Run once across all `summary` values after bulk summarization — cluster by semantic similarity, name clusters. Batch job, not pipeline stage.

---

## Development Approach

The project follows an iterative, phase-gated development workflow.

Each phase:

1. Builds a complete end-to-end capability.
2. Is independently usable.
3. Has explicit success criteria.
4. Must be validated against real Instagram content before the next phase begins.
5. Produces working software before additional abstractions are introduced.

Future phases must not be implemented ahead of validated earlier phases. Real-world findings take precedence over assumptions made during planning. The implementation plan is the source of truth for sequencing. Superpowers workflows, planning tools, and execution patterns are advisory and must not override phase boundaries, MVP scope, or project constraints.

### Preferred Workflow

Plan → Review → Implement → Test on real collection → Debug → Refine → Validate against success criteria → Freeze phase → Begin next phase

### Superpowers Usage

Use Superpowers to:

- Break approved phases into concrete tasks
- Review implementation plans
- Debug failures
- Review code quality
- Identify risks and edge cases

Do not use Superpowers to:

- Introduce future-phase functionality
- Add infrastructure not required by the current phase
- Redesign the architecture without evidence from implementation
- Bypass phase validation requirements

When implementation conflicts with planning assumptions, update the plan based on observed behavior rather than increasing architectural complexity. The goal is operational simplicity, visibility, and reliable progress rather than maximum automation.

---

## Development Principles

**Run everything locally.** No cloud functions, no hosted services, no workers. Scripts are invoked manually from the command line until operational volume justifies otherwise.

**One collection at a time.** Validate end-to-end on a small, well-understood collection before running on the full backlog.

**Logs over dashboards.** Structured stdout logging with timestamps and item-level status. No monitoring infrastructure.

**Fail loudly, preserve data.** On any extraction or API error: log the full error, write `Failed` status to Notion, continue to the next item. Never silently skip. Never delete or overwrite good data to recover from a failure.

**Config over code.** Batch sizes, target collection, API credentials, and processing flags live in `.env` or CLI flags — never hardcoded.

**Prefer visible, debuggable workflows over hidden automation.** Each pipeline stage should be invocable independently, produce human-readable output, and make its state inspectable in Notion without needing to read code. Automation that obscures what happened is a liability, not an asset.

**Claude Code for iteration.** Use Claude Code for prompt iteration, selector debugging, and schema adjustments. Do not use it to introduce architectural complexity not justified by the current phase.

---

## File Layout

```
pipeline/                       # Importable library (pip install -e .)
├── config.py                   # Config dataclass, load_config()
├── notion.py                   # All Notion API calls
├── collections.py              # Loads config/collections.json
├── session.py                  # Login, cookie persistence, health check
├── crawler.py                  # Collection traversal (scroll_harvest)
├── extractor.py                # Single-post metadata extraction
├── extractor_deep.py           # Transcript, OCR, carousel (Phase 2)
├── ingest.py                   # Ingest orchestration (no CLI)
├── runner.py                   # run_priority_stage() — shared bucketed loop
├── extract_runner.py           # run_extract_stage(), run_extract_item()
├── titler.py                   # generate_title() — Ollama local
├── observability.py            # StageProgress, setup_logging()
└── display.py                  # VcXsrv / X11 management

scripts/                        # CLI entry points
├── ingest.py                   # Single-collection ingest
├── ingest_batch.py             # All collections in priority order
├── list_collections.py         # Discover → config/collections.json
├── promote.py                  # Promote Imported → Queued
├── extract.py                  # Phase 2 deep extraction
├── title.py                    # Ollama title generation (Queued + Extracted)
└── summarize.py                # Claude Code summary + externals (--prepare / --upload)

config/
├── collections.json            # Gitignored — real data (43 entries)
└── collections.example.json    # Committed — 2-entry placeholder

prompts/
└── enrichment_v1.0-enrich.txt

.env                            # Secrets (gitignored)
session_cookies.json            # Persisted session (gitignored)
```
