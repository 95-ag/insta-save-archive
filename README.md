# Insta-Save

Turn your Instagram saves into a searchable, structured knowledge base in Notion.

Insta-Save ingests your saved posts, extracts what they actually contain (speech →
transcript, on-screen/slide text → OCR), and uses an LLM to enrich each item with a
**title, summary, externals, and semantic tags** — then optionally routes high-value
items to downstream systems. The goal: never need to re-open the original post.

> **Status:** v2 is in design/build. The working **v1** currently runs from `pipeline/` +
> `scripts/`; its operating manual is preserved at [`legacy/README.md`](legacy/README.md), and
> the code moves under `legacy/` during migration. This README describes the **v2 target**.
> Full design + decision log: [`.claude/docs/ARCHITECTURE.md`](.claude/docs/ARCHITECTURE.md).

---

## Pipeline

```
discover → ingest → select ─┬─ extract → calibrate* → enrich ─┐
                            │  (transcript+OCR)   (title+summary    ├─► Tagged → route* → handoff
                            └─ deterministic tag ───────────  +externals+tags)
   Imported  →  Queued  →  Extracted ─────────────────────────────► Tagged → Routed
```
`*` calibrate runs per-group on first-time runs only; route is optional.

**Two run modes**
- **First-time (bulk):** processes the whole archive group-by-group, with a per-group
  tag-vocabulary calibration step so tagging is accurate before the full run.
- **Incremental:** processes only new saves, reusing locked vocabularies.

## The enrich brain is pluggable

Choose how LLM enrichment runs — set once in config; the pipeline works with any:

| Backend | What it is | Automated | Cost |
|---|---|---|---|
| `local` | Ollama (qwen2.5) on your machine | yes | free |
| `api` | Anthropic API | yes | per-token |
| `claude-code` | a Claude Code session | semi | subscription |
| `cowork` | a self-paced Claude Cowork loop | semi | subscription |

Extraction (transcript/OCR) always runs locally; OCR can escalate hard slides to Claude vision.

## Quickstart *(v2 target interface)*

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. Configure (see docs/OPERATING.md)
cp .env.example .env                       # Notion token, IG username
cp config/run.example.json config/run.json # backend, ordering, caps

# 3. Discover collections + set group / order / extract (interactive, first time)
isa discover

# 4. First-time bulk run (group-by-group, with calibration)
isa run --mode first-time

# 5. Later — pick up new saves
isa run --mode incremental

# Anytime
isa status     # per-group counts, failures, what's left
isa backup     # snapshot Notion to JSON
```

## Docs

- **[Operating guide](docs/OPERATING.md)** — configuration reference + run recipes. Start here to operate it.
- **[Architecture](.claude/docs/ARCHITECTURE.md)** — full design, stages, backends, schema, decision log.

## Requirements

- Python ≥ 3.12; a Notion workspace + integration token; an Instagram account.
- Local extraction: `ffmpeg` (+ a GPU helps transcription; CPU works, slower).
- `local` enrich: [Ollama](https://ollama.com) with `qwen2.5:7b`. `api` enrich: an Anthropic API key.

## Repo layout *(v2 target)*

```
insta_save/   v2 package — stages · engines · backends · adapters · orchestrator · config
cli/          the `isa` entrypoint
config/       your private config (gitignored): collections.json · tags.json · run.json
legacy/       frozen v1 — fallback during migration
docs/         operating guide;  .claude/docs/ holds architecture (+ archived v1 docs)
```

Private data (`config/collections.json`, `config/tags.json`, `.env`, `session_cookies.json`)
is gitignored and never committed.
