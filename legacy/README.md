# Insta Save Archive

Crawls Instagram saved collections, writes post metadata to Notion, extracts transcripts and OCR text, and enriches each item with AI-generated titles, external references, and summaries. Runs entirely locally — no servers, no daemons, no cloud spend.

## Pipeline overview

```
Instagram saved collections
        │
        ▼
Phase 1 — Ingestion          legacy/scripts/ingest_batch.py / legacy/scripts/ingest.py
  Crawls each collection; writes author, type, caption, URL to Notion.
  Status: Imported
        │
        ▼ (manual: mark items Queued + set priority in Notion)
        ▼
Phase 2 — Extraction         legacy/scripts/extract.py
  Extracts transcripts (Reels/IGTV) and OCR text (Carousels/Posts) for Queued items.
  Status: Queued → Extracted
        │
        ▼ (run once all Queued items are extracted)
        ▼
Phase 3 — Title pass         legacy/scripts/title.py   [automated, Ollama, no status change]
  Generates a human-readable title from caption for Extracted and Imported items.
  Status: unchanged
        │
        ▼
Phase 3 — Summarize pass     legacy/scripts/summarize.py   [manual Claude Code session]
  Claude generates summary and externals for each Extracted item.
  Status: Extracted → Summarized
```

### Pipeline status values

| Status | Set by | Meaning |
|---|---|---|
| `Imported` | ingest scripts | Metadata written, awaiting extraction |
| `Queued` | `promote.py` | Marked for deep extraction |
| `Extracted` | `extract.py` | Transcript + OCR extracted |
| `Summarized` | `summarize.py --upload` | Summary + externals written by Claude |
| `Failed` | any stage | Stage failed; see `failure_notes` |

---

## Requirements

