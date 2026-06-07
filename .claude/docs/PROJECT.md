# Instagram → Notion Knowledge Ingestion Pipeline (Insta Save Archive)

## Purpose

A personal knowledge ingestion system that imports Instagram saved content into Notion, extracts structured knowledge from it, and routes high-value items into downstream specialized systems.

Instagram is the first source. The architecture should remain source-agnostic over time.

The primary value is reusable structured knowledge extraction — not the scraping itself. The goal is to reach a state where the original Instagram content is no longer needed as a reference.

---

## Architectural Principles

**Ingestion-first.** Build ingestion and extraction before any routing or distribution logic. Stable structured data first; automation of downstream workflows later.

**Source preservation.** Preserve enough source context and extracted data to minimize future dependence on Instagram as the canonical source. This is the purpose of transcripts, OCR text, raw extraction payloads, summaries, and archived assets.

**Idempotency.** All imports and pipeline runs must be safe to re-run. No duplicate Notion entries. No repeated work on already-processed items.

**Reprocessing as a first-class operation.** Support iterative reprocessing as extraction prompts, models, and schemas improve. Raw extraction payloads must be preserved to enable this without re-scraping.

---

## Constraints

- Instagram provides no API access to saved collections. All ingestion requires browser automation.
- Claude Max has usage/time limits. Processing should be cost-aware. Batch sizes should be tunable.
- Notion API is rate-limited. Ingestion clients must respect this.
- Human review remains part of the workflow until extraction quality is validated.

---

## Pipeline Flow

```
STAGE 1 — Ingest
  scripts/ingest.py / scripts/ingest_batch.py
  Writes: title (placeholder), ig_link, author, source_id, caption, type, posted_date
  Status: → Imported

        ↓ manual: set status=Queued + priority in Notion

STAGE 2 — Extract
  scripts/extract.py
  Input:  status = Queued   (priority order: High → Medium → Low → unprioritised)
  Writes: transcript, ocr_text
  Status: → Extracted

STAGE 3 — Summarize  [Claude Code — manual batch]
  scripts/summarize.py --prepare → Claude Code session → scripts/summarize.py --upload
  Input:  status = Extracted  (highest non-empty priority bucket per run)
  Writes: summary, externals
  Status: → Summarized

STAGE 4 — Tag  [NOT YET IMPLEMENTED]
  scripts/tag.py
  Input:  status = Summarized
  Writes: tags (multi-select)
    • Items with summary → embedding cluster → content-derived tags
    • Items without summary → generic tag copied from collection config
  Status: → Tagged

STAGE 5 — Route  [NOT YET IMPLEMENTED]
  Deterministic config mapping: collection name → route_target field
  Writes: route_target (select)
  No status change (or: → Routed — TBD at implementation time)

TITLE PASS  [independent — scripts/title.py]
  Input:  status = Queued OR Extracted  (placeholder title check; priority order)
  Writes: title field only
  Status: no change — title is decoupled from the status machine
  Run:    any time after Imported; does not block or gate other stages
```

---

## Status Machine

| Status | Set by | Meaning |
|---|---|---|
| `Imported` | ingest | Row created; basic metadata written |
| `Queued` | manual | Marked for extraction; priority set |
| `Extracted` | extract.py | Transcript and/or OCR available |
| `Summarized` | summarize.py | summary + externals written by Claude |
| `Tagged` | tag.py *(future)* | Semantic tags assigned |
| `Routed` | TBD *(future)* | route_target assigned |
| `Failed` | any stage | Error occurred; see failure_notes |

`Enriched` status was removed. Title generation no longer gates Claude summarization.

---

## Build Order

1. Finalize and create Notion database schema
2. Implement Instagram session management (login, persistence, health check, re-auth)
3. Build collection crawler (enumerate saves per collection, extract URL + basic metadata)
4. Implement Notion ingestion with deduplication
5. Add processing queue (manual status transitions initially)
6. Implement deep extraction (transcript, OCR, carousel text)
7. Add AI enrichment (summary, externals, tags, route_target)
8. Add downstream routing workflows

Stages 4–5 are future scope. Do not build ahead of validated earlier stages.

---

## Architecture

```
Browser automation
    ↓
Instagram Web (saved collections)
    ↓
Metadata extraction (URL, author, caption, type, collection, date)
    ↓
Notion API (canonical ingestion layer + processing queue)
    ↓
Deep extraction pipeline (transcript, OCR, carousel)
    ↓
Claude summarization (summary + externals)
    ↓
Embedding tagging + deterministic routing
    ↓
Downstream knowledge systems
```

### Deduplication

- `source_id` is the canonical deduplication key (IG shortcode)
- Before writing to Notion: query by `source_id`. If exists, skip or update non-destructively.
- Never rely on Notion's Created Time or page title for deduplication.

---

## Failure Handling

- Failures must be non-destructive. Partial data is preserved whenever possible.
- Failed items remain in Notion with `status = Failed` and errors written to `failure_notes`.
- No automatic retries. Failed items are retried explicitly after the underlying issue is resolved.
- A row with only URL + author + collection is still a valid, useful ingestion result.

---

## Notion Database Schema

### Stage 1 — Ingest fields

| Property | Type | Notes |
|---|---|---|
| `title` | Title | AI-generated descriptive title (placeholder at ingest). |
| `source_id` | Text | IG shortcode. Primary deduplication key. |
| `ig_link` | URL | Canonical post URL. |
| `author` | Text | IG handle without @. |
| `type` | Select | Reel, Post, Carousel, IGTV, Story Capture, Unknown |
| `collection` | Multi-select | All collections this save belongs to. |
| `caption` | Long text | Raw caption, verbatim. |
| `posted_date` | Date | From IG metadata. Nullable. |
| `imported_at` | Created time | Auto. Never overwrite. |
| `status` | Select | See status machine above. |
| `priority` | Select | High, Medium, Low — set manually per item. |
| `failure_notes` | Long text | Pipeline errors, broken URLs, extraction failures. |

