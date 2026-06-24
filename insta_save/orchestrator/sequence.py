"""Guided resumable sequencer — pure plan computation (no execution).

Reads all Notion pages once, tallies per-group counts, then maps each group
to its next pipeline action according to the rule table in the module docstring.
The result is a Plan whose `next_action` the orchestrator runs next.

Rule table (first match wins, per group):
  1. queued[g] > 0                          → extract       (automated)
  2. enrichable[g] > 0, NOT calibrated      → calibrate     (human gate)
  3. enrichable[g] > 0, calibrated          → enrich        (automated iff backend.AUTOMATED)
  4. deterministic[g] > 0                   → deterministic (automated iff title_mode=template)
  5. routing_enabled AND tagged[g] > 0      → route         (automated)
  6. else                                   → done

Count semantics:
  queued[g]     — Queued items whose ANY collection maps to group g (membership)
  enrichable[g] — Extracted items whose enrich_group == g (cross-group: enriched at LAST group)
  tagged[g]     — Tagged items whose ANY collection maps to group g (membership)
"""

import logging
from dataclasses import dataclass, field

from rich.console import Console

from insta_save.adapters.notion import query_all_pages
from insta_save.helpers.observability import RULE_TOP, render_rule
from insta_save.orchestrator import run_control
from insta_save.orchestrator.calibrate_gate import run_calibrate_gate
from insta_save.stages import enrich as _enrich_stage
from insta_save.stages.extract import run_extract_stage
from insta_save.stages.route import run_route_stage

log = logging.getLogger(__name__)


@dataclass
class GroupStep:
    group: str
    action: str    # "extract" | "calibrate" | "enrich" | "deterministic" | "route" | "done"
    automated: bool  # True if the sequencer can run it now; False = human/agent gate
    detail: str    # human-facing one-liner
    count: int = 0  # for "enrich" steps: number of Extracted items to process (group total)


@dataclass
class Plan:
    steps: list         # list[GroupStep], one per group, in collections_cfg.groups order
    next_action: object # first GroupStep whose action != "done", or None
    done: bool          # True iff every step's action == "done"
    skipped: list = field(default_factory=list)  # [{group, action, reason}] groups skipped on no-progress


def _parse_page(page: dict) -> tuple[str | None, list[str]]:
    """Extract (status, collections) from a raw page dict, tolerantly."""
    props = page.get("properties", {})
    status_sel = props.get("status", {}).get("select")
    status = status_sel.get("name") if status_sel else None
    collections = [c["name"] for c in props.get("collection", {}).get("multi_select", [])]
    return status, collections


def _tally(pages: list[dict], collections_cfg) -> tuple[dict, dict, dict, dict]:
    """Single-pass tally: queued[g], enrichable[g], tagged[g], deterministic[g] for all groups.

    queued and tagged use group membership (any collection in the group).
    enrichable uses enrich_group (the LAST extract group — cross-group assignment).
    deterministic counts Imported items NOT on the extract path, bucketed by group membership.
    """
    queued: dict[str, int] = {}
    enrichable: dict[str, int] = {}
    tagged: dict[str, int] = {}
    deterministic: dict[str, int] = {}

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

        elif status == "Imported":
            if not collections_cfg.is_extract_path(collections):
                seen_d: set[str] = set()
                for c in collections:
                    g = collections_cfg.group_of(c)
                    if g not in seen_d:
                        seen_d.add(g)
                        deterministic[g] = deterministic.get(g, 0) + 1

    return queued, enrichable, tagged, deterministic


def _backend_name(backend) -> str:
    return getattr(backend, "NAME", None) or str(backend)


