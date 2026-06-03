# Instagram → Notion Knowledge Ingestion Pipeline (Project Name: Insta Save Archive)

## Purpose

A personal knowledge ingestion system that imports Instagram saved content into Notion, extracts structured knowledge from it, and routes high-value items into downstream specialized systems.

Instagram is the first source. The architecture should remain source-agnostic over time.

The primary value is reusable structured knowledge extraction — not the scraping itself. The goal is to reach a state where the original Instagram content is no longer needed as a reference.

---

## Architectural Principles

**Ingestion-first.** Build the ingestion and extraction layers before any routing or distribution logic. Stable structured data first; automation of downstream workflows later.

**Source preservation.** The system should preserve enough source context and extracted data to minimize future dependence on Instagram as the canonical source. This is the purpose of transcripts, OCR text, raw extraction payloads, extracted summaries, and archived assets.

**Idempotency.** All imports and pipeline runs must be safe to re-run. No duplicate Notion entries. No repeated work on already-processed items.

**Reprocessing as a first-class operation.** Support iterative reprocessing as extraction prompts, models, and schemas improve over time. Raw extraction payloads must be preserved to enable this without re-scraping.

---

## Constraints

- Instagram provides no API access to saved collections. All ingestion requires browser automation.
- Claude Max has usage/time limits. Processing should be cost-aware. Batch sizes should be tunable.
- Notion API is rate-limited. Ingestion clients must respect this.
- Human review remains part of the workflow until extraction quality is validated.

---

## Build Order

Build and validate each stage before starting the next.

1. Finalize and create Notion database schema
2. Implement Instagram session management (login, persistence, health check, re-auth)
3. Build collection crawler (enumerate saves per collection, extract URL + basic metadata)
4. Implement Notion ingestion with deduplication
5. Add processing queue (manual status transitions initially)
6. Implement deep extraction (transcript, OCR, carousel text)
7. Add AI enrichment (summary, insights, tags, suggested next steps)
8. Add downstream routing workflows

Stages 7–8 are future scope. Do not build ahead of validated earlier stages.

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
AI enrichment (Claude)
    ↓
