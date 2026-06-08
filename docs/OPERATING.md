# Operating Guide

How to configure and run Insta-Save (v2). For the design rationale see
[`../.claude/docs/ARCHITECTURE.md`](../.claude/docs/ARCHITECTURE.md); for the v1 manual see
[`../legacy/README.md`](../legacy/README.md).

> **Status:** describes the v2 target interface (`isa` CLI). v2 is in build.

---

## 1. One-time setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium
sudo apt install ffmpeg              # transcript/OCR media handling
```

**Notion:** create a full-page database, create an integration
(Settings → Integrations), connect it to the database, copy the integration secret and
the 32-char database ID from the URL. Schema properties are created automatically on first
write (the pipeline ensures missing properties exist).

**`.env`** (secrets — never committed):

| Variable | Meaning |
|---|---|
| `NOTION_TOKEN` | Notion integration secret |
| `NOTION_DATABASE_ID` | 32-char database ID |
| `IG_USERNAME` | Instagram handle (no `@`) |
| `ANTHROPIC_API_KEY` | only for the `api` enrich backend |
| `OLLAMA_BASE_URL` | only for `local` (default `http://localhost:11434`) |

---

## 2. Configuration files

All live in `config/`. Private ones are gitignored.

### `run.json` — how a run behaves *(committed example: `run.example.json`)*

```json
{
  "mode": "first-time",
  "ordering": "collections.json",
  "enrich": { "backend": "cowork", "model": "claude-sonnet", "effort": "medium" },
  "extract": {
    "transcript": { "model": "small", "vad": true },
    "ocr": { "mode": "escalate", "escalate_threshold": 0.6 }
  },
  "batch": { "max_items": 15, "max_char_budget": 80000 },
  "guardrails": { "max_items_per_run": null, "max_spend_usd": null }
}
```

- **`enrich.backend`** — `local | api | claude-code | cowork`. **`model`/`effort`** — effort maps to thinking budget (`api`), model size (`local`), advisory (sessions).
- **`extract.transcript`** — `model`: `base | small | distil-large-v3`; `vad`: Silero voice-activity filter (recommended on).
- **`extract.ocr.mode`** — `none | rapidocr | local_vlm | claude_vlm | escalate`. `escalate` runs RapidOCR and only sends sub-`escalate_threshold` slides to Claude vision.
- **`batch`** — backend picks its own strategy; these are the caps for char/item-budget backends.
- **`guardrails`** — hard caps; a run stops before exceeding them. Set `max_spend_usd` for the `api` backend.

### `collections.json` — groups + grouping + extract *(private, gitignored)*

```json
{
  "groups": ["Hustling", "Biz", "Creative", "Biz - Clothing", "Content", "Lifestyle", "uncategorized"],
  "collections": {
    "Coding - AI":      { "group": "Hustling",  "extract": true },
    "Website Handling": { "group": "Hustling",  "extract": true },
    "Makeup":           { "group": "Lifestyle", "extract": false }
  }
}
```

- **`groups`** — ordered list; processing runs in this order. You order ~6 groups, not every folder. Group names live here (private file), never in committed code.
- **`group`** — which group a collection belongs to (its tag vocab + processing slot).
- **`extract`** — `true` → transcript/OCR + LLM enrich; `false` → deterministic branch (tag from collection, title from caption/author).
- A cross-group item is enriched at its **last** group in the `groups` list that has `extract` (first-time only). Items in no named collection fall into `uncategorized` (processed last).

Generate/refresh it with `isa discover` (interactive on first run; smart-merge afterwards — it
only prompts for new/removed collections and preserves your annotations).

### `tags.json` — tag vocabulary *(private, gitignored)*

Per-group + cross-group tags with one-line definitions. Built during **calibration** (§4), not by hand from scratch.

```json
{
  "content_type": { "tool": "a thing to use", "explainer": "how it works", "...": "..." },
  "groups":  { "Hustling": { "seo": "search ranking & visibility", "...": "..." } },
  "cross_group": { "ai": "AI applied in a non-eng domain", "...": "..." }
}
```

### `routes.json` — optional routing *(committed example)*

Maps `tag > collection > group → route_target` (tag-specific wins, then collection, then group default). Omit or disable to leave items at `Tagged`.

---

## 3. Running

```bash
isa discover                  # 0. surface collections + links; configure / diff
isa run --mode first-time     # bulk: per group → extract → calibrate → enrich
isa run --mode incremental    # delta: new saves only, reuse locked vocab
isa run --stage extract       # run a single stage (e.g. just drain extraction)
isa status                    # per-group counts: imported/extracted/tagged/failed/left
isa backup [--restore-check]  # snapshot Notion to JSON (+ optional restore test)
```

