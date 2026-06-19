# Insta-Save — Architecture (v2)

> **Status:** fully built (stages 0–6 · all four enrich backends · orchestrator · safety layer). The capstone clean run (#7) is the only remaining sub-project. The running v1 lives in `legacy/`.
> Supersedes `.claude/docs/archive/PROJECT.md` and `.claude/docs/archive/IMPLEMENTATION_PLAN.md`.
> Last updated: 2026-06-17.

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
3. EXTRACT (local engines: transcript + OCR)  ──► Extracted
     Carousel/Post: RapidOCR + persist slide      │
     images (tmp/slides/<shortcode>/).             │
     Reels: transcript + frame-OCR (frames         │
     ephemeral).                                   │
        │                                          │
   [first-time, per group]                         │
3.5 CALIBRATE ─ sample caption/OCR/transcript →    │
     LLM proposes tag vocab → you refine → lock     │
        ▼                                          ▼
4. ENRICH ── BACKEND (local | api | claude-code | cowork)   5. DETERMINISTIC
     two modality lanes per group:                            tags ◄ collection
       text lane  (Reels/IGTV): caption+transcript+OCR        title ◄ collection/
       vision lane (Carousels/Posts): caption+OCR+            caption/author
                   slide images (fill-subagent Reads them)    │ ─► Tagged
     → title + summary + externals + tags
        │  ─► Tagged
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
| Calibration (3.5) | **Yes** — per group, before its enrich loop | **Yes, if needed** — reuses locked vocab for existing groups; runs the interactive gate inline for any new uncalibrated group (e.g. a collection added since last run) |
| Human-in-loop | Per-group: vocab refine + spot-check | Spot-check only for existing groups; new/uncalibrated groups run the same interactive calibrate gate inline (no error) |
| Correction cost | Paid **once per group** | Near-zero |

The calibration gate is what makes tagging tractable: the hard part (vocabulary + tag
quality) is front-loaded onto a ~15–20 item sample per group, validated, then locked.
Bulk runs **separate the extract loop from the enrich loop** (extract must finish first so
calibration has content to sample).

### 3.1 The sequencer (orchestrator)

`orchestrator/sequence.py` provides `compute_plan(env, run_cfg, collections_cfg, vocab, backend, routes) -> Plan`
(a **pure read** of Notion state, no writes) and `run_first_time` / `run_incremental` (the guided,
resumable loop). For each group in `groups` order, `compute_plan` emits the next `GroupStep` via a
**5-rule decision table**: (1) Queued items → `extract`; (2) enrichable items + uncalibrated vocab →
`calibrate` (a human gate); (3) enrichable + calibrated → `enrich` (automated iff `backend.AUTOMATED`,
else an agent-filled gate); (4) routing enabled + Tagged items → `route`; (5) else `done`. Counts:
`queued` / `tagged` by collection membership, `enrichable` by `enrich_group==group` (cross-group, §7.3).

The runner **recomputes the plan from Notion after EACH automated step** — state lives in Notion, so
the loop is crash/compaction-safe and resumable (D6) with no in-memory or file state. It **stops and
returns only at an agent-filled enrich gate** (`backend.AUTOMATED is False`), and has a
**no-progress guard**: a `(group, action)` that repeats without advancing returns instead of looping
forever. A **calibrate** step is run **inline and interactively** in BOTH first-time and incremental
modes (`orchestrator/calibrate_gate.py`): the gate samples the group, the backend drafts a vocab if it
exposes `propose_vocab`, the human accepts/edits/aborts, and `lock_vocab` merges it into
`config/tags.json` before the loop continues with the reloaded vocab — calibrate is never a gate stop.
`dry_run=True` skips discover/ingest/select and returns the computed plan without executing any stage.
CLI: `isa run --mode first-time|incremental [--dry-run] [--select-mode inline|editor]`. On
**first-time only**, before anything else, the **run-config gate** (`orchestrator/config_gate.py`)
runs: it seeds `config/run.json` from a `claude-p` default if absent, then **keyboard-selects**
backend/model/effort (arrow-pick with per-option help; open fields offer an "Other…" free-text
entry) after a start mode-prompt (inline picker vs the whole file in `$EDITOR`, defaulting from
`--select-mode`), then a **Proceed / Go back / Edit in $EDITOR / Abort** confirm — so
`get_backend`/preflight see the chosen backend (incremental skips this and uses the locked
`run.json`). The keyboard-select prompts are built on `helpers/tui.py` (questionary; D27). It then
calls `run_pipeline` (`orchestrator/pipeline.py`), which
**front-folds discover → ingest → select** before entering the sequencer loop — first-time crawls
fresh, incremental reuses snapshots (`--fresh` forces a re-crawl in incremental). It runs preflight
+ the item-cap guardrail + a session usage reminder, then the loop, then prints the per-group plan and
the next gate.

---

## 4. Stages

| # | Stage | Tools | Backend? | Status out |
|---|---|---|---|---|
| 0 | Discover + config | `list_collections` (Playwright) + config loaders | no | — |
| 1 | Ingest | Playwright (session/crawler/extractor) | no | `Imported` |
| 2 | Select | `collections.json` (group/extract) | no | `Queued` or → branch |
| 3 | Extract | transcript + OCR engines; persists carousel/post slide images | engine-tiered | `Extracted` |
| 3.5 | Calibrate | sample + session proposes vocab (backend-independent, human-reviewed) | yes | locks vocab |
| 4 | Enrich | one-shot LLM; **two modality lanes**: text (Reels/IGTV) + vision (Carousels/Posts) | **yes** | `Tagged` |
| 5 | Deterministic | slug-tag union + title (template default, opt-in llm) | title only (opt-in) | `Tagged` |
| 6 | Route | pure Python (`routes.json`) | no | `Routed` (optional) |

- **Discover** surfaces all collections + post links. First-time → **keyboard-select** group + extract per new collection (arrow-pick an existing group, "New group…", or "→ Edit the rest in $EDITOR" to hand the remaining collections to the editor mid-loop), behind the same inline-vs-`$EDITOR` mode-prompt and Proceed/Go back/Edit/Abort confirm as the run-config gate (`helpers/tui.py`; D27); group *order* is set once in the `groups` list. Incremental → diff config, prompt only new/removed collections (smart-merge). v2 uses **one** harvester/merger — v1 had two divergent copies (`crawler.py` vs `list_collections.py`); see `CARRYOVER.md`.
- **Ingest** pulls metadata **yt-dlp `--dump-json` first** (more 429-resilient, works on image posts), browser render as fallback, with a **wall guard** (stop after 5 consecutive yt-dlp failures, defer to the next run).
- **Select** fans items to the extract path (`Queued`) or the deterministic branch (`extract=no` collections — no deep content worth a transcript).
- **Enrich** is one LLM pass producing all four fields from `title-seed + transcript + ocr_text + caption`. Replaces v1's separate `title` (Ollama) + `summarize` (Claude) passes for extracted items.
- **Deterministic** branch (st.5) tags `extract=no` items from the **slugified union of their collection names** and titles them by a config mode — `template` (`{collection} — {author}`, default) or opt-in `llm` (title from caption+collection+author). No transcript/OCR/semantic-LLM; `summary`/`externals` stay `None`; status → `Tagged` (marker `enrich_version="deterministic-v2.0"`). The `llm` mode is a **thin parallel `--prepare`/`--apply`** running on the formal `Backend` protocol (results parsed via `backends.base.parse_results`, backend selected via `get_backend`); automated backends (`local`/`api`) drain it in-process exactly like enrich, and it shares the multilingual `prompt.translate_directive` (narrowed to just the title). `output_language` (top-level run config) normalizes titles/summaries/tags to English — only raw transcript/OCR keep their original language.
- **Route** (st.6) is deterministic — `config/routes.json` maps `tag > collection > group → route_target` (tag wins, else collection, else group). Disabled when `routes.json` is absent or empty (every item stays `Tagged`). `isa run --stage route` accepts `--dry-run` (previews, no writes).

---

## 5. Enrich backends (the pluggable core)

**One file contract, five implementations.** Every backend reads `tmp/enrich/batch.json`
and writes `tmp/enrich/results.json` against `enrich_schema`. Only *who fills results* differs:

| Backend | Fills results by | Execution model | Quality | Cost | Vision-capable |
|---|---|---|---|---|---|
| `local` | Ollama call inline (qwen2.5) | **automated** | title-grade (D8, spike-confirmed) | free | no |
| `api` | Anthropic SDK call inline | **automated** | high | $ per token | yes |
| `claude-p` | headless `claude -p --output-format json` (prompt on STDIN, parses `result` envelope) | **automated** | high | subscription (Claude Max, no key) | yes |
| `claude-code` | a Claude Code session (`--prepare`/`--apply`) | **agent-filled**¹ | high | subscription | yes |
| `cowork` | a self-paced Cowork loop (one kickoff msg) | **agent-filled** | high | subscription | yes |

`claude-p` is the **default backend for the one-call orchestrator** (D25): Claude Max users have no
API key, and a headless `claude -p` subprocess gives `claude-code`-grade quality with zero manual
steps (`AUTOMATED=True`, in-process drain, no relay).

The `AUTOMATED` flag splits the backends into two execution models. **Automated** backends
(`local`, `api`, `claude-p`) fill `results.json` in-process — the CLI drains the whole group in **one command**
(prepare → fill → apply, looped). **Agent-filled** backends (`claude-code`, `cowork`) return
`FillResult(external=True)`: the CLI does a single prepare or apply step and the driving Claude
session / Cowork loop runs the fill and re-invokes. `VISION_CAPABLE` is a separate capability flag
(everything but `local` can see images) — the vision lane is gated on it (§5.1 preflight). For the
`api` backend the per-run spend guardrail fires **per batch** inside `drain_enrich_group` — after
`prepare` writes `prompt.txt`, before `backend.fill` — reading the rendered prompt length and raising
if the estimate exceeds `guardrails_max_spend_usd`. `enrich.drain_enrich_group(lanes=None)` is the
shared automated drain (both lanes; vision gated on `VISION_CAPABLE`) used by BOTH the sequencer and
the CLI `--stage enrich` automated path (which passes a single `lanes=[lane]`).

**Design layer (the `Backend` protocol, D5).** `backends.base` defines the `Backend` protocol —
each backend is a module exposing `NAME` · `AUTOMATED` · `VISION_CAPABLE` ·
`batch_budgets(run_cfg) -> Budgets` · `fill(env, run_cfg, enrich_dir) -> FillResult` — plus a
`get_backend(name)` registry (lazy import, so a missing optional dep only fails when that backend is
selected) and the shared `parse_results`. `Budgets(char_budget, max_items, image_token_budget)`
sizes a batch; `FillResult(external, filled, failed)` reports what `fill` did. The automated drain
loop stops on either a **DRAINED sentinel** (prepare batched nothing) or a **no-progress guard**
(prepare batched items but apply wrote none — those items stay un-advanced and would re-select
forever, so the loop breaks instead of spinning). **Automated fills normalize `page_id`/`source_id`
from `batch.json`** — the model is never trusted for identity (fabricated ids are dropped).

The file contract (`batch.json` → `results.json` under a backend-supplied directory) is identical
across all five; the **driver** branches on `AUTOMATED` (drain vs single-step) and `VISION_CAPABLE`
(lane preflight). `fill(env, run_cfg, enrich_dir)` is **dir-parameterized**, so the same backends
also drive the deterministic-title path (`tmp/deterministic/`), not enrich only (`tmp/enrich/`).
`enrich.backend` in run config selects one; `enrich.api_mode` (`sync` default | `batches`) picks the
`api` backend's call shape, and that backend reads an Anthropic key from env (`anthropic_api_key`).
`--status` (on `isa run --stage enrich`) calls `cowork.status` for a single group's remaining-enrichable count. (The top-level `isa status` command — §10 — is separate: a per-group table of all status counts.) Each backend also carries
**`model`** and **`effort`** (effort → thinking budget on `api`, model-size on `local`, advisory
on sessions). **On sessions, model+effort set the context capacity that caps batch size — confirm
both at run start** — now implemented by the first-time run-config gate (§3.1), where backend / model /
effort are chosen and confirmed before the long run (default backend `claude-p`). (v1 observation: a Sonnet+low session fills context and needs `/compact` far
sooner than Opus+high, so the safe `char_budget`/`max_items` ceiling scales with model+effort, not
a single fixed number.) The multilingual translate directive is centralized in
`backends.prompt.translate_directive` (output fields emitted in `output_language`, non-English
source translated + original language noted, raw transcript/OCR untouched) and is shared by enrich
and the deterministic-title path.

**Enrich is no longer strictly text-only.** For the vision lane (Carousels/Posts), the per-item
block in `prompt.txt` lists the slide image paths (under `tmp/slides/<shortcode>/`). On agent-filled
backends the fill-subagent `Read`s each image; the `api` backend attaches each slide as a base64
image block so the model SEES it directly. The image-token budget (`PER_SLIDE_IMAGE_TOKENS` ≈ 1600,
`image_token_estimate`, and `max_image_tokens` default 120 000) now lives in `backends.prompt`,
shared by all backends — the #4→#5 generalization of the vision contract under the `Backend`
protocol is **done** (the `VISION_CAPABLE` flag gates which backends may run the vision lane).

¹ **`claude-code` runs fully hands-off today with no new code** — a single session loops `--prepare`
→ dispatch a fresh **fill-subagent** (reads `prompt.txt`, including image paths for vision items,
writes `results.json`) → `--apply`, until drained. The per-batch subagent keeps the driver
session's context clean. This *is* the cowork loop below, done by hand; **`cowork` is the durable,
compaction-safe productization** of it.

**Cowork loop design (the friction-killer):** all state lives in Notion + `tmp/` files, never
in conversation. So the loop is: `--prepare` next batch → read prompt → write results → `--apply`
→ repeat until `--status` is zero. Because nothing important lives in chat, **auto-compaction is
safe and the loop resumes after any crash** (Notion status drives `--prepare` to the next undone
batch). One kickoff message; idempotent; resumable.

**`claude-p` (the one-call default).** Runs `claude -p --output-format json` headlessly as a
subprocess (prompt on STDIN since enrich prompts exceed argv limits; the `result` envelope key
carries the JSON array). No API key — uses a Claude Max subscription. `AUTOMATED=True`, so the
drain loop (§5) fills `results.json` in-process with no human steps. `VISION_CAPABLE=True` — the
same `IMAGES:`-path contract as `claude-code` works because `claude -p` reads slide images by file
path (so it must run with cwd = repo root for repo-relative paths to resolve). `--model` maps the
run.json alias (e.g. `claude-sonnet`) to the CLI value (`sonnet`) by stripping the `claude-`
prefix, controlling cost. `claude-p` also exposes `propose_vocab(prompt, model) -> dict`, which the
interactive calibrate gate uses to draft a vocabulary before the human reviews and locks it. Falls
under the same Claude-Max usage reminder as `claude-code` and `cowork`.

### 5.1 Dynamic batching (per backend)

Each backend exposes `batch_budgets(run_cfg) -> Budgets(char_budget, max_items, image_token_budget)`
(`backends.base`). `enrich.prepare` then greedily fills **one** budget-bounded batch: the first
item is always admitted, and subsequent items break early once either the `char_budget` (rendered
prompt length) or the `image_token_budget` would be exceeded. For automated backends the CLI
**drain loop** (§5) repeats prepare → fill → apply until prepare batches nothing.

| Backend | `batch_budgets` returns | Why |
|---|---|---|
| `local` | `Budgets(10**9, None, None)` — char/items uncapped, no image budget | per-item sequential `fill` (checkpoint every 25); 1 GPU, no batch gain, text-only |
| `api` | run-config budgets (`char_budget`, `max_items`, `image_token_budget`) | one whole-batch request per fill (`sync`, or one Message Batches request when `api_mode="batches"`) — per-item parallelism / async fan-out is **not built yet**, so the cheaper-async win is not realized at item granularity |
| `claude-p` | run-config budgets (`char_budget`, `max_items`, `image_token_budget`) | one `claude -p` subprocess call per fill (whole batch on STDIN); same ceiling rationale as the session backends — confirm `model` at run start |
| `claude-code` / `cowork` | run-config budgets (`char_budget`, `max_items` ~15, `image_token_budget`) | fit one session pass before compaction (ceiling scales with model+effort); small = resumable |

**Vision preflight.** A backend that is not `VISION_CAPABLE` (i.e. `local`) selected on the vision
lane fails fast — the CLI raises `SystemExit` before any work, so an image-blind backend can never
run the vision lane.

**Vision lane batching.** The vision lane additionally budgets on an **image-token estimate**
(`PER_SLIDE_IMAGE_TOKENS` ≈ 1600/slide, a conservative figure for a ~1080-wide IG portrait slide).
`image_token_budget` (`batch.max_image_tokens` in `run.json`, default 120 000) caps the total
estimated image tokens in a single batch, independently of `char_budget`. The first item is always
admitted; subsequent items break early if either budget would be exceeded.

Enrich runs **two modality lanes per group** — text (Reels/IGTV) is drained first, then vision
(Carousels/Posts). Each lane calls `--prepare`/`--apply` independently with its own budgets and
prompt template (`enrich_v2.0.txt` for text, `enrich_vision_v2.0.txt` for vision).

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
Configurable: `none | rapidocr`. (`local_vlm`, `claude_vlm`, and `escalate` are **retired** — see D15/D23.)

- **`rapidocr` (default):** RapidOCR runs on every carousel/post slide and every sampled reel frame. For carousel/Post slides, the image is **persisted** under `tmp/slides/<shortcode>/` (not deleted after OCR) so the vision enrich lane can read it later. `ocr_confidence` (mean RapidOCR per-detection score, 0–1) is stored as a diagnostic but **no longer gates anything** — every slide goes to the vision lane regardless of confidence, because confidence is a poor proxy for information capture (D15/D23).
- **Reels** stay text-only: transcript + frame-OCR text (frame images are ephemeral, cleaned up after OCR). No vision pass on reels.
- **`none`:** skips OCR entirely (transcript-only items).
- **`needs_vision` flag is retired.** It was removed from `slide_record` and `extract_ocr_frames` in #4. There is no longer an escalate threshold in config or code.
- **Slide image cache** lives at `tmp/slides/<shortcode>/slide<N>.jpg` (relative path stored in `carousel_slides[N].image` within `raw_extraction`). This is a local disk cache, NOT a new Notion property.

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
2. **LLM proposes** a candidate vocab from the sample → **you refine interactively** → lock into `tags.json`.
3. Run enrich on the sample, eyeball tag quality, adjust vocab, repeat until good.
4. Lock; run the full-group enrich loop.

> **Human-locked at the lock step; backend-assisted at the draft step.** Calibration is **not**
> routed through `Backend.fill()` — the vocab proposal is a human-reviewed gate, not a per-item
> automated fill. `calibrate.sample` writes `tmp/calibrate/prompt.txt`; the interactive gate
> (`orchestrator/calibrate_gate.py`) then checks whether the selected backend exposes
> `propose_vocab(prompt, model) -> dict`. If so (e.g. `claude-p`) it auto-drafts the proposal;
> otherwise it starts from an empty draft. Either way you enter an **interactive vocab editor**
> (D28, built on `helpers/tui.py`): read-only cross-axis **context** → an **edit loop** (reject one
> of the current group's granular topics / add a topic on any axis) → a merged **preview**
> (`config/tags.py merge_vocab`, no write) → **Confirm / Go back / Edit current ($EDITOR on the
> group proposal) / Edit all ($EDITOR on the whole `tags.json`) / Abort**. Confirm calls `lock_vocab`
> (granular set outright, content-type/cross-group additive); removing or moving a **shared**
> content-type/cross-group item is done via **Edit all** (the only path that writes `tags.json`
> directly, since `lock_vocab` never removes a shared item). The gate is reachable inline (the
> first-time loop) and **standalone** via `isa run --stage calibrate --group G`. So `enrich.backend`
> determines whether the draft is auto-generated, but the human lock always applies (D18).

### 7.3 Cross-group items
An item in collections spanning groups gets the **union** of its groups' granular vocab + cross-group,
and is enriched **once, at its last-in-order group that has `extract`** — by then every vocab it needs is
locked. Implemented via `collections.extract_groups_of` + `tags.union_topics`: `enrich.prepare`
computes the batch's group-union to render the prompt vocab block (`prompt.build_prompt(groups=…)`),
and `enrich.apply` validates EACH item against the union of ITS OWN groups' topics. **Calibrate by membership, enrich by that last group:** a group's vocab samples *any* item touching
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

`routes.json` shape: `{"by_tag": {tag: target}, "by_collection": {collection: target}, "by_group": {group: target}}`
(see `config/routes.example.json`). Disabled when absent or empty (`load_routes` → empty `Routes()` →
`route_for` returns `None` for every item). `isa run --stage route` accepts `--dry-run`.

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

**`carousel_slides` per-slide record shape (v2, post-#4):** `{slide, text, ocr_confidence, image}`.
`needs_vision` is **removed**. `image` is the slide image path relative to `tmp_dir`
(e.g. `slides/<shortcode>/slide1.jpg`). Slide images live in a local disk cache
(`tmp/slides/<shortcode>/`) — this is **not** a new Notion property; it is a local artefact
referenced by the vision enrich lane.

---

## 10. Cross-cutting

- **Versioning / reprocessing:** `extract_version` and `enrich_version` are independent → re-extract (e.g. new whisper) without re-enriching, and re-tag (new vocab) without re-extracting. `raw_extraction` is never overwritten.
- **Failure triage:** `isa status` reports per-group + TOTAL counts across all statuses (Imported/Queued/Extracted/Tagged/Routed/Failed) plus `remaining` (= Imported+Queued+Extracted); an item spanning groups counts once per group. `isa status --retry-failed` requeues each `Failed` item to `Extracted` (if it has `raw_extraction`) or else `Queued`, clearing `failure_notes`. `Failed` items keep partial data + `failure_notes`.
- **Guardrails (important):** per-run caps — `max_items` and/or `max_spend` (api) — plus Claude-Max usage awareness for sessions. A bulk run cannot silently overrun budget. Two wiring points: the item cap (`guardrails_max_items_per_run`) is checked once at run start in `isa run --mode` against the summed `remaining`; the spend cap (`guardrails_max_spend_usd`, api-only) is checked per batch inside `drain_enrich_group`.
- **Preflight:** before a run, `preflight(env, run_cfg, stages=…)` fails fast on: invalid `enrich.effort` (must be low/medium/high); missing Notion config (credential presence check, not a live ping); backend unreachable (Ollama health-check for `local`; API-key presence for `api`; nothing for the session backends); and extract engines not importable (`faster_whisper`/`rapidocr_onnxruntime`, only when extract is in the run's stages).
- **Backup + restore-check:** `isa backup` snapshots the whole Notion DB to `tmp/backups/notion-<ts>.json` (all statuses, no filter). `isa backup --restore-check` is a **dry structural verification** — it re-reads the JSON and diffs page count + per-status + per-group tallies against live Notion (no writes; no scratch DB). MUST exist before #7's wipe.
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
│   ├── orchestrator/            # pipeline (front-fold + mode dispatch) · sequence (modes+plan) · config_gate (first-time run-config gate) · calibrate_gate (interactive vocab lock) · runner · preflight · guardrails · status_report
│   ├── stages/                  # discover · ingest · select · extract · calibrate · enrich · deterministic · route
│   ├── engines/                 # transcript · ocr · vision  (extract plugins)
│   ├── backends/                # base · local_ollama · api_anthropic · claude_p · claude_code · cowork
│   ├── adapters/                # notion (state) · instagram/ (session·display·harvest·crawl·extractor·cookies)
│   ├── helpers/                 # observability (StageProgress, setup_logging) · tui (keyboard-select prompts over questionary)
│   ├── enrich_schema.py · reconcile.py · snapshots.py (crawl) · backup.py (Notion→JSON)
├── cli/isa.py                   # single entrypoint: isa discover|run|status|backup
├── config/                      # gitignored DATA: collections.json · tags.json · routes.json · run.json
├── prompts/                     # versioned enrich · calibrate · deterministic-title prompts
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
| `config/run.json` | yes (example) | mode · `enrich.backend/model/effort/api_mode` · top-level `output_language` · `deterministic.title_mode` · `extract.ocr.mode` · `batch.max_char_budget/max_image_tokens` · guardrail caps |
| `config/collections.json` | **no** (private) | ordered `groups` list (group names live here) + per-collection `{group, extract}` |
| `config/tags.json` | **no** (private) | per-group + cross-group vocab with definitions |
| `config/routes.json` | yes (example) | route map (optional; absent/empty ⇒ routing disabled) |

Interactive gates depend on **`questionary`** (a runtime dependency; pulls `prompt_toolkit`) for the
keyboard-select prompts — see `helpers/tui.py` and D27.

---

## 13. Design decision log

| # | Decision | Why | Rejected alternative |
|---|---|---|---|
| D1 | Two run modes (bulk vs incremental) | Bulk needs calibration + checkpoints; steady-state must be light | One mode for both (calibration overhead every run) |
| D2 | Fork: extract path vs deterministic branch | Thin/visual saves don't warrant transcription or an LLM | LLM-enrich everything (waste on no-content) |
| D3 | One-shot enrich (title+summary+externals+tags) | Claude already loads the content; extra fields are ~free; coherent labels | Separate passes (re-reads content N times) |
| D4 | Drop `Summarized` status | One-shot lands at `Tagged`; the intermediate stop no longer exists | Keep it (ghost status, friction) |
| D5 | Backend file-contract (local/api/claude-p/claude-code/cowork) | Subscription, API, and local must all work; user picks at start. Realized as the `Backend` protocol + `get_backend` registry in `backends.base`, with `Budgets`/`FillResult` typing the contract | Hardcode one engine; lose portability (Phase 6) |
| D6 | Cowork loop: state in Notion, not chat | Makes auto-compaction safe and the loop crash-resumable | State in conversation (breaks on compaction) |
| D7 | Per-backend dynamic batching | Optimal chunk differs per backend, expressed as `Budgets(char_budget, max_items, image_token_budget)`: `local` uncaps char/items and checkpoints per item; the others pass the run-config budgets through | One fixed batch size |
| D8 | Local LLM is **structurally** full-enrich-capable; title-grade is the PRACTICAL quality ceiling on this box | The `local` backend withholds no field structurally (a stronger local model can do full enrich — portability). Title-grade is a quality ceiling of THIS box's qwen2.5:7b, NOT an architectural restriction. **Spike-confirmed 2026-06-10:** constrained `format=` fixes JSON compliance, but qwen2.5:7b summaries drop the high-value specifics (numbers, benchmarks, names) and externals come back empty or hallucinated — semantic *quality*, not compliance, is the wall | Hard-cap `local` to title-only (would block a stronger local model) |
| D9 | Three-axis tags + per-group vocab | Granular within group, correct cross-tagging, content-type for search/downstream | Flat tag list (loses the kind-of-item axis) |
| D10 | Calibration gate (sample → propose → refine → lock) | Tag correction is expensive; front-load it onto a sample per group | Tag the whole group then correct (huge correction load) |
| D11 | Cross-group: union vocab, enrich at last extract group (calibrate by membership) | All vocabs locked before tagging; first-time-only concern | Earliest-order (tags before later vocabs exist) |
| D12 | Split `extract_version` / `enrich_version` | Independent reprocessing of extract vs enrich | One `processing_version` (conflated, ambiguous) |
| D13 | Keep `raw_extraction`, unversioned | Durable per-slice payload; re-parse without re-scraping | Drop it (lose reprocessing record) |
| D14 | Transcript: base int8 (CPU) + tuned params | On-device spike: small/distil gave ~identical content at 3–7× cost; tuned params are faster + as accurate | small/distil (cost, no gain); GPU (no cuBLAS) |
| D15 | OCR: RapidOCR for text baseline + **vision-at-enrich** for carousel/post slides (all slides, not escalate) | Live DB showed 0% of slides were flagged by the old confidence gate, and OCR confidence ≠ information capture. Images are effectively free on a Claude Max subscription session, so per-slide confidence gating has no value. Escalate path retired. | Escalate-to-Claude-vision at extract time (was D15 v1); OCR-only (misses graphic slides) |
| D16 | Routing deterministic + optional | Collection encodes destination; 100% reliable, no model | AI-chosen routing (cost, unreliable) |
| D17 | Fresh `insta_save/` + `legacy/` fallback | Clean v2 boundaries; v1 runnable during migration | In-place rewrite (risky, no fallback) |
| D18 | Backend drafts vocab (if it exposes `propose_vocab`), human refines + locks | Faster than authoring cold; human keeps control. Backends with `propose_vocab` (e.g. `claude-p`) auto-draft; others fall back to a manual write-proposed-json prompt. The calibrate gate is never part of `Backend.fill()` — it is always human-reviewed before lock | Pure-manual (slow) or pure-LLM (uncurated) |
| D19 | Guardrails + preflight + backup/restore | Bulk re-runs are high-blast-radius; fail fast, cap spend, be restorable — built as preflight (effort/notion/backend/engines), item-cap (pre-run vs remaining), per-batch spend-cap, usage reminder, backup + dry restore-check | Trust the run (silent overruns, unrecoverable writes) |
| D20 | Group-level ordering (not per-collection) | ~6 groups to order, not ~44 folders; group names move to the private config | Per-collection `order` (tedious; leaks group names in code) |
| D21 | Ingest metadata yt-dlp-first, browser fallback, wall guard | More 429-resilient; works on image posts; v1-proven | Browser-first (fragile, rate-limited) |
| D22 | Consolidate v1's two collection scrapers/mergers into one discover stage | v1 had divergent duplicates (`crawler.py` vs `list_collections.py`) | Keep both (maintenance trap) |
| D23 | Vision as an enrich input modality; two lanes per group (text/vision) | Carousel/post slides contain information that transcript/OCR alone can miss; feeding raw images into the enrich lane is simpler and more complete than a separate post-OCR vision pass. Reel-vision deferred: reel frames carry little unique information vs transcript + frame-OCR text, and a per-frame vision pass is not content-completeness-gated. | Single lane (would need complex image-text merge logic); per-frame confidence gate at extract time (wrong signal) |
| D24 | Sequencer: guided-resumable, state in Notion, 5-rule decision table | Re-reading Notion before each step makes the loop crash/compaction-safe with no in-memory/file state; sequencing is a pure function of DB state; stops at human/agent gates; a no-progress guard prevents infinite loops | State in memory (breaks on crash); single-pass planning (stale after each step) |
| D25 | `claude-p` automated backend (headless `claude -p`, Claude Max, default for the one-call orchestrator); the calibrate gate runs INLINE in both modes | Claude Max users have no API key; a headless subprocess gives `claude-code`-grade quality fully automated (`AUTOMATED=True`), vision-capable (reads slides by path), in-process with no relay. Running the calibrate gate inline (backend drafts via `propose_vocab` → human locks) makes `isa run --mode first-time` a true one-call pipeline (front-folds discover→ingest→select) instead of a multi-command manual sequence | `api`-only (needs a key); `claude-code` agent-filled loop (needs a driving session); incremental raising on uncalibrated groups (forces a separate first-time run) |
| D26 | First-time runs confirm run-config interactively (seed `claude-p` default `run.json`, inline backend/model/effort or `$EDITOR` via `--select-mode`, then confirm); incremental stays silent | A truly fresh user must not silently run on the wrong backend — the loader defaults `enrich.backend` to `local` (title-only here) and requires the file to exist. This implements the long-documented §5/D7 "confirm model+effort at run start" as a gate, mirroring the select/calibrate gates. Incremental stays unattended/cron-able | Silent defaults (wrong backend, crash on missing file); a full field-by-field wizard (YAGNI — only backend/model/effort matter, rest via `$EDITOR`) |
| D27 | Interactive gates use keyboard-select (questionary) via a shared `helpers/tui.py`: arrow-pick + per-option help + inline/`$EDITOR` mode-prompt + Proceed/Go back/Edit/Abort confirm (the collection gate adds a mid-loop "Edit the rest in $EDITOR" escape) | No-typing-by-default, self-documenting (per-option help), and consistent across the run-config and collection gates; tests monkeypatch the four `tui` primitives since questionary needs a TTY. The calibrate gate adopts the same helper next | Typed `input()` (error-prone, no discoverability); a full-screen prompt_toolkit form (YAGNI) |
| D28 | Calibrate is an interactive vocab editor (context → reject/add → merged preview → confirm), reachable inline and via `--stage calibrate --group G`; `merge_vocab` (pure) powers both the preview and the lock; interactive reject is current-group granular only, shared-axis removal routes to "Edit all ($EDITOR)" | Structured reject/add with a live preview beats blind `$EDITOR` JSON editing; one merge definition means preview and lock can't drift; the additive `lock_vocab` cannot remove a shared content-type/cross-group item, so cross-group surgery is deliberately editor-only | Raw-JSON-only editing (no preview, error-prone); interactive cross-group removal (would need a non-additive lock, risking clobber across groups); a live multi-pane vocab TUI (YAGNI) |

---

## 14. Out of scope / later
- **Phase 4 reorg** — audit/merge/rename collections once items are enriched; re-tag stale-vocab items via `enrich_version`.
- **Downstream route pipelines** — per-`route_target` extraction + writes to target DBs (separate project).
- **Vision API pricing** — moot on the current Claude Max subscription session (images are effectively free). Per-image token cost (`PER_SLIDE_IMAGE_TOKENS` ≈ 1600) matters only for a future `api`-backend vision path; confirm via the `claude-api` reference before budgeting that run.
- **Reel vision** — deferred; not content-completeness-gated (transcript + frame-OCR text is already adequate for reels). Revisit only if evidence shows reels carry meaningful on-screen information not captured by text extraction.