Downstream knowledge systems
```

### Deduplication

- `source_id` is the canonical deduplication key
- Derive `source_id` deterministically from the Instagram shortcode only (not full URL)
- Canonical shortcode extraction: parse from `/reel/{shortcode}/`, `/p/{shortcode}/`, or `/tv/{shortcode}/` — normalize to shortcode string
- Before writing to Notion: query by `source_id`. If exists, skip (or update non-destructively if explicitly reprocessing)
- Never rely on Notion's Created Time or page title for deduplication

---

## Processing Queue

State machine:

```
Imported → Queued → Expanded → Reviewed → Routed
```

Additional states: `Legacy Processed`, `Archived`, `Ignored`, `Failed`

Not all `Imported` items need to reach `Expanded`. Queue selectively based on processing priority.

### Status Transition Ownership

| Transition | Assigned by |
|---|---|
| → `Imported` | Automatically by ingestion pipeline on successful import |
| → `Queued` | Manually by user, or via batch-selection workflow |
| → `Expanded` | Automatically after successful deep extraction |
| → `Reviewed` | Manually by user after reviewing extracted content |
| → `Routed` | Automatically or manually after downstream routing completes |
| → `Legacy Processed` | Manually during migration of previously processed saves |
| → `Archived` / `Ignored` | Manually by user |
| → `Failed` | Automatically when any pipeline stage fails |

---

## Human Review

Review happens inside Notion. No external review tooling is required initially.

Users review items with status `Expanded`. During review, users:

- Validate extraction quality (summary, insights, tags, routing suggestions)
- Edit any fields as needed
- Then either:
  - Set status to `Reviewed`
  - Re-queue for reprocessing (`Queued`)
  - Set to `Archived` or `Ignored`

This step exists to catch extraction errors before items are routed downstream.

---

## Failure Handling

- Failures must be non-destructive. Partial data is preserved whenever possible.
- Failed items remain in Notion with status `Failed` and errors written to `failure_notes`.
- No automatic retries. Failed items are retried explicitly after the underlying issue is resolved.
- A row with only URL + author + collection is still a valid, useful ingestion result.

---

## Pipeline Stages

### Stage 1 — Ingestion (MVP)

Crawl Instagram saved collections and write one Notion row per save.

Fields populated:

| Field | Source |
|---|---|
| `source_id` | Derived from IG shortcode |
| `ig_link` | Canonical post URL |
| `author` | IG username (no @) |
| `caption` | Raw caption text |
| `type` | Reel / Post / Carousel / Unknown |
| `collection` | All collections this save appears in |
| `posted_date` | From IG metadata if available |
| `imported_at` | Notion Created Time (auto, never overwrite) |
| `pipeline_status` | Set to `Imported` |

Missing metadata is stored as null — never as a placeholder string.

#### Stage 1 Success Criteria

- Can crawl a selected Instagram collection and enumerate its saved items
- Can extract metadata (URL, author, caption, type, collection, date) for each item
- Can create Notion entries with correct field mapping
- Can be re-run without creating duplicate entries
- Can recover from interruption and continue without reprocessing already-imported items

### Stage 2 — Deep Extraction

Runs on items with status `Queued`. Triggered manually or in batches.

Extraction targets:

- **Transcript**: Spoken audio content from reels/video
- **OCR text**: Visible text from frames, overlays, slides, and graphics
- **Carousel text**: Slide-by-slide structured text (separate from OCR when structure matters)

All raw outputs are stored in `raw_extraction` as JSON before any summarization. This preserves source fidelity for reprocessing with improved prompts or models.

`transcript_available` is set to true only if transcript extraction produced usable output.

Fields populated:

- `transcript`, `ocr_text` *(source acquisition outputs — captured from original content)*
- `expanded_summary`, `key_insights` *(AI-generated synthesis outputs — not present in the original source)*
- `extracted_externals` (tools, links, products, people, locations)
- `raw_extraction` (full JSON payload)
- `last_processed_at`, `processing_version`
- `pipeline_status` → `Expanded` on success, `Failed` on failure

**`processing_version`** format: `v{major}.{minor}-{descriptor}` e.g. `v1.0-base`, `v1.2-carousel`. Increment minor on prompt changes, major on schema changes. Updated on every extraction run.

### Stage 3 — Enrichment *(future)*

Runs on `Expanded` items.

- AI-generated semantic `tags` (topic/theme focused, not collection names)
- `suggested_next_step` (concise, operational)
- `duplicate_confidence` assessment against existing items
- Manual `similar_info` linking via Notion relation

Note: cross-item operations (duplicate clustering, related content linking) are graph-level operations. They run as separate batch jobs, not inline per-item steps.

Seeding tags: on first enrichment run, use existing collection names as initial tag candidates before AI refinement.

Status → `Reviewed` (after human review)

### Stage 4 — Distribution *(future)*

Route `Reviewed` items to downstream databases based on `route_target`.

Examples: Recipe DB, Brand Swipe File, Engineering KB, Startup Research, Travel Planner

Status → `Routed`

---

## Open Architectural Decision — Save-to-Downstream Relationship

The relationship between an Instagram save and a downstream knowledge object is currently undefined.

For example:

```
Instagram Save → Recipe DB entry
Instagram Save → Business Idea entry
Instagram Save → Swipe File entry
```

A single save may produce one or more downstream objects. Those objects may have substantially different schemas from the source save.

This needs to be resolved before Stage 4 is implemented. Options include: direct field mapping, a separate transformation layer, or derivative Notion databases linked by relation. This decision is deferred to Stage 4 planning.

---

## Notion Database Schema

### MVP — Stage 1

| Property | Type | Notes |
|---|---|---|
| `title` | Title | AI-generated descriptive title. Not the caption. |
| `source_id` | Text | IG shortcode. Primary deduplication key. |
| `ig_link` | URL | Canonical post URL. |
| `author` | Text | IG handle without @. |
| `type` | Select | Reel, Post, Carousel, IGTV, Story Capture, Unknown |
| `collection` | Multi-select | All collections this save belongs to. |
| `caption` | Long text | Raw caption, verbatim. |
| `posted_date` | Date | From IG metadata. Nullable. |
| `imported_at` | Created time | Auto. Never overwrite. |
| `pipeline_status` | Select | Imported, Queued, Expanded, Reviewed, Routed, Legacy Processed, Archived, Ignored, Failed |
| `processing_priority` | Select | High, Medium, Low, Reference Only |
| `failure_notes` | Long text | Pipeline errors, broken URLs, extraction failures. |

### Add in Stage 2

| Property | Type | Notes |
|---|---|---|
| `transcript_available` | Checkbox | True only if extraction produced usable output. |
| `transcript` | Long text | Raw spoken content. |
| `ocr_text` | Long text | Visually detected text from frames/slides. |
| `expanded_summary` | Long text | Full content summary. Sufficient to avoid rewatching. |
| `key_insights` | Long text | Distilled transferable knowledge. |
| `extracted_externals` | Long text | Tools, links, products, creators, locations. Structured text. |
| `raw_extraction` | Long text | Full JSON extraction payload. Preserve for reprocessing. Never overwrite. May eventually evolve into versioned extraction records if multiple reprocessing generations need to be preserved. |
| `last_processed_at` | Date | Updated on every pipeline run. |
| `processing_version` | Text | e.g. `v1.2-carousel`. |

### Add in Stage 3

| Property | Type | Notes |
|---|---|---|
| `tags` | Multi-select | AI semantic tags. Topic/theme focused, not collection names. |
| `route_target` | Multi-select | Target downstream systems. |
| `suggested_next_step` | Text | Short, operational AI-generated recommendation. |
| `detected_entities` | Text | Named entities: brands, tools, apps, creators, locations, etc. |
| `review_notes` | Long text | Manual human commentary, corrections, implementation notes. |
| `similar_info` | Relation (self) | Links to related or duplicate saves. |
| `duplicate_confidence` | Select | Low, Medium, High. |
| `source_assets` | Text / URL | Reference to any archived media, screenshots, or transcripts at an external location. |

`detected_entities` is stored as structured text initially. Promote to a dedicated field type if entity-based querying becomes a priority.

`source_assets` replaces the earlier `media_assets_saved` / `media_archive_link` pair. A single field pointing to any archive location is sufficient until a storage backend is decided.

---

## Operational Notes

**Session management is the highest operational risk.** Browser sessions are fragile. Build and validate session health checks and re-auth recovery before building anything that depends on an active session.

**Notion as canonical layer.** Treat the Notion database as the source of truth for pipeline state. Export a full JSON backup periodically. Do not rely on Notion indefinitely as the only copy of extracted data.

**Reprocessing is a first-class operation.** The `processing_version` + `raw_extraction` fields exist to support running improved prompts over existing data without re-scraping Instagram. Never overwrite `raw_extraction`.

**Start with one collection.** Do not bulk-import all 42 collections before validating the pipeline end-to-end on a small batch. Pick a small, well-understood collection first.

**Future consideration — collection history.** The `collection` field stores current collection membership at time of import. If collections are later renamed, merged, or reorganized, that historical context will be lost. This is not a current concern but may become relevant if collection provenance matters for downstream routing or auditing.
