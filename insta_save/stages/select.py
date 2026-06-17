"""Select stage (st.2) — Imported → Queued (extract path) or left Imported (deterministic
branch #2, not built yet). Branch is pure config: is_extract_path over the item's
collections. Reuses the shared priority-bucketed runner."""

import logging

from insta_save.adapters import notion
from insta_save.orchestrator.runner import run_priority_stage

log = logging.getLogger(__name__)


def _select_item(env, collections_cfg, item) -> str:
    """Promote extract-path items to Queued; leave others Imported for the deterministic
    branch. Returns a counter name."""
    if collections_cfg.is_extract_path(item.get("collections", [])):
        notion.mark_queued(env, item["page_id"])
        return "queued"
    return "deterministic_pending"


def run_select_stage(env, collections_cfg, progress, *, limit=None, group=None,
                     write_delay: float = 0.0) -> dict:
    """Drive selection over Imported items in priority order."""
    def _process(env_, item, _ctx):
        return _select_item(env_, collections_cfg, item)

    return run_priority_stage(
        env, "Imported", _process, progress,
        limit=limit, group=group, collections_cfg=collections_cfg,
        stage_key="select", bar_label="Select (Imported)",
        write_delay=write_delay, delay_on={"queued"})