def _step_for_group(
    group: str,
    queued: dict,
    enrichable: dict,
    tagged: dict,
    deterministic: dict,
    vocab,
    backend,
    routing_enabled: bool,
    det_automated: bool = True,
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
            count=enrichable.get(group, 0),
        )

    if deterministic.get(group, 0) > 0:
        return GroupStep(
            group=group,
            action="deterministic",
            automated=det_automated,
            detail=f"tag {deterministic[group]} deterministic items",
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
    det_title_mode = getattr(run_cfg, "deterministic_title_mode", "template") if run_cfg is not None else "template"
    det_automated = det_title_mode == "template"

    pages = query_all_pages(env)
    queued, enrichable, tagged, deterministic = _tally(pages, collections_cfg)

    steps = [
        _step_for_group(group, queued, enrichable, tagged, deterministic,
                        vocab, backend, routing_enabled, det_automated)
        for group in collections_cfg.groups
    ]

    next_action = next((s for s in steps if s.action != "done"), None)
    done = next_action is None

    return Plan(steps=steps, next_action=next_action, done=done)


# ---------------------------------------------------------------------------
# Sequencer execution
# ---------------------------------------------------------------------------

_console = Console()


def _band(group, collections_cfg, *, done: bool) -> None:
    """Print a heavy ═ rule banding a group open (done=False) or closed (done=True)."""
    idx = list(collections_cfg.groups).index(group) + 1
    total = len(collections_cfg.groups)
    label = f"done · {group}" if done else f"group · {group}"
    _console.print(render_rule(label, width=RULE_TOP, char="═", index=(idx, total)))


def _next_unstuck(plan, stuck_groups):
    """First step that is neither done nor in a stuck group — None if none remain."""
    return next((s for s in plan.steps
                 if s.action != "done" and s.group not in stuck_groups), None)


def _run_loop(env, run_cfg, collections_cfg, vocab, backend, routes, *,
              progress_factory=None, interactive=False) -> Plan:
    """Inner loop shared by run_first_time and run_incremental.

    Repeatedly computes the plan, checks whether the next step can be executed
    automatically, and drives the appropriate stage drain until the plan is done
    or a gate (non-automated step) is reached.

    Args:
        interactive: when True, calibrate steps are run inline (the gate prompts
                     the user and reloads vocab before continuing). When False,
                     a calibrate step is returned as a gate stop. Agent-filled
                     enrich steps always return as a gate regardless of this flag.
    """
    executed: set[tuple[str, str]] = set()
    stuck_groups: set[str] = set()
    skipped: list[dict] = []
    current_group: str | None = None

    while True:
        run_control.checkpoint()
        plan = compute_plan(env, run_cfg, collections_cfg, vocab, backend, routes)

        step = _next_unstuck(plan, stuck_groups)
        if step is None:
            # Nothing left that isn't done or stuck.
            if current_group is not None:
                _band(current_group, collections_cfg, done=True)
            plan.skipped = skipped
            return plan

        # Open/close group bands on group transitions.
        if step.group != current_group:
            if current_group is not None:
                _band(current_group, collections_cfg, done=True)
            _band(step.group, collections_cfg, done=False)
            current_group = step.group

        if not step.automated:
            if step.action == "calibrate" and interactive:
                with run_control.gate():
                    vocab = run_calibrate_gate(env, run_cfg, collections_cfg=collections_cfg,
                                               backend=backend, group=step.group)
                continue  # re-plan with the now-calibrated vocab; band stays open
            # Agent-filled enrich (or non-interactive): return the plan as a gate.
            _band(current_group, collections_cfg, done=True)
            plan.skipped = skipped
            return plan

        key = (step.group, step.action)
        if key in executed:
            # This (group, action) already ran but state didn't advance. Skip the GROUP
            # and keep driving the others — one stuck group must not halt the whole run.
            log.warning(
                "sequencer: no progress for group=%r action=%r — skipping this group, "
                "continuing with the rest", step.group, step.action,
            )
            stuck_groups.add(step.group)
            skipped.append({"group": step.group, "action": step.action,
                            "reason": "stage ran but Notion state did not advance"})
            continue

        executed.add(key)

        _execute_step(env, run_cfg, collections_cfg, vocab, backend, routes,
                      step, progress_factory)


def _null_progress(label):
    """Minimal no-op progress context manager for use when progress_factory is None."""
    class _N:
        def __enter__(self): return None
        def __exit__(self, *a): return False
    return _N()


def _execute_step(env, run_cfg, collections_cfg, vocab, backend, routes,
                  step, progress_factory):
    """Dispatch one automated step to the appropriate stage drain."""
    pf = progress_factory or _null_progress

    if step.action == "extract":
        with pf(f"Extract · {step.group}") as progress:
            run_extract_stage(env, run_cfg.extract, progress,
                              group=step.group, collections_cfg=collections_cfg)

    elif step.action == "enrich":
        _enrich_stage.drain_enrich_group(
            env, run_cfg, collections_cfg, vocab, backend, step.group,
            progress_factory=progress_factory,
            group_total=step.count,
        )

    elif step.action == "deterministic":
        from insta_save.stages.deterministic import run_deterministic_stage
        with pf(f"Deterministic · {step.group}") as progress:
            run_deterministic_stage(env, collections_cfg, progress, group=step.group)

    elif step.action == "route":
        write_delay = getattr(env, "notion_write_delay", 0.0)
        with pf(f"Route · {step.group}") as progress:
            run_route_stage(env, routes, collections_cfg, progress,
                            group=step.group, write_delay=write_delay)


def run_first_time(env, run_cfg, collections_cfg, vocab, backend, routes, *,
                   progress_factory=None, dry_run=False) -> Plan:
    """Run the pipeline in first-time mode (calibrate is a human gate, not an error).

    Executes automated steps — extract, enrich (automated backend), route — in the
    order the plan prescribes, recomputing from Notion state after each step. Stops
    when the plan is done, a non-automated step (calibrate, or agent-filled enrich)
    is reached, or the no-progress guard fires.

    Args:
        env:             EnvConfig.
        run_cfg:         RunConfig.
        collections_cfg: CollectionsConfig.
        vocab:           Vocab.
        backend:         Backend module.
        routes:          Routes.
        progress_factory: Optional ``(label: str) -> context manager``.
        dry_run:         If True, compute and return the plan without executing anything.

    Returns:
        The final Plan (done, or stopped at a gate / no-progress).
    """
    if dry_run:
        return compute_plan(env, run_cfg, collections_cfg, vocab, backend, routes)
    return _run_loop(env, run_cfg, collections_cfg, vocab, backend, routes,
                     progress_factory=progress_factory, interactive=True)


def run_incremental(env, run_cfg, collections_cfg, vocab, backend, routes, *,
                    progress_factory=None, dry_run=False) -> Plan:
    """Run the pipeline in incremental mode (delta only, reuses existing snapshots).

    Like run_first_time but processes only items that have not yet been processed
    (delta run). If a group requires calibration (new collection added since the
    last run), the gate is run interactively inline — the same behavior as
    first-time mode.

    Args:
        env:             EnvConfig.
        run_cfg:         RunConfig.
        collections_cfg: CollectionsConfig.
        vocab:           Vocab.
        backend:         Backend module.
        routes:          Routes.
        progress_factory: Optional ``(label: str) -> context manager``.
        dry_run:         If True, compute and return the plan without executing anything.

    Returns:
        The final Plan (done, or stopped at an agent-filled enrich gate / no-progress).
    """
    if dry_run:
        return compute_plan(env, run_cfg, collections_cfg, vocab, backend, routes)
    return _run_loop(env, run_cfg, collections_cfg, vocab, backend, routes,
                     progress_factory=progress_factory, interactive=True)
