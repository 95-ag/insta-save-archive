# insta_save/backends/cowork.py
"""cowork enrich backend — agent-filled (like claude-code) + a durable, resumable
status loop (D6).

cowork shares claude-code's file contract and prompt assembly; fill() is external
(a Cowork session, not Python, reads prompt.txt + slide images and writes
results.json). What cowork adds is durability: ALL state lives in Notion + tmp/
files, never in conversation, so the loop survives auto-compaction or a crash and
resumes from wherever Notion's per-item status left off.

The one-kickoff loop the driving Cowork session runs:
    status(group)  -> remaining count from Notion
    while remaining:
        prepare      (CLI writes batch.json + prompt.txt for the next undone slice)
        fill         (the Cowork session reads prompt.txt, writes results.json)
        apply        (CLI writes Notion -> Tagged; drained items leave the query)
        status(group)
Because prepare/apply are idempotent and Notion status is the source of truth,
re-running after any interruption picks up the next undone batch — no chat state."""

import logging

from insta_save.backends.base import Budgets, FillResult

log = logging.getLogger(__name__)

NAME = "cowork"; AUTOMATED = False; VISION_CAPABLE = True

# Statuses still eligible for enrichment (mirrors enrich.prepare's default input).
ENRICHABLE_STATUSES = ("Extracted",)


def batch_budgets(run_cfg) -> Budgets:
    return Budgets(char_budget=run_cfg.char_budget, max_items=run_cfg.max_items,
                   image_token_budget=run_cfg.image_token_budget)


def fill(env, run_cfg, enrich_dir) -> FillResult:
    """Agent-filled: a Cowork session reads prompt.txt (+ slide images) and writes
    results.json. No in-process work."""
    return FillResult(external=True)


def _enrichable_stubs(env, collections_cfg, group):
    """Notion stubs still enrichable for the group, in priority order. Reuses
    enrich's group-stub iterator so the count matches what prepare would batch."""
    from insta_save.stages.enrich import _ordered_group_stubs
    return list(_ordered_group_stubs(env, ENRICHABLE_STATUSES, group, collections_cfg))


def status(env, collections_cfg, group) -> int:
    """Count of items still enrichable for the group (0 == drained). The cowork
    loop's stop condition; resumable because it reads live Notion status."""
    return len(_enrichable_stubs(env, collections_cfg, group))
