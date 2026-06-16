"""Guided resumable sequencer — pure plan computation (no execution).

Reads all Notion pages once, tallies per-group counts, then maps each group
to its next pipeline action according to the rule table in the module docstring.
The result is a Plan whose `next_action` the orchestrator runs next.

Rule table (first match wins, per group):
  1. queued[g] > 0                          → extract   (automated)
  2. enrichable[g] > 0, NOT calibrated      → calibrate (human gate)
  3. enrichable[g] > 0, calibrated          → enrich    (automated iff backend.AUTOMATED)
  4. routing_enabled AND tagged[g] > 0      → route     (automated)
  5. else                                   → done

Count semantics:
  queued[g]     — Queued items whose ANY collection maps to group g (membership)
  enrichable[g] — Extracted items whose enrich_group == g (cross-group: enriched at LAST group)
  tagged[g]     — Tagged items whose ANY collection maps to group g (membership)
"""

from dataclasses import dataclass

from insta_save.adapters.notion import query_all_pages


@dataclass
class GroupStep:
    group: str
    action: str    # "extract" | "calibrate" | "enrich" | "route" | "done"
    automated: bool  # True if the sequencer can run it now; False = human/agent gate
    detail: str    # human-facing one-liner


@dataclass
class Plan:
    steps: list         # list[GroupStep], one per group, in collections_cfg.groups order
    next_action: object # first GroupStep whose action != "done", or None
    done: bool          # True iff every step's action == "done"


def _parse_page(page: dict) -> tuple[str | None, list[str]]:
    """Extract (status, collections) from a raw page dict, tolerantly."""
    props = page.get("properties", {})
    status_sel = props.get("status", {}).get("select")
    status = status_sel.get("name") if status_sel else None
    collections = [c["name"] for c in props.get("collection", {}).get("multi_select", [])]
    return status, collections


def _tally(pages: list[dict], collections_cfg) -> tuple[dict, dict, dict]:
    """Single-pass tally: queued[g], enrichable[g], tagged[g] for all groups.

    queued and tagged use group membership (any collection in the group).
    enrichable uses enrich_group (the LAST extract group — cross-group assignment).
    """
    queued: dict[str, int] = {}
    enrichable: dict[str, int] = {}
    tagged: dict[str, int] = {}

    for page in pages:
        status, collections = _parse_page(page)
        if status is None:
            continue

        if status == "Queued":
            seen_groups: set[str] = set()
            for c in collections:
                g = collections_cfg.group_of(c)
                if g not in seen_groups:
                    seen_groups.add(g)
                    queued[g] = queued.get(g, 0) + 1

        elif status == "Extracted":
            eg = collections_cfg.enrich_group(collections)
            if eg is not None:
                enrichable[eg] = enrichable.get(eg, 0) + 1

        elif status == "Tagged":
            seen_groups_t: set[str] = set()
            for c in collections:
                g = collections_cfg.group_of(c)
                if g not in seen_groups_t:
                    seen_groups_t.add(g)
                    tagged[g] = tagged.get(g, 0) + 1

    return queued, enrichable, tagged


def _backend_name(backend) -> str:
    return getattr(backend, "NAME", None) or str(backend)


def _step_for_group(
    group: str,
    queued: dict,
    enrichable: dict,
    tagged: dict,
    vocab,
    backend,
    routing_enabled: bool,
) -> GroupStep:
    """Apply the rule table and return the GroupStep for one group."""
    name = _backend_name(backend)

    if queued.get(group, 0) > 0:
        return GroupStep(
            group=group,
            action="extract",
            automated=True,
            detail=f"drain extract: {queued[group]} Queued",
        )

    if enrichable.get(group, 0) > 0 and not vocab.has_group(group):
        return GroupStep(
            group=group,
            action="calibrate",
            automated=False,
            detail=f"calibrate vocab for {group} ({enrichable[group]} items waiting to enrich)",
        )

    if enrichable.get(group, 0) > 0:
        return GroupStep(
            group=group,
            action="enrich",
            automated=backend.AUTOMATED,
            detail=f"enrich {enrichable[group]} items via {name}",
        )

    if routing_enabled and tagged.get(group, 0) > 0:
        return GroupStep(
            group=group,
            action="route",
            automated=True,
            detail=f"route {tagged[group]} Tagged",
        )

    return GroupStep(
        group=group,
        action="done",
        automated=True,
        detail="nothing pending",
    )


def compute_plan(env, run_cfg, collections_cfg, vocab, backend, routes) -> Plan:
    """Read all Notion pages once, classify each item, and return the Plan.

    Args:
        env:             EnvConfig (passed to query_all_pages).
        run_cfg:         RunConfig (accepted for forward-compat; not deeply used here).
        collections_cfg: CollectionsConfig with .groups ordering + group_of/enrich_group.
        vocab:           Vocab with .has_group(group) -> bool.
        backend:         backend module with .AUTOMATED: bool and .NAME: str.
        routes:          Routes with .by_tag/.by_collection/.by_group dicts.

    Returns:
        Plan with per-group steps in collections_cfg.groups order, next_action, and done flag.
    """
    routing_enabled = bool(routes.by_tag or routes.by_collection or routes.by_group)

    pages = query_all_pages(env)
    queued, enrichable, tagged = _tally(pages, collections_cfg)

    steps = [
        _step_for_group(group, queued, enrichable, tagged, vocab, backend, routing_enabled)
        for group in collections_cfg.groups
    ]

    next_action = next((s for s in steps if s.action != "done"), None)
    done = next_action is None

    return Plan(steps=steps, next_action=next_action, done=done)
