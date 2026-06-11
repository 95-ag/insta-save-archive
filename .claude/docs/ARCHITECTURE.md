# Insta-Save — Architecture (v2)

> **Status:** target architecture, in build. The running v1 lives in `legacy/`.
> Supersedes `.claude/docs/archive/PROJECT.md` and `.claude/docs/archive/IMPLEMENTATION_PLAN.md`.
> Last updated: 2026-06-09.

A personal knowledge pipeline that ingests Instagram saves into Notion, extracts
their content, enriches it with an LLM into titles/summaries/externals/tags, and
(optionally) routes high-value items to downstream systems. Instagram is the first
source; the design stays source-agnostic.

---

## 1. Principles (carried forward, still true)

- **Notion is the canonical state store.** Pipeline state = item `status`. Everything is resumable from it.
- **Idempotent + reprocessable.** Re-running any stage is safe. Raw inputs are preserved so prompts/models can improve without re-scraping.
- **Fail loud, preserve data.** Errors → `Failed` + `failure_notes`; never silently drop or overwrite good data.
- **Config over code.** Backends, ordering, batch sizes, engine choices live in config — not hardcoded.
- **Local-first, but backend-agnostic.** No daemons/queues/cloud required to run. The *enrich* brain is pluggable: local LLM, Claude subscription (Code/Cowork), or API.
- **Complexity requires evidence.** Prefer config/prompt/selector changes over new infrastructure.

---

## 2. Pipeline overview

```
0. CONFIG + DISCOVER ─ surface collections+links; first-time: set group + extract per collection;
                       incremental: prompt for new/removed collections
        │
1. INGEST (Playwright) ───────────────────────────────────► Imported
        │
2. SELECT ─ per collection: group · extract? ── branch:
        │                                              │
   extract = YES                                  extract = NO
        ▼                                              ▼
3. EXTRACT (local engines: transcript + OCR/vision) ─► Extracted
        │                                              │
   [first-time, per group]                             │
3.5 CALIBRATE ─ sample caption/OCR/transcript →        │
     LLM proposes tag vocab → you refine → lock         │
        ▼                                              ▼
4. ENRICH ── BACKEND (local | api | claude-code | cowork)   5. DETERMINISTIC
     one pass: title + summary + externals + tags             tags ◄ collection
        │  ─► Tagged                                          title ◄ collection/
        │                                                            caption/author
        │                                                     │ ─► Tagged
        └───────────────────────────┬─────────────────────────┘
                                     ▼
6. ROUTE (optional, deterministic) ─ route_target ◄ tags/collection/group ─► Routed
                                     ▼
              handoff to per-route pipeline (separate project, out of scope)
```

**Status machine (v2):** `Imported → Queued → Extracted → Tagged → Routed`, plus `Failed`.
`Summarized` is **removed** — the one-shot enrich (stage 4) lands directly at `Tagged`. The
deterministic branch (stage 5) reaches `Tagged` without extract/enrich.

---

## 3. Run modes

| | **First-time (bulk backfill)** | **Incremental (steady state)** |
|---|---|---|
| Trigger | Initial run over the whole archive | New saves since last run |
| Granularity | **Stage-at-a-time per group**: drain Extract for the group → calibrate → drain Enrich | Small delta; group order still respected |
| Ordering | Group order (position in the `groups` list) | Same ordering, applied to the delta |
| Calibration (3.5) | **Yes** — per group, before its enrich loop | **No** — reuses the locked vocab |
| Human-in-loop | Per-group: vocab refine + spot-check | Spot-check only |
| Correction cost | Paid **once per group** | Near-zero |

The calibration gate is what makes tagging tractable: the hard part (vocabulary + tag
quality) is front-loaded onto a ~15–20 item sample per group, validated, then locked.
Bulk runs **separate the extract loop from the enrich loop** (extract must finish first so
calibration has content to sample).

---

## 4. Stages

| # | Stage | Tools | Backend? | Status out |
|---|---|---|---|---|
| 0 | Discover + config | `list_collections` (Playwright) + config loaders | no | — |
| 1 | Ingest | Playwright (session/crawler/extractor) | no | `Imported` |
| 2 | Select | `collections.json` (group/extract) | no | `Queued` or → branch |
| 3 | Extract | transcript + OCR/vision engines | engine-tiered | `Extracted` |
| 3.5 | Calibrate | sample + chosen backend proposes vocab | yes | locks vocab |
| 4 | Enrich | one-shot LLM | **yes** | `Tagged` |
| 5 | Deterministic | pure Python (maps/templates) | no | `Tagged` |
| 6 | Route | pure Python (`routes.json`) | no | `Routed` (optional) |

