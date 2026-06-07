# Tasks

## Active Plan

`/home/ag-95/.claude/plans/2026-06-07-rename-pipeline.md`
Branch: `feature-phase3-enrichment-runs` — closing for PR

---

## Branch closure — PENDING COMMITS

### Cluster J — pipeline code ⏳
Files: `pipeline/config.py`, `pipeline/extractor_deep.py`, `scripts/title.py`, `scripts/summarize.py`
Message: `feat: add post OCR extraction, rate limit delay, and title scope fix`

### Cluster K — promote.py rename + docs ⏳
Files: `scripts/promote.py`, `README.md`, `.claude/docs/PROJECT.md`, `.claude/docs/IMPLEMENTATION_PLAN.md`
Message: `fix: rename queue.py to promote.py to avoid shadowing stdlib queue module`

### Cluster L — work docs ⏳
Files: `.claude/work/session.md`, `.claude/work/tasks.md`
Message: `chore: finalize work docs for branch closure`

---

## Phase 3 Enrichment — COMPLETE ✅

All commits landed on `feature-phase3-enrichment-runs`.

| Commit | Summary |
|---|---|
| 1775259 | Priority-bucketed stage runner |
| de2a2ed | Extraction through shared runner |
| f212810 | Local enrichment through shared runner |
| 981fc2c | Summarize by priority bucket |
| ada84d6 | Docs: runner model |
| 30e2be0 | Fix type detection + no_data counter |
| aa4eaec | Externals grouped by category |
| bb6a04b | Ollama JSON schema format |
| 8ae2f4e | Drop key_insights; dynamic content batching |
| 61735f7 | Title only from local pass |
| 474fdf5 | Claude pass: summary + externals |
| 89721f1 | Script renames + title pass decoupling |
| 461ec71 | Module renames |
| cde96c7 | Field + status renames |
| 83f14e3 | Summary paragraph breaks |
| 0081f6d | Docs update |
| 0556640 | Work docs |
| c5b9ca1 | Remove transcript_available; README + stale refs |
| J–L | Pending |

---

## O-Runs (ongoing — tracked in session.md)

| Run | Status |
|---|---|
| Title — Extracted + Imported | ✅ done |
| Summarize — set 1 (High/Med/Low) | ✅ done |
| Extract — set 2 (all Queued, overnight) | 🔄 running |
| Summarize — set 2 | ⏳ daytime cycles |
| Spot-check O4 | ⏳ after all Summarized |

---

## Backlog — next branches (post-merge)

### Immediate (clear off before new phases)
- [ ] T-refactor: delete dead code (`pipeline/enrich_claude.py`, `scripts/run_enrichment.py`)
- [ ] T-orchestrator: single full-pipeline CLI for incremental add/remove cycles

### Phase 4 — Collection reorganisation
- [ ] Audit all 43 collections against enriched content (duplicates, near-duplicates, too-broad, too-narrow)
- [ ] Produce target collection list: merges, renames, retirements
- [ ] Bulk-migrate Notion `collection` tags on affected pages
- [ ] Update `config/collections.json` — groups, extract flags
- [ ] Trigger: Phase 3 complete + spot-checked first

### Phase 5 — Downstream processing
- [ ] `route_target` assigned from collection config (deterministic, not AI)
- [ ] Collection-typed Claude prompts: recipe → recipe-shaped extraction, market research → brand/opportunity extraction
- [ ] Write to downstream Notion DBs per route_target
- [ ] `tags` via embedding clusters across all `summary` values (batch job, post bulk-summarize)
- [ ] Trigger: Phase 4 complete + 50+ Summarized items + routing targets confirmed