- Python 3.12+
- [Playwright](https://playwright.dev/python/) with Chromium
- [Ollama](https://ollama.com) (system-wide WSL install) with `qwen2.5:7b`
- A Notion integration with access to a database (see setup)
- Instagram account with saved collections
- ffmpeg: `sudo apt install ffmpeg`
- WSL2 Ubuntu (Windows host)

---

## Setup

### 1. Install system dependencies

```bash
sudo apt install ffmpeg zstd
curl -fsSL https://ollama.com/install.sh | sh   # installs Ollama as a systemd service
ollama pull qwen2.5:7b
```

### 2. Install Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .              # editable install so scripts can import from pipeline/
playwright install chromium
```

### 3. Configure environment

Copy `.env.example` to `.env` and fill in all values:

```bash
cp .env.example .env
```

| Variable | Description | Default |
|---|---|---|
| `IG_USERNAME` | Instagram username (no `@`) | required |
| `TARGET_COLLECTION` | Collection name for single-collection runs | required |
| `NOTION_TOKEN` | Notion integration secret (`secret_...`) | required |
| `NOTION_DATABASE_ID` | 32-char hex ID from database URL | required |
| `NOTION_WRITE_DELAY` | Seconds between Notion writes | `0.4` |
| `PROCESSING_VERSION` | Version key for `raw_extraction` | `v1.0-base` |
| `WHISPER_MODEL` | faster-whisper model: `base` or `small` | `base` |
| `TMP_DIR` | Temp directory for media files | `tmp` |
| `OLLAMA_MODEL` | Ollama model for title pass | `qwen2.5:7b` |
| `OLLAMA_BASE_URL` | Ollama API endpoint | `http://localhost:11434` |
| `ENRICHMENT_VERSION` | Version tag written to summarized items | `v1.0-enrich` |
| `EXTRACT_DELAY_MIN` | Minimum seconds between extracted items (rate limit guard) | `3.0` |
| `EXTRACT_DELAY_MAX` | Maximum seconds between extracted items | `7.0` |

### 4. Set up Notion

1. Create a new full-page database in Notion.
2. Go to **Settings → Integrations → Develop your own integrations** → create an integration → copy the secret.
3. Open your database → **...** → **Connect to** → select your integration.
4. Copy the database ID from the URL: `notion.so/workspace/<DATABASE_ID>?v=...`

The database needs these properties. Add them in the order the phases are introduced — the pipeline validates them at write time, not startup.

**Phase 1 — add before ingestion:**

| Property | Type |
|---|---|
| `title` | Title |
| `source_id` | Text |
| `ig_link` | URL |
| `author` | Text |
| `type` | Select |
| `caption` | Text |
| `posted_date` | Date |
| `collection` | Multi-select |
| `status` | Select |
| `failure_notes` | Text |

**Phase 2 — add before running extraction:**

| Property | Type |
|---|---|
| `priority` | Select (options: High, Medium, Low) |
| `transcript` | Text |
| `ocr_text` | Text |
| `raw_extraction` | Text |
| `last_processed_at` | Date |
| `processing_version` | Text |

**Phase 3 — add before running enrichment:**

| Property | Type |
|---|---|
| `externals` | Text |
| `summary` | Text |

You can add Phase 3 properties programmatically instead of manually:

```python
from pipeline.config import load_config
from notion_client import Client
config = load_config()
client = Client(auth=config.notion_token)
db = client.databases.retrieve(database_id=config.notion_database_id)
ds_id = db["data_sources"][0]["id"]
client.data_sources.update(ds_id, properties={
    "externals": {"rich_text": {}},
    "summary":   {"rich_text": {}},
})
```

### 5. Bootstrap collections

Your collection list lives in `config/collections.json` (gitignored). Generate it from your Instagram account:

```bash
python legacy/scripts/list_collections.py --update
```

Then edit `config/collections.json` to set `group` and `extract` for each collection. See `config/collections.example.json` for the format.

### 6. Authenticate Instagram

On first run, a browser window opens at the Instagram login page. Log in manually (username + password + 2FA if enabled). The session is saved to `session_cookies.json` and reused — you should only need to do this once.

**WSL display note:** If the browser window doesn't appear or isn't interactable, use `--headed` and ensure VcXsrv is running:

```bash
# Windows PowerShell — start VcXsrv
Start-Process "C:\Program Files\VcXsrv\vcxsrv.exe" -ArgumentList ":1 -multiwindow -ac -noclipboard"
# Then run with:
python legacy/scripts/ingest.py --headed
```

---

## Running

> **v1 is archived under `legacy/`.** Run all commands from the **repo root** so relative `config/` and `tmp/` paths resolve. `pip install -e .` keeps the `pipeline` package importable from its new location (`pyproject.toml` scans both `.` and `legacy`), so `from pipeline.config import ...` and the `python legacy/scripts/*.py` entrypoints work unchanged.

Always activate the venv first:

```bash
source .venv/bin/activate
```

---

### Phase 1 — Ingest (collection sync)

Ingest is a **fail-safe sync**, not a one-way import. Each run:

1. **Discovers** collections from the `/saved/` index (additive — new collections are added to `collections.json`; missing ones are flagged, never deleted).
2. **Crawls** each collection, recording whether the crawl was *complete* (reached the bottom and stopped finding new posts).
3. **Loads** all Notion pages once (bulk dedup — no per-post queries).
4. **Reconciles** crawled membership against Notion: creates new posts, and updates collection tags for posts that moved.

**Safety principle — presence is reliable, absence is not:**
- A post is **tagged** with a collection whenever it's found there (always safe).
- A tag is **removed** only when that collection's crawl *completed* — so a transient Instagram render glitch can never strip valid tags. Removing a whole collection's tags requires explicit `--confirm-removed`.

The terminal shows live progress bars; full detail goes to `logs/ingest_<timestamp>.log`.

#### Full sync (all collections)

```bash
python legacy/scripts/ingest_batch.py                 # discover + crawl + reconcile + apply
python legacy/scripts/ingest_batch.py --dry-run        # compute the plan, write nothing
python legacy/scripts/ingest_batch.py --discover-only  # just refresh collections.json
python legacy/scripts/ingest_batch.py --headed         # visible browser (first login)
```

Tuning and recovery:
```bash
# Reuse complete snapshots younger than N minutes (default 360) — fast crash-resume
python legacy/scripts/ingest_batch.py --max-snapshot-age 60

# Ignore snapshots, re-crawl everything fresh
python legacy/scripts/ingest_batch.py --fresh

# Allow stripping a collection you deleted on Instagram (repeatable)
python legacy/scripts/ingest_batch.py --confirm-removed "Old Collection"
```

Snapshots live in `tmp/ingest/snapshots/` (gitignored). A crash loses at most the in-flight crawl; re-running reuses fresh snapshots and converges (all writes are idempotent).

#### Single collection

```bash
# Set TARGET_COLLECTION in .env, then:
python legacy/scripts/ingest.py
python legacy/scripts/ingest.py --dry-run
python legacy/scripts/ingest.py --headed
```

Single-collection mode skips discovery and reconciles only that collection. Tags for a post's *other* collections are left untouched — these show as "unsafe removals skipped" in the summary, which is expected.

#### Dry-run summary

```
collections=43 · creates=14 · retags=1 · unchanged=236 · skipped_unsafe=0
```
- **creates** — new posts to add · **retags** — posts whose collection tags change
- **unchanged** — already correct · **skipped_unsafe** — removals withheld (incomplete crawl)

---

### Phase 2 — Extraction

#### Step 1 — Queue items

Set items to `Queued` status in Notion (and optionally set `priority`: High / Medium / Low). Then:

```bash
# Queue all pilot collections (extract=True in collections.json)
python legacy/scripts/promote.py --all-pilot

# Queue a single collection
python legacy/scripts/promote.py --collection "<YOUR_COLLECTION>"

# Preview without writing
python legacy/scripts/promote.py --all-pilot --dry-run
```

Sets `status` from `Imported` → `Queued` for matched items.

#### Step 2 — Run extraction

```bash
# All Queued items (priority order: High → Medium → Low → unprioritised)
python legacy/scripts/extract.py

# Limit to N items
python legacy/scripts/extract.py --limit 10

# Single item by shortcode
python legacy/scripts/extract.py --source_id <SHORTCODE>

# Headed browser (needed if Playwright can't find a display)
python legacy/scripts/extract.py --headed
```

Sets `status` from `Queued` → `Extracted` (or `Failed`). Items with no extractable content (no transcript and no OCR) stay `Queued` — they won't silently become `Extracted` with empty data.

Inter-item delay (default 3–7s) is applied between each extraction to avoid HTTP 429 rate limiting. Adjust via `EXTRACT_DELAY_MIN` / `EXTRACT_DELAY_MAX` in `.env`.

**Extraction by type:**
- **Reel / IGTV** — transcript via yt-dlp + faster-whisper; OCR frames via ffmpeg + RapidOCR
- **Carousel** — slide download + OCR per slide via Playwright
- **Post** — single-image download + OCR via Playwright

---

### Phase 3 — Title pass (Ollama, automated)

Generates a human-readable `title` from caption for items that still have a placeholder title. Runs on **Extracted** and **Imported** items — run after extraction, once no Queued items remain. Does **not** change status.

Safe to run anytime, repeatedly — only items with a placeholder title are processed.

**Check Ollama is running:**
```bash
systemctl status ollama    # should show active (running)
# or start manually:
ollama serve &
```

```bash
# Full run — all Queued + Extracted items (Imported items at lower priority)
python legacy/scripts/title.py

# Limit to N items
python legacy/scripts/title.py --limit 10

# Single item by shortcode
python legacy/scripts/title.py --source_id <SHORTCODE>

# Force overwrite (re-runs even if title already exists)
python legacy/scripts/title.py --force
```

**VRAM note:** qwen2.5:7b requires ~4GB VRAM. If you hit OOM:
```bash
ollama rm qwen2.5:7b
ollama pull qwen2.5:3b
# set OLLAMA_MODEL=qwen2.5:3b in .env
```

---

### Phase 3 — Summarize pass (Claude Code session)

Generates `summary` and `externals` for `Extracted` items using a Claude Code session. Highest priority first (High → Medium → Low → unprioritised). Sets `status` → `Summarized`.

Workflow — repeat until no `Extracted` items remain:

#### Step 1 — Prepare a batch

```bash
python legacy/scripts/summarize.py --prepare
```

Fetches the highest-priority non-empty `Extracted` bucket up to a content budget (~200k chars). Writes:
- `tmp/enrichment_batch.json` — raw item data
- `tmp/enrichment_prompt.txt` — Claude-ready prompt

#### Step 2 — Run Claude

In a Claude Code session, say:
> *"Read tmp/enrichment_prompt.txt and write the results JSON to tmp/enrichment_results.json"*

Claude reads all items in one turn and writes a JSON array:
```json
[
  {
    "page_id": "...",
    "source_id": "...",
    "summary": "...",
    "externals": "..."
  }
]
```

#### Step 3 — Upload results

```bash
python legacy/scripts/summarize.py --upload
```

Writes `summary` and `externals` to Notion for each item. Sets `status` → `Summarized`. Cleans up tmp files on full success.

Repeat steps 1–3 until `--prepare` reports no `Extracted` items remain.

**externals format** (grouped by category):
```
[Tools]
  Figma — UI design tool used for wireframing
[Creators]
  @username — person referenced in post
[Links]
  https://example.com — resource mentioned in caption
```

---

## Common failures

**`session_cookies.json` missing or expired`**
Browser opens for manual login. Complete it and the session saves automatically.

**`crawler: collection 'X' not found`**
Collection name must match exactly. Check `instagram.com/<username>/saved/` or run `python legacy/scripts/list_collections.py`.

**`Could not find property with name or id: <prop>`**
A Notion property name doesn't match the pipeline's expectation. Verify names match the tables in the setup section — no trailing spaces, correct capitalisation.

**`NOTION_TOKEN` or `NOTION_DATABASE_ID` not set`**
Add them to `.env` and re-run.

**`Instagram redirects to login mid-run`**
Session expired. Delete `session_cookies.json` and re-run to trigger re-auth.

**`Ollama not reachable at http://localhost:11434`**
Ollama isn't running. Start it: `ollama serve` or `sudo systemctl start ollama`.

**`HTTP 429` during extraction**
Instagram rate-limited the session. Increase `EXTRACT_DELAY_MIN` and `EXTRACT_DELAY_MAX` in `.env`, then re-run (already-extracted items are skipped).

**`Collections file not found`**
`config/collections.json` doesn't exist. Run `python legacy/scripts/list_collections.py --update` to generate it.