- **Discover** surfaces all collections + post links. First-time → prompt group + extract per collection (group *order* is set once in the `groups` list). Incremental → diff config, prompt only new/removed collections (smart-merge). v2 uses **one** harvester/merger — v1 had two divergent copies (`crawler.py` vs `list_collections.py`); see `CARRYOVER.md`.
- **Ingest** pulls metadata **yt-dlp `--dump-json` first** (more 429-resilient, works on image posts), browser render as fallback, with a **wall guard** (stop after 5 consecutive yt-dlp failures, defer to the next run).
- **Select** fans items to the extract path (`Queued`) or the deterministic branch (`extract=no` collections — no deep content worth a transcript).
- **Enrich** is one LLM pass producing all four fields from `title-seed + transcript + ocr_text + caption`. Replaces v1's separate `title` (Ollama) + `summarize` (Claude) passes for extracted items.
- **Deterministic** branch tags from collection membership and titles from collection/caption/author — no LLM, for thin/visual saves.

---

## 5. Enrich backends (the pluggable core)

**One file contract, four implementations.** Every backend reads `tmp/enrich/batch.json`
and writes `tmp/enrich/results.json` against `enrich_schema`. Only *who fills results* differs:

| Backend | Fills results by | Automated? | Quality | Cost |
|---|---|---|---|---|
| `local` | Ollama call inline (qwen2.5) | yes | title-only (D8, spike-confirmed) | free |
| `api` | Anthropic SDK call inline | yes | high | $ per token |
| `claude-code` | a Claude Code session (`--prepare`/`--apply`) | yes¹ | high | subscription |
| `cowork` | a self-paced Cowork loop (one kickoff msg) | semi | high | subscription |

The pipeline core (build batch → hand to backend → apply results → write Notion) is identical
across all four. `enrich.backend` in run config selects one. Each backend also carries
**`model`** and **`effort`** (effort → thinking budget on `api`, model-size on `local`, advisory
on sessions). **On sessions, model+effort set the context capacity that caps batch size — confirm
both at run start.** (v1 observation: a Sonnet+low session fills context and needs `/compact` far
sooner than Opus+high, so the safe `char_budget`/`max_items` ceiling scales with model+effort, not
a single fixed number.)

¹ **`claude-code` runs fully hands-off today with no new code** — a single session loops `--prepare`
→ dispatch a fresh **fill-subagent** (reads `prompt.txt`, writes `results.json`) → `--apply`, until
drained. The per-batch subagent keeps the driver session's context clean. This *is* the cowork loop
below, done by hand; **`cowork` is the durable, compaction-safe productization** of it.

**Cowork loop design (the friction-killer):** all state lives in Notion + `tmp/` files, never
in conversation. So the loop is: `--prepare` next batch → read prompt → write results → `--apply`
→ repeat until `--status` is zero. Because nothing important lives in chat, **auto-compaction is
safe and the loop resumes after any crash** (Notion status drives `--prepare` to the next undone
batch). One kickoff message; idempotent; resumable.

### 5.1 Dynamic batching (per backend)

`backend.plan_batches(candidates, run_config)` returns batches by a backend-appropriate strategy:

| Backend | Strategy | Why |
|---|---|---|
| `local` | `single` (sequential, checkpoint every ~25) | 1 GPU, no batch gain |
| `api` | `token_budget` + parallelism, or **Message Batches API** for bulk | ~50% cheaper async for first-time |
| `claude-code` / `cowork` | `char_budget` + `max_items` (~15) | fit one session pass before compaction (ceiling scales with model+effort); small = resumable |

---

## 6. Extract engines

### 6.1 Transcript
**Choice (spike-validated 2026-06-09): `faster-whisper base int8` on CPU + VAD + tuned params.**
On-device benchmark over 4 speech-heavy Reels (500–600 words each):

| Config | avg s/clip | Quality |
|---|---|---|
| base + v1 params | 14.6s | baseline |
| **base + tuned** | **11.0s** | identical content, fewer fallback retries |
| small + tuned | 30.6s | ~identical content (minor punctuation only) |
| distil-large-v3 + tuned | 79.7s (+68s load) | ~identical; impractical on CPU |

