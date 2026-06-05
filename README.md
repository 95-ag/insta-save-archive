# Insta Save Archive

Crawls Instagram saved collections, writes post metadata to Notion, extracts transcripts and OCR text, and enriches each item with AI-generated titles, external references, summaries, and insights. Runs entirely locally — no servers, no daemons, no cloud spend.

## Pipeline overview

```
Instagram saved collections
        │
        ▼
Phase 1 — Ingestion          scripts/ingest_batch.py / scripts/ingest.py
  Crawls each collection; writes author, type, caption, URL to Notion.
  Status: Imported
        │
        ▼
Phase 2 — Extraction         scripts/queue_pilot.py → scripts/run_extraction.py
  Queues pilot collections; extracts transcripts (Reels) and OCR (Carousels).
  Status: Queued → Expanded
        │
        ▼
Phase 3a — Local enrichment  scripts/run_enrichment_local.py   [automated, unattended]
  Ollama (qwen2.5:7b) generates title and extracted_externals for all Expanded items.
  Status: Expanded → Enriched
        │
        ▼
Phase 3b — Claude enrichment scripts/run_enrichment_claude_code.py   [manual, priority collections]
  Claude Code session generates expanded_summary and key_insights, one collection per turn.
  Status: Enriched → Summarised
```

### Pipeline status values

| Status | Set by | Meaning |
|---|---|---|
| `Imported` | ingest scripts | Metadata written, awaiting extraction |
| `Queued` | `queue_pilot.py` | Marked for deep extraction |
| `Expanded` | `run_extraction.py` | Transcript + OCR extracted |
| `Failed` | any stage | Stage failed; see `failure_notes` |
| `Enriched` | `run_enrichment_local.py` | Title + externals written by Ollama |
| `Summarised` | `run_enrichment_claude_code.py` | Summary + insights written by Claude |

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
| `OLLAMA_MODEL` | Ollama model for local enrichment | `qwen2.5:7b` |
| `OLLAMA_BASE_URL` | Ollama API endpoint | `http://localhost:11434` |
| `ENRICHMENT_VERSION` | Version tag written to enriched items | `v1.0-enrich` |

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
| `pipeline_status` | Select |
| `failure_notes` | Text |

**Phase 2 — add before running extraction:**

| Property | Type |
|---|---|
| `transcript_available` | Checkbox |
| `transcript` | Text |
| `ocr_text` | Text |
| `raw_extraction` | Text |
| `last_processed_at` | Date |
| `processing_version` | Text |

**Phase 3 — add before running enrichment (or use the setup snippet):**

| Property | Type |
|---|---|
| `extracted_externals` | Text |
| `expanded_summary` | Text |
| `key_insights` | Text |

You can add Phase 3 properties programmatically instead of manually:

```python
from pipeline.config import load_config
from notion_client import Client
config = load_config()
client = Client(auth=config.notion_token)
db = client.databases.retrieve(database_id=config.notion_database_id)
ds_id = db["data_sources"][0]["id"]
client.data_sources.update(ds_id, properties={
    "extracted_externals": {"rich_text": {}},
    "expanded_summary":    {"rich_text": {}},
    "key_insights":        {"rich_text": {}},
})
```

### 5. Bootstrap collections

Your collection list lives in `config/collections.json` (gitignored). Generate it from your Instagram account:

```bash
python scripts/list_collections.py --update
```

Then edit `config/collections.json` to set `group` and `extract` for each collection. See `config/collections.example.json` for the format.

### 6. Authenticate Instagram

On first run, a browser window opens at the Instagram login page. Log in manually (username + password + 2FA if enabled). The session is saved to `session_cookies.json` and reused — you should only need to do this once.

**WSL display note:** If the browser window doesn't appear or isn't interactable, use `--headed` and ensure VcXsrv is running:

```bash
# Windows PowerShell — start VcXsrv
Start-Process "C:\Program Files\VcXsrv\vcxsrv.exe" -ArgumentList ":1 -multiwindow -ac -noclipboard"
# Then run with:
python scripts/ingest.py --headed
```

---

## Running

Always activate the venv first:

```bash
source .venv/bin/activate
```

---

### Phase 1 — Ingest

#### All collections (batch)

```bash
python scripts/ingest_batch.py
```

Processes all collections in group priority order. Re-runnable — skips already-ingested posts.

```bash
# Start from a specific group (skips earlier groups)
python scripts/ingest_batch.py --start-from-group "<GROUP>"

# Preview order without crawling
python scripts/ingest_batch.py --dry-run

# Headed browser (needed for first login)
python scripts/ingest_batch.py --headed
```

#### Single collection

```bash
# Set TARGET_COLLECTION in .env, then:
python scripts/ingest.py

# Headed browser
python scripts/ingest.py --headed
```

Typical output:
```
13:42:06 INFO session: status=valid
13:42:25 INFO crawler: found 36 posts in '<YOUR_COLLECTION>'
13:46:54 INFO ingest: done — created=36 skipped=0 failed=0
```

Re-runs skip existing posts: `created=0 skipped=36 failed=0`

---

### Phase 2 — Extraction

#### Step 1 — Queue pilot collections

