# Insta Save Archive

Crawls an Instagram saved collection, writes post metadata to Notion, and runs deep extraction on each item — transcript, OCR text, and carousel slide text. Runs locally — no servers, no daemons.

## What it does

**Phase 1 — Ingestion** (`ingest.py`): for each post in a named saved collection:
- Extracts: author, post type (Post / Reel / Carousel / IGTV), caption, posted date, source URL
- Deduplicates against Notion before writing — safe to re-run and interrupt

**Phase 2 — Deep extraction** (`run_extraction.py`): for each item manually set to `Queued` in Notion:
- Reels: spoken transcript via faster-whisper + on-screen text from sampled video frames
- Carousels: per-slide OCR text in slide order
- Stores full outputs in Notion properties; preserves all prior extractions under versioned keys in `raw_extraction`

## Requirements

- Python 3.12+
- [Playwright](https://playwright.dev/python/) with Chromium
- A Notion integration with access to a database (see setup below)
- Instagram account with saved collections
- Windows with VcXsrv (WSL display — see note below)

## Setup

### 1. Install system dependencies

```bash
sudo apt install ffmpeg
```

### 2. Install Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 3. Configure environment

Copy `.env.example` to `.env` and fill in all values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `IG_USERNAME` | Your Instagram username (no `@`) |
| `TARGET_COLLECTION` | Exact name of the collection to crawl (e.g. `Job Hunt`). Use `all-posts` only after all named collections are done — it contains every saved post. |
| `NOTION_TOKEN` | Notion integration secret (`secret_...`) |
| `NOTION_DATABASE_ID` | 32-character hex ID from the Notion database URL |
| `BATCH_SIZE` | Posts per batch (default `50`, unused in Phase 1) |
| `NOTION_WRITE_DELAY` | Seconds between Notion writes (default `0.4`) |
| `PROCESSING_VERSION` | Version key written to `raw_extraction` (default `v1.0-base`) |
| `WHISPER_MODEL` | faster-whisper model size: `base` or `small` (default `base`) |
| `TMP_DIR` | Directory for temporary media files (default `tmp`) |

### 4. Set up Notion

1. Create a new full-page database in Notion.
2. Go to **Settings → Integrations → Develop your own integrations** and create an integration. Copy the secret.
3. Open your database, click **...** → **Connect to** → select your integration.
4. Copy the database ID from the URL: `notion.so/your-workspace/<DATABASE_ID>?v=...`

The database must have these properties with these exact names and types:

**Phase 1 (ingestion):**

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

**Phase 2 (extraction) — add these before running `run_extraction.py`:**

| Property | Type |
|---|---|
| `transcript_available` | Checkbox |
| `transcript` | Text |
| `ocr_text` | Text |
| `raw_extraction` | Text |
| `last_processed_at` | Date |
| `processing_version` | Text |

### 5. Authenticate Instagram

On first run, a browser window opens at the Instagram login page. Log in manually (username/password + 2FA if enabled). The session is saved to `session_cookies.json` and reused on subsequent runs — you should only need to do this once.

**WSL display note:** WSLg window interaction is broken on some machines. If you're running under WSL and the browser window doesn't appear or isn't interactable:

1. Install [VcXsrv](https://sourceforge.net/projects/vcxsrv/) on Windows.
2. Launch it: `vcxsrv.exe :1 -multiwindow -ac -noclipboard`
3. Run the pipeline with: `DISPLAY=172.22.48.1:1.0 python ingest.py`

If VcXsrv is blocked, check Windows Firewall for Block rules on the Public profile for "VcXsrv windows xserver" and disable them.

## Running

```bash
source .venv/bin/activate
```

### Phase 1 — Ingest a collection

```bash
# Standard run
python ingest.py

# WSL with VcXsrv
DISPLAY=172.22.48.1:1.0 python ingest.py
```

Output is logged to stdout. A typical run looks like:

```
13:42:02 INFO ingest: starting — collection='Job Hunt'
13:42:06 INFO session: status=valid
13:42:25 INFO crawler: found 36 posts in 'Job Hunt'
13:42:31 INFO ingest: created C--xP58PhNv → <page-id>
...
13:46:54 INFO ingest: done — created=36 skipped=0 failed=0
```

Re-running on the same collection skips already-ingested posts:

```
13:46:54 INFO ingest: done — created=0 skipped=36 failed=0
```

### Phase 2 — Deep extraction

In Notion, set the `pipeline_status` of items you want to process to `Queued`, then run:

```bash
# Process all Queued items
DISPLAY=172.22.48.1:1.0 python run_extraction.py

# Process up to N items
DISPLAY=172.22.48.1:1.0 python run_extraction.py --limit 10

# Reprocess a single item by shortcode
DISPLAY=172.22.48.1:1.0 python run_extraction.py --source_id DYUjq6US1Dg
```

Each processed item transitions to `Expanded`. On failure it transitions to `Failed` with a `failure_notes` message. Re-running a previously `Expanded` item appends a new version key to `raw_extraction` without overwriting prior data — set `PROCESSING_VERSION` in `.env` to distinguish reprocessing runs.

## Common failures

**`session_cookies.json` missing or expired**
The browser window opens for manual login. Complete the login and the session is saved automatically.

**`crawler: collection 'X' not found`**
The collection name in `TARGET_COLLECTION` must match exactly (case-insensitive). Check the available collections printed in the error, or visit `instagram.com/<username>/saved/` to verify the name.

**`Could not find property with name or id: <prop>`**
A Notion database property name doesn't match what the pipeline expects. Verify all property names match the table in the setup section exactly — no trailing spaces, correct capitalisation.

**`NOTION_TOKEN` or `NOTION_DATABASE_ID` not set**
These are validated on startup. Set them in `.env` and re-run.

**Instagram redirects to login mid-run**
Session expired. Delete `session_cookies.json` and re-run to trigger re-auth.