- **Model: `base` int8.** `small`/`distil` produced essentially the same content (word counts within ~1%; only punctuation/capitalization differs) at 3×–7× the cost — not worth it for summarization input.
- **Tuned params (adopt — faster *and* at least as accurate):** `condition_on_previous_text=False`, `temperature=0.0`, `vad_filter=True` (Silero), explicit `no_speech_threshold=0.6` / `compression_ratio_threshold=2.4` / `log_prob_threshold=-1.0`. VAD kills hallucination on music-only reels; `temperature=0.0` + no prev-text conditioning removed the fallback retries that made v1 params ~30% slower.
- **CPU-only.** GPU is unavailable (`libcublas.so.12` not installed); `base+tuned` at ~11s/clip and 2.3 GB peak RAM is fast enough that CUDA isn't worth setting up (and the 4 GB GPU is contended by Ollama).
- Language auto-detect stays ON. Revisit `small`/`distil` only if non-English or noisy content later shows `base` is insufficient (evidence-driven).

### 6.2 OCR / vision (`ocr.mode`)
Configurable: `none | rapidocr | local_vlm | claude_vlm | escalate`.
- **`escalate` (recommended):** RapidOCR runs and scores its own confidence/coverage; only sub-threshold slides are passed **on top** to Claude vision and merged. `escalate_threshold` is configurable.
- **No local VLM on 4 GB** — only tiny models (Moondream2 ~2 GB, Qwen2-VL-2B int4 ~3 GB) fit, weak on dense slide text, and can't co-reside with whisper. Local-VLM remains a selectable option for larger machines.
- **Claude vision** has zero local memory cost; per-image token cost is small (≈ cents/carousel). *(Exact current pricing: confirm via the `claude-api` reference before budgeting a large run.)*

---

## 7. Tagging

### 7.1 Three-axis taxonomy
- **Content-type** (exactly 1): describes *what kind* of item.
- **Granular topic** (0–3, with cross-group): *what it's about*, specific to the group.
- **Cross-group topic**: broad themes spanning ≥2 groups.

Worked example — **Hustling** (the calibration test group):
- Content-type (5): `inspo` · `tool` · `tutorial` · `tips/hacks` · `explainer`
- Granular (8): `claude-code` · `ai-ml-engg` · `vibe-code` · `web-dev` · `ui-ux` · `seo` · `job-search` · `freelance`
- Cross-group (7): `ai` · `design` · `branding` · `marketing` · `automation` · `productivity` · `content-creation`

Cap: 1 content-type + up to 3 topics. The classifier prompt carries one-line definitions per
tag; constrained-enum decoding makes out-of-vocab output impossible, and a validation pass
dedupes/clamps and blanks an invalid content-type (review escape hatch). Vocab lives in
`config/tags.json` (gitignored; per-group + cross-group sections).

### 7.2 Calibration (per group, first-time)
1. Query ~15–20 of the group's items; gather `caption + ocr_text + transcript`.
2. **LLM proposes** a candidate vocab from the sample → **you refine** → lock into `tags.json`.
3. Run enrich on the sample, eyeball tag quality, adjust vocab, repeat until good.
4. Lock; run the full-group enrich loop.

### 7.3 Cross-group items
An item in collections spanning groups gets the **union** of its groups' granular vocab + cross-group,
and is enriched **once, at its last-in-order group that has `extract`** — by then every vocab it needs is
locked. **Calibrate by membership, enrich by that last group:** a group's vocab samples *any* item touching
it, but enrich fires only on items whose last extract group is the current one. An item is on the extract
path if **any** of its collections is `extract=yes` (richer wins); deterministic only if all are `extract=no`.
This ordering only matters during **first-time bulk** — incremental already has every vocab locked.

---

## 8. Routing (optional)
Deterministic, no model: `config/routes.json` maps `tag > collection > group → route_target`
(tag-specific wins, else collection, else group default). Writes `route_target` + `Routed`.
Disable-able (items stay `Tagged`). Hands off to a per-route pipeline (separate project).
Rationale: collection membership already encodes destination; a model adds cost and unreliability
for a decision config makes 100% reliably.

---

## 9. Notion schema

### 9.1 Current audit (live, 2026-06-08 — 19 properties)
Keep: `title`, `source_id`, `ig_link`, `author`, `type`(Reel/Carousel/Post), `collection`(44 opts),
`caption`, `posted_date`, `Created Date`, `status`, `priority`(High/Med/Low), `failure_notes`,
`transcript`, `ocr_text`, `summary`, `externals`, `last_processed_at`.

Issues found:
- **No `tags`, no `route_target`** — to add.
- **`collection` has a stray `test` option** — remove.
- **`processing_version` (188/600) is shared by extract + enrich** (83 Extracted + 105 Summarized = 188) — split.
- **`status` options** = Imported/Queued/Extracted/Summarized/Failed — add `Tagged`/`Routed`, retire `Summarized`.

