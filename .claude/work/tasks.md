# Tasks

Plan: `/home/ag-95/.claude/plans/2026-06-04-batch-ingest-extract-enrich.md`

## Batch Ingest + Phase 3 Enrichment

### Part A — Collections Registry + Batch Ingestion

- [ ] A1: `collections_config.py` — all 43 collections, group taxonomy, pilot flags, `ordered_for_ingestion()`, `pilot_collections()`, `classify_new_collection()`
- [ ] A2: `ingest.py` refactor (extract `ingest_with_context`) + `ingest_batch.py` (group-priority batch runner, `--start-from-group`, `--dry-run`)
- [ ] A3: [Operational] Run `ingest_batch.py` on all 43 collections; verify re-run = 0 creates

### Part B — Queue + Extract Pilot Set

- [ ] B1: `queue_pilot.py` + `notion.py` additions (`mark_queued`, `query_by_collection_and_status`)
- [ ] B2: [Operational] `queue_pilot.py --all-pilot` → `run_extraction.py` → verify all pilot items Expanded

### Part C — Phase 3 Enrichment Build

- [ ] C1: `config.py` + `requirements.txt` — add `anthropic_api_key`, `enrichment_model`, `enrichment_version`; install `anthropic`
- [ ] C2: `prompts/enrichment_v1.txt` — prompt template
- [ ] C3: `enrichment.py` — `validate_enrichment_config`, `enrich_item` (tool_use, structured output)
- [ ] C4: `notion.py` additions — `get_page_content`, `write_enrichment`
- [ ] C5: `run_enrichment.py` — CLI (`--limit`, `--source_id`, `--dry-run`, `--force`)
- [ ] C6: [Operational] Dry-run 3 items → live 5 items → reprocess test → full pilot batch

---

## Headless Default + Headed Auto-Setup — COMPLETE

- [x] T1: `display.py` — `ensure_display()`: detect Windows IP, probe port 6001, auto-launch VcXsrv via cmd.exe
- [x] T2: `session.py` — `headless=True` default on `_launch_browser` + `ensure_authenticated`; auto-relaunch headed on cookie expiry
- [x] T3: CLI flags — `--headed` on `ingest.py`, `run_extraction.py`, `crawler.py`, `session.py` `__main__`
- [x] T4: Verification — headless session check passes; `--help` flags confirmed on all CLIs

---

## Phase 2 — Deep Extraction — COMPLETE

- [x] V1: Transcript engine bake-off — faster-whisper base int8 locked
- [x] V2: OCR engine bake-off — RapidOCR locked
- [x] V3: Carousel mechanics — DOM stepping confirmed
- [x] T1–T9: All build tasks complete; phase frozen

---

## Phase 1 — COMPLETE

- [x] T1–T10: All tasks complete
