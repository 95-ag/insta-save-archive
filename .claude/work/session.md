# Session

## Current State

**Branch:** `feature-batch-ingest-phase3-enrich`
**Last commit:** `bbee814` — docs: update tasks and session for Phase 3 enrichment plan

Phase 2 COMPLETE. All 43 collections ingested. 155 pilot items Expanded (transcript + OCR done).
Split enrichment plan written. Next: execute split enrichment plan (Ollama setup + implementation).

---

## What Was Built This Session (committed on branch)

| Commit | Files | What |
|---|---|---|
| d5509ff | `collections_config.py`, `ingest.py`, `ingest_batch.py` | Collections registry (43 collections, groups, pilot flags); batch ingestion runner |
| 157e38a | `queue_pilot.py`, `notion.py` | Bulk queue promoter; `mark_queued`, `query_by_collection_and_status` |
| a96ed20 | `enrichment.py`, `run_enrichment.py`, `notion.py`, `config.py`, `requirements.txt`, `prompts/enrichment_v1.0-enrich.txt`, `list_collections.py` | Phase 3 enrichment infra (API-based, needs split per new plan); fixed collection scroll bug |
| bbee814 | `tasks.md`, `session.md` | Docs |

**Note:** `enrichment.py` and `run_enrichment.py` were built for Anthropic API (no key available). They will be modified by the split enrichment plan to narrow scope. Do not run them as-is.

---

## Operational Status

- **A3 Batch ingest:** COMPLETE — all 43 collections ingested, re-run = 0 creates
- **B2 Pilot extraction:** COMPLETE — `expanded=155 failed=0 skipped=0`
- **C6 Enrichment:** NOT YET — blocked on Anthropic API; resolved via split enrichment plan

---

## Enrichment Strategy (FINAL)

### Split by field

| Field | Engine | Scope |
|---|---|---|
| `title` | Ollama (local) | All 155 Expanded items |
| `extracted_externals` | Ollama (local) | All 155 Expanded items |
| `expanded_summary` | Claude Code (manual) | Hustling pilot only (6 collections) |
| `key_insights` | Claude Code (manual) | Hustling pilot only (6 collections) |

### Local pass (Ollama)
- Engine: qwen2.5:7b; fallback qwen2.5:3b if 4GB VRAM OOMs
- Ollama is system-wide (not project-scoped); models at `~/.ollama/models/`
- Pattern: per-item sequential — Notion READ → Ollama → Notion WRITE (interrupt-safe)
- Skip condition: title is not a placeholder (`{author} — {shortcode}` pattern)
- Script: `run_enrichment_local.py`

### Claude Code pass
- Scope: **Hustling only** — 6 collections, Branding & Logo last
- Order: Coding AI → Coding Web Design → Website Handling → Inspo Website → Job Hunt → Branding & Logo
- Pattern: batch read all items for collection → one Claude turn (entire collection in one prompt) → per-item upload
- Token efficiency: instructions once per batch, compact JSON array output, ~6 turns total
- Script: `run_enrichment_claude_code.py --prepare --collection X` → Claude writes results → `--upload`
- All other collections (Biz, Content, Lifestyle) deferred

### Future enrichment (deferred)
- Biz pilot (Hustle Ideas, Side Hustle Help, Inspo - BoI Biz) — Claude or local later
- Content + Lifestyle pilot — same
- Stage 3 (tags, duplicate_confidence, similar_info, source_assets) — all local
- `detected_entities` REMOVED (redundant with extracted_externals)
- `suggested_next_step` deferred until summary quality validated
- Stage 4 `route_target` — Claude (judgment needed)

---

## Next Plan to Execute

**Plan:** `/home/ag-95/.claude/plans/2026-06-05-split-enrichment-local-claude.md`

### Files the plan creates/modifies

**New:**
- `enrichment_local.py` — Ollama client, tool_use, returns `{title, extracted_externals}`
- `run_enrichment_local.py` — per-item local pass CLI
- `run_enrichment_claude_code.py` — `--prepare` (batch → prompt file) / `--upload` (results → Notion) / `--list-priority`

**Modified:**
- `enrichment.py` — narrow `_SAVE_ENRICHMENT_TOOL` to `expanded_summary + key_insights` only
- `notion.py` — add `write_local_enrichment(config, page_id, title, extracted_externals)`; remove title + extracted_externals from `write_enrichment`
- `run_enrichment.py` — add `--collection` flag, order by enrichment priority
- `config.py` — add `ollama_model: str`, `ollama_base_url: str` to Config + load_config
- `collections_config.py` — add `ENRICHMENT_HUSTLING_ORDER`, `pilot_collections_by_enrichment_priority()`
- `requirements.txt` — add `ollama>=0.3.0`