### Stage 2 — Extract fields

| Property | Type | Notes |
|---|---|---|
| `transcript` | Long text | Raw spoken content from reels/video. |
| `ocr_text` | Long text | Visually detected text from frames/slides. |
| `raw_extraction` | Long text | Full JSON extraction payload. Preserved for reprocessing. Never overwritten. |
| `last_processed_at` | Date | Updated on every pipeline write. |
| `processing_version` | Text | e.g. `v1.2-carousel`. Increment minor on prompt changes, major on schema changes. |

### Stage 3 — Summarize fields

| Property | Type | Notes |
|---|---|---|
| `summary` | Long text | Full content extraction as clean prose. All information from transcript, OCR, caption — filler stripped. Structured with paragraph breaks. |
| `externals` | Long text | Tools, links, brands, creators, techniques, locations. Grouped by category with section headers. |

### Stage 4+ — Future fields (NOT YET IMPLEMENTED)

| Property | Type | Notes |
|---|---|---|
| `tags` | Multi-select | Semantic topic/theme tags. Generated as embedding clusters across all items — batch job, not per-item LLM. Items with summary get content-derived tags; others get generic tags from collection config. |
| `route_target` | Select | Destination system. Deterministic: derived from collection via config mapping, not AI-generated. |
| `review_notes` | Long text | Manual human commentary, corrections, implementation notes. |
| `similar_info` | Relation (self) | Links to related or duplicate saves. Populated by cross-item similarity batch job. |
| `source_assets` | Text / URL | Reference to any archived media or transcripts at an external location. |

**Dropped fields (removed during implementation):**
- `key_insights` — embedded in `summary` content extraction. Separate field added no value.
- `detected_entities` — redundant with `externals`.
- `transcript_available` — always equals `transcript is not None`; removed.
- `expanded_summary` — renamed to `summary`.
- `extracted_externals` — renamed to `externals`.
- `pipeline_status` — renamed to `status`.
- `processing_priority` — renamed to `priority`.
- `suggested_next_step` — collapsed into `route_target`: the destination IS the next step.

---

## Enrichment Engine Split (Locked)

| Field | Engine | Status |
|---|---|---|
| `title` | Ollama local (automated) | ✅ implemented |
| `summary` | Claude Code session (manual batch) | ✅ implemented |
| `externals` | Claude Code session (same session as summary) | ✅ implemented |
| `tags` | Embedding cluster batch job | ❌ not yet implemented |
| `route_target` | Config mapping (deterministic from collection) | ❌ not yet implemented |

**Local LLM scope is title only.** Everything else AI-generated goes through Claude. Local Ollama is fast and automated; Claude is manual-batch but higher quality for semantic extraction.

**Title can be derived from caption alone.** Transcript/OCR add context for thin-caption Reels but are not required. Title generation runs independently of the Extracted gate — it processes Queued and Extracted items without changing status.

**route_target is deterministic, not AI-generated.** Collection membership determines destination:
- Recipe collections → Recipe Notion DB
- Coding / Web / Job Hunt → Learning page
- Biz / Clothing → Market research DB

The config mapping (`collections.json`) encodes collection → route_target. No model needed to decide where a post goes.

**Collection-typed extraction (future):** Different Claude prompt per `route_target`. A recipe post needs recipe-shaped output (ingredients, steps, time); a market research post needs brand/opportunity output. The Claude summarize pass selects the extraction template based on the item's collection → route_target mapping.

---

## Key Files

```
pipeline/
  config.py            Config dataclass, load_config(), validate_notion_config()
  notion.py            All Notion API calls
  collections.py       Loads config/collections.json; ordered_for_ingestion()
  session.py           ensure_authenticated()
  crawler.py           scroll_harvest() — collection crawl
  extractor.py         extract_post() — single-post metadata
  extractor_deep.py    extract_transcript(), extract_carousel(), extract_ocr_frames()
  ingest.py            ingest_with_context() — no CLI
  runner.py            run_priority_stage() — shared priority-bucketed stage loop
  extract_runner.py    run_extract_stage(), run_extract_item()
  titler.py            generate_title(), validate_title_config()
  observability.py     StageProgress, setup_logging()
  display.py           ensure_display(), close_display() — VcXsrv / X11

scripts/
  ingest.py            Single-collection ingest
  ingest_batch.py      All collections in priority order
  list_collections.py  Discover + --update → config/collections.json
  queue.py             Promote Imported → Queued
  extract.py           Phase 2 deep extraction
  title.py             Ollama title generation (Queued + Extracted items)
  summarize.py         Claude Code summary + externals (--prepare / --upload)

config/
  collections.json     Gitignored — your real data (43 entries)
  collections.example.json   Committed — 2-entry placeholder template
```

---

## Operational Notes

**Session management is the highest operational risk.** Browser sessions are fragile. Validate session health checks and re-auth recovery before building anything that depends on an active session.

**Notion as canonical layer.** Treat the Notion database as the source of truth for pipeline state. Export a full JSON backup periodically. Do not rely on Notion indefinitely as the only copy of extracted data.

**Reprocessing is a first-class operation.** The `processing_version` + `raw_extraction` fields exist to support running improved prompts over existing data without re-scraping Instagram. Never overwrite `raw_extraction`.

**Start with one collection.** Do not bulk-import all collections before validating the pipeline end-to-end on a small batch. Pick a small, well-understood collection first.
