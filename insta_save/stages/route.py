"""Route stage (st.6) — Tagged -> Routed. Deterministic, no model: route_target is
resolved from config/routes.json (primary topic > tag > collection > group).
Missing/empty routes.json => routing disabled (every item stays Tagged).
summary/externals untouched."""

import logging

from insta_save.adapters.notion import get_item_status, write_route, write_unroute
from insta_save.orchestrator.runner import run_priority_stage

log = logging.getLogger(__name__)


def _decision_source(routes, tags, collections, groups, primary_topic, target) -> str:
    """Classify how an already-resolved `target` (routes.route_for, primary topic
    included) was decided: "primary" only when the item's primary topic actually
    changed the destination from what plain by_tag/collection/group order alone would
    have produced; "order" otherwise — including a primary topic with a rule that
    happens to agree with the order-only answer (review fix round 1)."""
    order_target = routes.route_for(tags, collections, groups)
    return "primary" if target != order_target else "order"


def _route_item(env, item, routes, collections_cfg, *, dry_run=False, decided=None) -> str:
    tags, collections = item.get("tags", []), item.get("collections", [])
    groups = [collections_cfg.group_of(c) for c in collections]
    primary_topic = item.get("primary_topic")
    target = routes.route_for(tags, collections, groups, primary_topic=primary_topic)
    if target is None:
        return "unrouted"
    if not dry_run:
        write_route(env, item["page_id"], target)
    if decided is not None:
        by = _decision_source(routes, tags, collections, groups, primary_topic, target)
        decided[by] = decided.get(by, 0) + 1
    return "routed"


def run_route_stage(env, routes, collections_cfg, progress, *, limit=None, group=None,
                    dry_run=False, write_delay: float = 0.0) -> dict:
    """Drive routing over Tagged items. Counters: routed / unrouted / failed, plus
    "decided_by": {"primary": n, "order": n} — how many of the routed items' targets
    were actually changed by the item's primary topic (differs from the order-only
    answer) versus decided by plain by_tag/collection/group order (review fix round 1;
    same semantics as run_reroute_stage's breakdown).

    dry_run: when True, no Notion writes are made and write_delay is forced to 0
    (there is nothing to throttle when nothing is written)."""
    effective_delay = 0.0 if dry_run else write_delay
    decided: dict = {"primary": 0, "order": 0}
    counts = run_priority_stage(
        env, "Tagged",
        lambda e, it, ctx: _route_item(e, it, routes, collections_cfg, dry_run=dry_run,
                                       decided=decided),
        progress, limit=limit, group=group, collections_cfg=collections_cfg,
        stage_key="route", bar_label="Route (Tagged)",
        write_delay=effective_delay, delay_on={"routed"})
    counts["decided_by"] = decided
    return counts


def _reroute_item(env, item, routes, collections_cfg, moves: dict, *, decided=None,
                  dry_run=False) -> str:
    """Re-evaluate one already-Routed item's target against the current routes config.

    `moves` is a shared (old_target, new_target) -> count accumulator the caller collects
    across the run — new_target=None means the item lost its route and went back to Tagged.
    `decided` is the companion "primary"/"order" -> count accumulator: "primary" only when
    the item's primary topic actually changed the NEW target from what plain rule order
    alone would have produced (see _decision_source); "order" otherwise, including a
    primary topic whose rule happens to agree with the order-only answer (review fix
    round 1). Both only ever grow for an item that actually moves, and both are optional
    so the caller can preview a decision without recording it. run_priority_stage's
    process_fn contract only returns a single counter name, so this detail rides along on
    the side channels instead.

    "unchanged" only applies when the recomputed target matches a REAL current target —
    an item stuck in Routed with route_target already None must still be sent back to
    Tagged if it still resolves to no route, or it would stay stuck there forever with
    distribute never seeing it."""
    tags, collections = item.get("tags", []), item.get("collections", [])
    groups = [collections_cfg.group_of(c) for c in collections]
    primary_topic = item.get("primary_topic")
    target = routes.route_for(tags, collections, groups, primary_topic=primary_topic)
    current = item.get("route_target")
    if target == current and current is not None:
        return "unchanged"
    if target is None:
        if not dry_run:
            write_unroute(env, item["page_id"])
        moves[(current, None)] = moves.get((current, None), 0) + 1
        return "unrouted"
    if not dry_run:
        write_route(env, item["page_id"], target)
    moves[(current, target)] = moves.get((current, target), 0) + 1
    if decided is not None:
        by = _decision_source(routes, tags, collections, groups, primary_topic, target)
        decided[by] = decided.get(by, 0) + 1
    return "rerouted"


def _reroute_item_guarded(env, item, routes, collections_cfg, moves: dict, *, decided=None,
                          dry_run=False) -> str:
    """The per-item function run_reroute_stage actually drives: same decision as
    _reroute_item, plus a stale-read guard around the real write.

    Notion's query index can lag a status change made elsewhere (e.g. a distribute apply
    settling the same item mid-run), so a write must not blindly trust the item list
    run_reroute_stage read at the start of the run. dry-run never writes, so it delegates
    straight through with no extra read. A real run previews the decision first (a
    throwaway moves dict and no `decided` — nothing is recorded, and no read happens, for
    an "unchanged" outcome); when the preview says a write is coming, the item's live
    status is re-read, and the write is skipped — counted "skipped_stale", no move and no
    decision recorded — if the item has already left Routed."""
    if dry_run:
        return _reroute_item(env, item, routes, collections_cfg, moves, decided=decided,
                             dry_run=True)
    preview = _reroute_item(env, item, routes, collections_cfg, {}, dry_run=True)
    if preview == "unchanged":
        return "unchanged"
    if get_item_status(env, item["page_id"]) != "Routed":
        return "skipped_stale"
    return _reroute_item(env, item, routes, collections_cfg, moves, decided=decided,
                         dry_run=False)


def run_reroute_stage(env, routes, collections_cfg, progress, *, limit=None, group=None,
                      dry_run=False, write_delay: float = 0.0) -> dict:
    """Re-evaluate already-Routed items against routes.json (never Distributed/Misrouted —
    their content already landed on a page; rebuild handles those). Counters: unchanged /
    rerouted / unrouted / skipped_stale / failed, plus "moves": {(old_target, new_target):
    count} with new_target=None for items that lost their route and returned to Tagged, and
    "decided_by": {"primary": n, "order": n} — how many of the rerouted items' NEW targets
    were actually changed by the item's primary topic (differs from the order-only answer)
    versus decided by plain by_tag/collection/group order (review fix round 1).

    dry_run: no Notion writes; write_delay is forced to 0 (nothing to throttle)."""
    effective_delay = 0.0 if dry_run else write_delay
    moves: dict = {}
    decided: dict = {"primary": 0, "order": 0}
    counts = run_priority_stage(
        env, "Routed",
        lambda e, it, ctx: _reroute_item_guarded(e, it, routes, collections_cfg, moves,
                                                 decided=decided, dry_run=dry_run),
        progress, limit=limit, group=group, collections_cfg=collections_cfg,
        stage_key="reroute", bar_label="Reroute (Routed)",
        write_delay=effective_delay, delay_on={"rerouted", "unrouted"})
    counts["moves"] = moves
    counts["decided_by"] = decided
    return counts