### 9.2 Target changes
| Action | Property | Notes |
|---|---|---|
| **Add** | `tags` (multi_select) | content-type + topic tags |
| **Add** | `route_target` (select) | optional; deterministic |
| **Add** | `extract_version` (rich_text) | transcript/OCR engine + params |
| **Add** | `enrich_version` (rich_text) | LLM model + prompt + **tag-vocab version** |
| **Remove** | `processing_version` | after migrating into the two split fields |
| **Keep** | `raw_extraction` | immutable extract payload (reprocessing safety net); version lives in `extract_version`, NOT inside the payload |
| **Status** | add `Tagged`, `Routed`; drop `Summarized` | migrate existing `Summarized` → `Tagged` first |
| **Clean** | `collection` | drop `test` option |

**`raw_extraction` decision:** keep it. It holds per-slide arrays/methods that the flat
`transcript`/`ocr_text` fields don't, and it's the only durable record enabling re-OCR/re-parse
without re-scraping. It is never versioned internally — `extract_version` carries the version.

---

## 10. Cross-cutting

- **Versioning / reprocessing:** `extract_version` and `enrich_version` are independent → re-extract (e.g. new whisper) without re-enriching, and re-tag (new vocab) without re-extracting. `raw_extraction` is never overwritten.
- **Failure triage:** `--status` reports per-group counts of `Failed` + no-content + remaining; `Failed` items keep partial data + `failure_notes`; explicit retry path. In the **first full batch, no-content items are debugged (not auto-routed)** — only *true* no-content falls to the deterministic branch.
- **Guardrails (important):** per-run caps — `max_items` and/or `max_spend` (api) — plus Claude-Max usage awareness for sessions. A bulk run cannot silently overrun budget.
- **Preflight:** before a run, verify the chosen backend (Ollama up / API key valid / session files writable), Notion reachable, engines importable. Fail fast.
- **Backup + restore-check:** snapshot the Notion DB to JSON before bulk re-runs; periodically run a restore-to-scratch check (a backup never restored isn't a backup).
- **Uncategorized:** items in no named collection (the IG "all-posts" view) fall into a default last group `uncategorized`.
- **Terminal:** rich `StageProgress` is carried over and extended — per-group + per-stage bars, `isa status` as a summary table; all `logging` stays file-only and is never mixed with the live display.
- **Carry-over:** v1's hard-won fixes (selectors, CDN domain, UTF-16 truncation, content guard, reconcile invariants, VcXsrv runbook…) are catalogued in [`CARRYOVER.md`](CARRYOVER.md) — consult it when reimplementing each stage.

---

## 11. Folder structure

```
insta-save-archive/
├── legacy/                      # FROZEN v1 — runnable fallback during migration
│   ├── pipeline/  scripts/
│   └── README.md
├── insta_save/                  # v2 package (import name; distribution = insta-save)
│   ├── config/                  # typed loaders: run · collections · tags · routes
│   ├── orchestrator/            # runner (modes) · preflight · guardrails · batching
│   ├── stages/                  # discover · ingest · select · extract · calibrate · enrich · deterministic · route
│   ├── engines/                 # transcript · ocr · vision  (extract plugins)
│   ├── backends/                # base · local_ollama · api_anthropic · claude_code · cowork
│   ├── adapters/                # notion (state) · instagram/ (session·display·harvest·crawl·extractor·cookies)
│   ├── helpers/                 # observability (StageProgress, setup_logging)
│   ├── enrich_schema.py · reconcile.py · snapshots.py · backup.py
├── cli/isa.py                   # single entrypoint: isa discover|run|status|backup
├── config/                      # gitignored DATA: collections.json · tags.json · routes.json · run.json
├── prompts/                     # versioned enrich + calibrate prompts
├── tests/  tmp/  logs/
└── pyproject.toml               # installs insta_save* ; legacy run via its own note
```

Two plugin axes get their own folders — `engines/` (extract) and `backends/` (enrich) — so adding
a backend/OCR mode is a new file, not surgery. `stages/` is the linear pipeline (one concept per
file). `adapters/` isolates external systems. `orchestrator/` owns mode sequencing, preflight,
guardrails, batching. One `isa` CLI replaces scattered scripts.

---

## 12. Configuration files

| File | Committed? | Holds |
|---|---|---|
| `config/run.json` | yes (example) | mode · `enrich.backend/model/effort` · `ocr.mode/threshold` · ordering source · caps |
| `config/collections.json` | **no** (private) | ordered `groups` list (group names live here) + per-collection `{group, extract}` |
| `config/tags.json` | **no** (private) | per-group + cross-group vocab with definitions |
| `config/routes.json` | yes (example) | route map (optional) |

---

## 13. Design decision log

| # | Decision | Why | Rejected alternative |
|---|---|---|---|
| D1 | Two run modes (bulk vs incremental) | Bulk needs calibration + checkpoints; steady-state must be light | One mode for both (calibration overhead every run) |
| D2 | Fork: extract path vs deterministic branch | Thin/visual saves don't warrant transcription or an LLM | LLM-enrich everything (waste on no-content) |
| D3 | One-shot enrich (title+summary+externals+tags) | Claude already loads the content; extra fields are ~free; coherent labels | Separate passes (re-reads content N times) |
| D4 | Drop `Summarized` status | One-shot lands at `Tagged`; the intermediate stop no longer exists | Keep it (ghost status, friction) |
| D5 | Backend file-contract (local/api/claude-code/cowork) | Subscription, API, and local must all work; user picks at start | Hardcode one engine; lose portability (Phase 6) |
| D6 | Cowork loop: state in Notion, not chat | Makes auto-compaction safe and the loop crash-resumable | State in conversation (breaks on compaction) |
| D7 | Per-backend dynamic batching | Optimal chunk differs (single vs token-budget vs char-budget) | One fixed batch size |
| D8 | Local LLM scope = title-from-caption + deterministic only | 7B is reliable for constrained/automated tasks, weak for semantic extraction. **Spike-confirmed 2026-06-10:** constrained `format=` fixes JSON compliance (the old reason is moot), but qwen2.5:7b summaries drop the high-value specifics (numbers, benchmarks, names) and externals come back empty or hallucinated — semantic *quality*, not compliance, is the wall | Local for tags/summary (compliance + quality issues) |
| D9 | Three-axis tags + per-group vocab | Granular within group, correct cross-tagging, content-type for search/downstream | Flat tag list (loses the kind-of-item axis) |
| D10 | Calibration gate (sample → propose → refine → lock) | Tag correction is expensive; front-load it onto a sample per group | Tag the whole group then correct (huge correction load) |
| D11 | Cross-group: union vocab, enrich at last extract group (calibrate by membership) | All vocabs locked before tagging; first-time-only concern | Earliest-order (tags before later vocabs exist) |
| D12 | Split `extract_version` / `enrich_version` | Independent reprocessing of extract vs enrich | One `processing_version` (conflated, ambiguous) |
| D13 | Keep `raw_extraction`, unversioned | Durable per-slice payload; re-parse without re-scraping | Drop it (lose reprocessing record) |
| D14 | Transcript: base int8 (CPU) + tuned params | On-device spike: small/distil gave ~identical content at 3–7× cost; tuned params are faster + as accurate | small/distil (cost, no gain); GPU (no cuBLAS) |
| D15 | OCR: RapidOCR + escalate-to-Claude-vision | 4 GB can't run a good local VLM; escalate only weak slides | Always-VLM (memory/cost) or OCR-only (misses graphic slides) |
| D16 | Routing deterministic + optional | Collection encodes destination; 100% reliable, no model | AI-chosen routing (cost, unreliable) |
| D17 | Fresh `insta_save/` + `legacy/` fallback | Clean v2 boundaries; v1 runnable during migration | In-place rewrite (risky, no fallback) |
| D18 | LLM proposes vocab, human refines | Faster than authoring cold; human keeps control | Pure-manual (slow) or pure-LLM (uncurated) |
| D19 | Guardrails + preflight + backup/restore | Bulk re-runs are high-blast-radius; fail fast, cap spend, be restorable | Trust the run (silent overruns, unrecoverable writes) |
| D20 | Group-level ordering (not per-collection) | ~6 groups to order, not ~44 folders; group names move to the private config | Per-collection `order` (tedious; leaks group names in code) |
| D21 | Ingest metadata yt-dlp-first, browser fallback, wall guard | More 429-resilient; works on image posts; v1-proven | Browser-first (fragile, rate-limited) |
| D22 | Consolidate v1's two collection scrapers/mergers into one discover stage | v1 had divergent duplicates (`crawler.py` vs `list_collections.py`) | Keep both (maintenance trap) |

---

## 14. Out of scope / later
- **Phase 4 reorg** — audit/merge/rename collections once items are enriched; re-tag stale-vocab items via `enrich_version`.
- **Downstream route pipelines** — per-`route_target` extraction + writes to target DBs (separate project).
- **Exact API/vision pricing** — confirm via `claude-api` reference before a large `api`-backend run.
