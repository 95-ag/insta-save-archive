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
| `pipeline_status` | Set to `Imported` on creation. Never overwritten by ingestion. |

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

- `queue_runner.py` — Query Notion for `Queued` items, run extraction per item, update status to `Expanded` or `Failed`
- `extractor_deep.py` — Per-item deep extraction: transcript (from video), OCR (from frames), carousel text (slide-by-slide)
- `extractor_deep.py` is modular: each extraction type (transcript / OCR / carousel) is a separate callable function, not a monolith
- Carousel extraction may use OCR, vision models, or a hybrid approach depending on content structure — many carousel slides are layouts and structured graphics rather than simple OCR targets; the approach should be chosen per-item type during Phase 2 implementation
- Updated `notion_client.py` — Support for writing Stage 2 fields, including `raw_extraction` (JSON string), `last_processed_at`, `processing_version`
- `run_extraction.py` — CLI: accepts `--limit N` and `--source_id X` flags for targeted runs

**No AI calls in Phase 2.** Transcript = spoken word captured from video audio. OCR = text detected in frames. Neither requires Claude at this stage. AI enrichment is Phase 3.

### Data Flow

```
run_extraction.py
  → notion_client.py   (query Queued items, paginate)
  → session.py         (reuse Phase 1 session management)
  → extractor_deep.py  (navigate to ig_link, extract media)
    → transcript()
    → ocr_frames()
    → carousel_text()
  → notion_client.py   (write fields, set status)
```

### Fields Populated in Phase 2

| Field | Source |
|---|---|
| `transcript` | Extracted from video audio |
| `ocr_text` | Text detected from video frames / image overlays |
| `raw_extraction` | Full JSON: all extraction outputs, timestamps, method used. Never overwritten on reprocess — append with version key. |
| `transcript_available` | Checkbox. True only if transcript produced usable output (non-empty, non-junk). |
| `last_processed_at` | Timestamp of this extraction run |
| `processing_version` | e.g., `v1.0-base`. Increment minor for prompt/method changes. |
| `pipeline_status` | `Expanded` on success. `Failed` + `failure_notes` on any error. |

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
- `transcript_available` is false for items where extraction returned empty or junk output
- Failed items have `pipeline_status = Failed` and a human-readable message in `failure_notes`
- Re-running extraction on an already-`Expanded` item does not overwrite `raw_extraction` (appends under new version key)
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
- Human review of 5+ `Expanded` items confirms extraction quality is sufficient to support summarization

---

## Phase 3 — AI Enrichment

### Goal

Run Claude-powered summarization and insight extraction over `Expanded` items using `raw_extraction` as the primary input. Populate `expanded_summary`, `key_insights`, and `extracted_externals`. Support iterative reprocessing as prompts improve.

### Deliverables

- `enrichment.py` — Query `Expanded` items, call Claude API with extraction payload, write enrichment fields
- `prompts/` — Directory of versioned prompt templates. One file per enrichment type.
- Updated `notion_client.py` — Write enrichment fields without touching `raw_extraction`
- `run_enrichment.py` — CLI with `--limit`, `--source_id`, `--dry-run` flags

**Use Claude-based enrichment.** The implementation mechanism is intentionally undecided and should be selected when Phase 3 begins based on cost, workflow, and operational simplicity. Claude Max, Claude Code, or the API are all viable options. No LangChain, no agent frameworks, no orchestration regardless of mechanism.

### Fields Populated in Phase 3

| Field | Source |
|---|---|
| `expanded_summary` | AI-generated. Full content summary from `raw_extraction`. |
| `key_insights` | AI-generated. Distilled, transferable knowledge. |
| `extracted_externals` | AI-extracted tools, links, products, people, locations. Structured text. |
| `processing_version` | Updated to reflect enrichment prompt version |
| `last_processed_at` | Updated on each enrichment run |

### Non-Goals for Phase 3

- Semantic tagging (Stage 3 in PROJECT.md — defer unless needed)
- `route_target`, `suggested_next_step`, `duplicate_confidence` (Stage 3)
- Downstream database writes
- Cross-item operations (dedup clustering, similarity)

### Success Criteria

- `expanded_summary` is sufficient to understand the content without watching/reading the original
- `key_insights` contains extractable, actionable knowledge for at least 80% of test items
- Reprocessing an already-enriched item with an updated prompt overwrites enrichment fields but not `raw_extraction`
- Dry-run mode prints the Claude prompt and expected output without writing to Notion
- Cost per item is known and acceptable before running on full backlog

### Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Claude Max usage limits hit during bulk enrichment | Medium | Tune `--limit` flag. Run in small batches. Schedule non-peak. |
| Prompt quality poor on first pass | Expected | `raw_extraction` preserved. Reprocess is free. Treat first pass as calibration. |
| Enrichment fields overwritten on accidental re-run | Low | Add `--force` flag requirement to overwrite existing enrichment. Default: skip already-enriched items. |

### Exit Criteria

Phase 3 is complete when:

- A batch of 20+ items has `expanded_summary` and `key_insights` populated
- Human review (status: `Reviewed`) confirms extraction quality meets the bar of replacing the original source as reference
- Reprocessing with an updated prompt has been tested at least once without data loss
- The cost and operational overhead of the chosen enrichment mechanism is understood before running on the full backlog

---

## Phase 4 — Distribution *(Future)*

Deferred. Do not design or implement until Phase 3 is validated and the save-to-downstream relationship (one-to-one vs. one-to-many, schema transformation approach) is resolved.

Trigger for Phase 4 planning: at least 50 items have reached `Reviewed` status and downstream routing targets are confirmed.

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
/
├── .env                        # Secrets and config (gitignored)
├── .env.example
├── config.py                   # Loads env vars, exposes typed config
├── session.py                  # Login, cookie persistence, health check
├── crawler.py                  # Collection traversal
├── extractor.py                # Metadata parsing (Phase 1)
├── extractor_deep.py           # Transcript, OCR, carousel (Phase 2)
├── notion_client.py            # Notion API wrapper
├── ingest.py                   # Phase 1 orchestration entry point
├── queue_runner.py             # Phase 2 orchestration entry point
├── enrichment.py               # Phase 3 Claude enrichment
├── run_extraction.py           # Phase 2 CLI
├── run_enrichment.py           # Phase 3 CLI
├── prompts/                    # Versioned prompt templates
│   └── enrichment_v1.txt
├── session_cookies.json        # Persisted session (gitignored)
└── README.md
```

Phases 2 and 3 files are not created until their phase begins.