**Commits:**
1. `feat: add local Ollama enrichment pass for title and extracted_externals`
   → `enrichment_local.py`, `run_enrichment_local.py`, `notion.py`, `config.py`, `requirements.txt`
2. `feat: add Claude Code enrichment pass for summary and insights — Hustling only`
   → `collections_config.py`, `enrichment.py`, `run_enrichment_claude_code.py`, `run_enrichment.py`, `notion.py`
3. `docs: update tasks and session for split enrichment plan`

### Pre-execution Ollama setup
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &
ollama pull qwen2.5:7b
ollama run qwen2.5:7b "List tools: I use Figma, Notion, VS Code."  # test GPU inference
# nvidia-smi in second terminal to watch VRAM
# If OOM: ollama rm qwen2.5:7b && ollama pull qwen2.5:3b → set OLLAMA_MODEL=qwen2.5:3b in .env
```

### Operational order after plan executes
1. `python run_enrichment_local.py` (overnight, all 155 items)
2. Per collection: `--prepare --collection "Coding - AI"` → Claude reads prompt file → `--upload`
3. Repeat for all 6 Hustling collections

---

## Collection Registry (43 collections)

Group priority: Hustling → Content → Creative → Biz → Biz - Clothing → Lifestyle

```
Hustling (extract=True, Claude enrichment):
  Job Hunt | Coding - AI | Coding - Web Design | Website Handling | Branding & Logo | Inspo - Website

Content (partial extract):
  Digital Content Creation ✓ | Tips - Content Creation ✓ | Inspo - Quotes/Captions/Audio ✓
  Inspo - Reel/Post/Story Ideas | Inspo - Video Film/Editing | Photography/Filmography

Creative (no extract):
  Inspo - Art | Inspo - Digital Art | Inspo - Crafts | Arts & craft | Canva Hacks

Biz (partial extract):
  Hustle Ideas ✓ | Side Hustle Help ✓ | Inspo - BoI Biz ✓
  Patches Collab | Info for patch | Photo booth | 3D printing

Biz - Clothing (no extract):
  Clothing - Tutorials/Making | Clothing - Brands/Ideas | Clothing - lino prints
  Clothing - Accessories | Clothing - Suppliers

Lifestyle (partial extract):
  Foodie ✓ | Fitness ✓ | Quotes ✓
  Clothing hacks | BLR | Hair hacks | Makeup | Home ideas | Plants & Pets
  Interesting buys | Travel | Posing | boi saves | Tutorials
```

Slugs + numeric IDs → see `collections_config.py:COLLECTIONS`

---

## Locked Technical Decisions

| Concern | Decision |
|---|---|
| Transcript engine | yt-dlp + faster-whisper base int8 |
| OCR engine | RapidOCR (rapidocr-onnxruntime==1.4.4) |
| Local enrichment engine | Ollama + qwen2.5:7b (fallback: 3b) |
| Claude enrichment mechanism | Claude Code session (no API; Claude Max only) |
| Claude enrichment scope | Hustling only (current phase) |
| Notion write (local pass) | Per-item sequential (interrupt-safe) |
| Notion write (Claude pass) | Collection-batch read → one Claude turn → per-item upload |
| extracted_externals format | String, one per line: `[type] name — context` |
| detected_entities field | REMOVED — redundant with extracted_externals |
| article selector | Dead — do not use |
| Carousel img scope | `ul img` not `img` |
| CDN domain | instagram.fblr22-*.fna.fbcdn.net |
| Cookie format for yt-dlp | JSON → Netscape at runtime → tmp/cookies.txt |
| Venv binaries in subprocess | `Path(sys.executable).parent / "binary-name"` |

## Key Notion API Findings (permanent)

- notion-client 3.1.0, API 2025-09-03
- `databases.query` removed → `data_sources.query(data_source_id, ...)`
- `data_source_id` ≠ `database_id` → resolve via `databases.retrieve()`['data_sources'][0]['id']
- `_get_data_source_id` is called per-invocation (not cached) — acceptable at current scale

## Environment

- WSL2 Ubuntu, Windows host (Taiga), GPU: RTX 3050 Ti 4GB VRAM
- Branch: `feature-batch-ingest-phase3-enrich`
- Venv: `.venv/` → `source .venv/bin/activate`
- Playwright: headless default; `--headed` for visible browser; `DISPLAY=172.22.48.1:1.0`
- Ollama: system-wide, not project-scoped
- Sensitive: `session_cookies.json`, `.env` — gitignored, never stage