```bash
# Queue all pilot collections (extract=True in collections.json)
python scripts/queue_pilot.py --all-pilot

# Queue a single collection
python scripts/queue_pilot.py --collection "<YOUR_COLLECTION>"

# Preview without writing
python scripts/queue_pilot.py --all-pilot --dry-run
```

Sets `pipeline_status` from `Imported` → `Queued` for matched items.

#### Step 2 — Run extraction

```bash
# All Queued items
python scripts/run_extraction.py

# Limit to N items
python scripts/run_extraction.py --limit 10

# Single item by shortcode
python scripts/run_extraction.py --source_id <SHORTCODE>

# Headed browser
python scripts/run_extraction.py --headed
```

Sets `pipeline_status` from `Queued` → `Expanded` (or `Failed`). Writes `transcript`, `ocr_text`, and versioned `raw_extraction`. Re-runnable — skips already `Expanded` items unless `--force`.

---

### Phase 3a — Local enrichment (Ollama, automated)

Generates `title` and `extracted_externals` for all `Expanded` items. Runs unattended. Sets `pipeline_status` → `Enriched`.

**Check Ollama is running:**
```bash
systemctl status ollama    # should show active (running)
# or start manually:
ollama serve &
```

```bash
# Full run — all Expanded items (run overnight or when idle)
python scripts/run_enrichment_local.py 2>&1 | tee /tmp/local_enrichment.log

# Dry-run — preview output, no Notion writes
python scripts/run_enrichment_local.py --dry-run --limit 5

# Limit to N items
python scripts/run_enrichment_local.py --limit 10

# Single item by shortcode
python scripts/run_enrichment_local.py --source_id <SHORTCODE>

# Force overwrite (re-runs even if title is not a placeholder)
python scripts/run_enrichment_local.py --force --source_id <SHORTCODE>
```

Interrupt-safe — if it stops mid-run, re-run from the beginning. Items with a real title (non-placeholder) are skipped automatically.

**extracted_externals format** (one entry per line):
```
[tool] Figma — UI design tool used
[brand] Acme Co — subject of the post
[creator] @username — person referenced
[website] example.com — resource mentioned
```
Valid types: `tool`, `app`, `brand`, `creator`, `website`, `link`, `location`, `technique`

**VRAM note:** qwen2.5:7b requires ~4GB VRAM. If you hit OOM:
```bash
ollama rm qwen2.5:7b
ollama pull qwen2.5:3b
# set OLLAMA_MODEL=qwen2.5:3b in .env
```

---

### Phase 3b — Claude enrichment (Claude Code session)

Generates `expanded_summary` and `key_insights` for priority collections. Runs as a Claude Code session — one collection per turn. Sets `pipeline_status` → `Summarised`.

**Run local enrichment first** — Claude pass reads `Enriched` items (post-local pass).

#### Step 1 — Check priority order

```bash
python scripts/run_enrichment_claude_code.py --list-priority
```

#### Step 2 — Prepare a batch

```bash
python scripts/run_enrichment_claude_code.py --prepare --collection "<YOUR_COLLECTION>"
```

Writes `tmp/enrichment_batch.json` (raw data) and `tmp/enrichment_prompt.txt` (Claude-ready prompt).

#### Step 3 — Run Claude

In this Claude Code session, say:
> *"Read tmp/enrichment_prompt.txt and write the results JSON to tmp/enrichment_results.json"*

Claude reads all items for the collection in one turn and writes a JSON array:
```json
[
  {
    "page_id": "...",
    "source_id": "...",
    "expanded_summary": "...",
    "key_insights": ["...", "..."]
  }
]
```

#### Step 4 — Upload results

```bash
python scripts/run_enrichment_claude_code.py --upload
```

Writes `expanded_summary` and `key_insights` to Notion for each item. Cleans up tmp files on success.

Repeat steps 2–4 for each priority collection.

---

## Common failures

**`session_cookies.json` missing or expired`**
Browser opens for manual login. Complete it and the session saves automatically.

**`crawler: collection 'X' not found`**
Collection name must match exactly. Check `instagram.com/<username>/saved/` or run `python scripts/list_collections.py`.

**`Could not find property with name or id: <prop>`**
A Notion property name doesn't match the pipeline's expectation. Verify names match the tables in the setup section — no trailing spaces, correct capitalisation.

**`NOTION_TOKEN` or `NOTION_DATABASE_ID` not set`**
Add them to `.env` and re-run.

**`Instagram redirects to login mid-run`**
Session expired. Delete `session_cookies.json` and re-run to trigger re-auth.

**`Ollama not reachable at http://localhost:11434`**
Ollama isn't running. Start it: `ollama serve` or `sudo systemctl start ollama`.

**`enrichment_local: model did not call tool`**
Ollama returned a text response instead of a tool call. Usually a one-off — re-run the item with `--source_id`. If persistent, the model may be overloaded or the prompt is too long; try `qwen2.5:3b`.

**`extracted_externals is not a property that exists`**
Phase 3 Notion properties haven't been created yet. Run the setup snippet from the Notion setup section above.

**`Collections file not found`**
`config/collections.json` doesn't exist. Run `python scripts/list_collections.py --update` to generate it.