**Command / flag reference (allowed values):**
```
isa discover
isa run   --mode  {first-time | incremental}        # default: incremental
          --stage {discover | ingest | select | extract | calibrate | enrich | deterministic | route}
          --group <name>        # restrict to one group (e.g. calibrate a single group)
          --limit <N>           # cap items processed this run
          --reextract           # re-run extract on already-Extracted items (e.g. new engine)
          --reenrich            # re-tag already-Tagged items (e.g. new vocab)
          --retry-failed        # reprocess items currently in Failed
isa status
isa backup [--restore-check]
```
Omit `--stage` to run the whole mode end-to-end.

**Preflight** runs automatically before any run: verifies the chosen backend (Ollama up /
API key valid / session files writable), Notion reachability, and engine imports — and fails
fast with a clear message rather than mid-batch.

**First-time order of operations (per group):**
1. `extract` drains the group to `Extracted` (local; transcript + OCR).
2. `calibrate` (§4) locks the group's tag vocab.
3. `enrich` loops the group → `Tagged`.
4. Spot-check, then the next group.

**Incremental** runs the same stages on just the new delta, in group order, with **no
calibration** (vocab already locked).

---

## 4. Calibration (first-time, per group)

Tagging accuracy is front-loaded here so the full run needs little correction.

1. `isa run --stage calibrate --group "<Group>"` samples ~15–20 items' caption/OCR/transcript and the LLM **proposes** a candidate vocab.
2. Review the proposal; edit `config/tags.json` to **refine** (rename/drop/add, fix definitions).
3. It enriches the sample with the refined vocab and shows the resulting tags.
4. Adjust and repeat until the sample tags look right, then it **locks** the vocab.
5. Proceed to the full-group enrich.

Cross-group items (in collections spanning groups) receive the **union** of their groups'
vocab and are enriched once, when the **last** of their groups runs.

---

## 5. The enrich loop (session backends)

For `claude-code` / `cowork`, enrichment runs as a review-friendly loop. All state lives in
Notion + `tmp/enrich/`, so it is **crash-resumable and compaction-safe**:

1. `isa run --stage enrich` prepares the next batch → `tmp/enrich/prompt.txt`.
2. The session reads the prompt, writes `tmp/enrich/results.json` (title + summary + externals + tags).
3. Results are applied to Notion (`Tagged`); the loop advances to the next batch.

In **Cowork**, one kickoff message drives the whole loop ("run until `isa status` shows zero;
compact freely between batches — all state is in Notion"). If the session dies, restart with
the same message: Notion status resumes exactly where it left off. `local`/`api` backends do
steps 1–3 automatically with no session.

---

## 6. Failures, guardrails, backup

- **Failure triage:** `isa status` lists per-group `Failed` + no-content + remaining. `Failed` items keep partial data + `failure_notes`. Retry after fixing the cause: `isa run --stage extract --retry-failed`.
- **No-content:** on the first full batch, investigate *why* an item has no transcript/OCR/caption (extraction bug? over-aggressive VAD? carousel scope?) before trusting the deterministic branch — only true no-content should fall there.
- **Guardrails:** set `guardrails.max_spend_usd` / `max_items_per_run` in `run.json`; the run stops before exceeding them.
- **Backup:** `isa backup` before any bulk re-run; `isa backup --restore-check` periodically (a backup never restored isn't a backup).

---

## 7. Reprocessing

`extract_version` and `enrich_version` are independent:
- Re-extract (e.g. after a whisper upgrade) without re-enriching: `isa run --stage extract --reextract`.
- Re-tag under a new vocabulary without re-extracting: `isa run --stage enrich --reenrich`.

`raw_extraction` is never overwritten, so re-parsing slides/audio never requires re-scraping Instagram.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `Ollama not reachable` | `sudo systemctl start ollama` (or `ollama serve`); check `OLLAMA_BASE_URL` |
| `ANTHROPIC_API_KEY not set` (api backend) | add it to `.env`, or switch `enrich.backend` |
| Instagram redirects to login mid-run | session expired — delete `session_cookies.json`, re-run to re-auth |
| `HTTP 429` during extraction | raise inter-item delay; already-extracted items are skipped on re-run |
| Browser window doesn't appear (WSL) | run with `--headed` and ensure VcXsrv is running (see `legacy/README.md`) |
| Transcript is gibberish on a music reel | ensure `transcript.vad: true` — VAD skips non-speech and prevents hallucination |
| Property errors on write | the pipeline auto-creates missing properties; verify the integration has edit access |
