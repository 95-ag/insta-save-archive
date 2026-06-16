"""Route stage (st.6) — Tagged -> Routed. Deterministic, no model: route_target is
resolved from config/routes.json (tag > collection > group). Missing/empty routes.json
=> routing disabled (every item stays Tagged). summary/externals untouched."""

import logging

from insta_save.adapters.notion import write_route
from insta_save.orchestrator.runner import run_priority_stage

log = logging.getLogger(__name__)


def _route_item(env, item, routes, collections_cfg, *, dry_run=False) -> str:
    groups = [collections_cfg.group_of(c) for c in item.get("collections", [])]
    target = routes.route_for(item.get("tags", []), item.get("collections", []), groups)
    if target is None:
        return "unrouted"
    if not dry_run:
        write_route(env, item["page_id"], target)
    return "routed"


def run_route_stage(env, routes, collections_cfg, progress, *, limit=None, group=None,
                    dry_run=False, write_delay: float = 0.0) -> dict:
    """Drive routing over Tagged items. Counters: routed / unrouted / failed.

    dry_run: when True, no Notion writes are made and write_delay is forced to 0
    (there is nothing to throttle when nothing is written)."""
    effective_delay = 0.0 if dry_run else write_delay
    return run_priority_stage(
        env, "Tagged",
        lambda e, it, ctx: _route_item(e, it, routes, collections_cfg, dry_run=dry_run),
        progress, limit=limit, group=group, collections_cfg=collections_cfg,
        stage_key="route", bar_label="Route (Tagged)",
        write_delay=effective_delay, delay_on={"routed"})
