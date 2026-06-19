"""Tests for orchestrator.sequence: compute_plan decision logic and sequencer execution.

All tests monkeypatch query_all_pages (or compute_plan) so no Notion call is made.
"""

from types import SimpleNamespace

import pytest

from insta_save.config.collections import CollectionsConfig
from insta_save.config.routes import Routes
from insta_save.orchestrator import sequence
from insta_save.stages import enrich as enrich_stage
from insta_save.stages.extract import run_extract_stage
from insta_save.stages.route import run_route_stage


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _collections_cfg(*group_names):
    """Build a CollectionsConfig with one extract=True collection per group
    named after the group (e.g. group "Hustling" -> collection "Hustling")."""
    groups = tuple(group_names)
    collections = {
        g: {"group": g, "extract": True, "slug": g.lower(), "numeric_id": str(i)}
        for i, g in enumerate(group_names)
    }
    return CollectionsConfig(groups=groups, collections=collections)


def _fake_vocab(*calibrated_groups):
    """Vocab stub: has_group returns True iff the group is in calibrated_groups."""
    calibrated = set(calibrated_groups)
    return SimpleNamespace(has_group=lambda g: g in calibrated)


def _backend(automated=True, name="test-backend"):
    return SimpleNamespace(AUTOMATED=automated, NAME=name, VISION_CAPABLE=False)


def _page(status, collections):
    """Build a minimal raw page dict as returned by query_all_pages."""
    return {
        "page_id": "p-" + "-".join(collections),
        "properties": {
            "status": {"select": {"name": status}},
            "collection": {"multi_select": [{"name": c} for c in collections]},
        },
    }


def _patch(monkeypatch, pages):
    monkeypatch.setattr(sequence, "query_all_pages", lambda env: pages)


# ---------------------------------------------------------------------------
# extract-first: Queued items → "extract" step, automated=True
# ---------------------------------------------------------------------------

def test_extract_first(monkeypatch):
    cfg = _collections_cfg("Hustling", "Biz", "uncategorized")
    _patch(monkeypatch, [_page("Queued", ["Hustling"])])
    vocab = _fake_vocab("Hustling")
    backend = _backend(automated=True)
    routes = Routes()

    plan = sequence.compute_plan(None, None, cfg, vocab, backend, routes)

    hustling_step = plan.steps[0]
    assert hustling_step.group == "Hustling"
    assert hustling_step.action == "extract"
    assert hustling_step.automated is True
    assert "Queued" in hustling_step.detail


# ---------------------------------------------------------------------------
# calibrate gate: enrichable but not calibrated → "calibrate", automated=False
# ---------------------------------------------------------------------------

def test_calibrate_gate_when_uncalibrated(monkeypatch):
    cfg = _collections_cfg("Hustling", "Biz", "uncategorized")
    _patch(monkeypatch, [_page("Extracted", ["Hustling"])])
    vocab = _fake_vocab()  # no groups calibrated
    backend = _backend(automated=True)
    routes = Routes()

    plan = sequence.compute_plan(None, None, cfg, vocab, backend, routes)

    hustling_step = plan.steps[0]
    assert hustling_step.action == "calibrate"
    assert hustling_step.automated is False
    assert plan.next_action is hustling_step


# ---------------------------------------------------------------------------
# enrich step: calibrated + enrichable → "enrich"; automated follows backend
# ---------------------------------------------------------------------------

def test_enrich_automated_backend(monkeypatch):
    cfg = _collections_cfg("Hustling", "uncategorized")
    _patch(monkeypatch, [_page("Extracted", ["Hustling"])])
    vocab = _fake_vocab("Hustling")
    backend = _backend(automated=True, name="api")
    routes = Routes()

    plan = sequence.compute_plan(None, None, cfg, vocab, backend, routes)

    step = plan.steps[0]
    assert step.action == "enrich"
    assert step.automated is True
    assert "api" in step.detail


def test_enrich_agent_filled_backend(monkeypatch):
    cfg = _collections_cfg("Hustling", "uncategorized")
    _patch(monkeypatch, [_page("Extracted", ["Hustling"])])
    vocab = _fake_vocab("Hustling")
    backend = _backend(automated=False, name="claude-code")
    routes = Routes()

    plan = sequence.compute_plan(None, None, cfg, vocab, backend, routes)

    step = plan.steps[0]
    assert step.action == "enrich"
    assert step.automated is False


# ---------------------------------------------------------------------------
# route: Tagged + routing enabled → "route" automated=True
#        routing disabled (empty Routes) → "done"
# ---------------------------------------------------------------------------

def test_route_when_routing_enabled(monkeypatch):
    cfg = _collections_cfg("Hustling", "uncategorized")
    _patch(monkeypatch, [_page("Tagged", ["Hustling"])])
    vocab = _fake_vocab("Hustling")
    backend = _backend()
    routes = Routes(by_group={"Hustling": "Notion DB"})

    plan = sequence.compute_plan(None, None, cfg, vocab, backend, routes)

    step = plan.steps[0]
    assert step.action == "route"
    assert step.automated is True
    assert "Tagged" in step.detail


def test_route_skipped_when_routing_disabled(monkeypatch):
    cfg = _collections_cfg("Hustling", "uncategorized")
    _patch(monkeypatch, [_page("Tagged", ["Hustling"])])
    vocab = _fake_vocab("Hustling")
    backend = _backend()
    routes = Routes()  # all dicts empty → routing_enabled=False

    plan = sequence.compute_plan(None, None, cfg, vocab, backend, routes)

    step = plan.steps[0]
    assert step.action == "done"


# ---------------------------------------------------------------------------
# cross-group: Extracted item whose enrich_group==G counted in enrichable[G]
#              not in enrichable[F] (F < G in order)
# ---------------------------------------------------------------------------

def test_cross_group_enrichable_counted_at_last_group(monkeypatch):
    """An item in both group F and group G (G later in order, extract=True)
    contributes to enrichable[G] only, not enrichable[F]."""
    cfg = CollectionsConfig(
        groups=("First", "Last", "uncategorized"),
        collections={
            "ColF": {"group": "First", "extract": False, "slug": "colf", "numeric_id": "1"},
            "ColG": {"group": "Last",  "extract": True,  "slug": "colg", "numeric_id": "2"},
        },
    )
    # Item belongs to both ColF and ColG; enrich_group = Last (the last extract group)
    _patch(monkeypatch, [_page("Extracted", ["ColF", "ColG"])])
    vocab = _fake_vocab("Last")
    backend = _backend(automated=True)
    routes = Routes()

    plan = sequence.compute_plan(None, None, cfg, vocab, backend, routes)

    first_step = next(s for s in plan.steps if s.group == "First")
    last_step  = next(s for s in plan.steps if s.group == "Last")

    # First group has no enrichable items from this page
    assert first_step.action == "done"
    # Last group sees the enrichable item and proceeds to enrich
    assert last_step.action == "enrich"


# ---------------------------------------------------------------------------
# done flag: all groups "done" → plan.done True, next_action None
# ---------------------------------------------------------------------------

def test_plan_done_when_all_groups_done(monkeypatch):
    cfg = _collections_cfg("Hustling", "uncategorized")
    _patch(monkeypatch, [])  # empty DB
    vocab = _fake_vocab()
    backend = _backend()
    routes = Routes()

    plan = sequence.compute_plan(None, None, cfg, vocab, backend, routes)

    assert plan.done is True
    assert plan.next_action is None
    assert all(s.action == "done" for s in plan.steps)


# ---------------------------------------------------------------------------
# ordering: next_action is the FIRST non-done group in collections_cfg.groups order
# ---------------------------------------------------------------------------

def test_next_action_is_first_non_done_group(monkeypatch):
    cfg = _collections_cfg("Alpha", "Beta", "Gamma", "uncategorized")
    # Only Gamma has Queued items; Alpha and Beta are empty
    _patch(monkeypatch, [_page("Queued", ["Gamma"])])
    vocab = _fake_vocab()
    backend = _backend()
    routes = Routes()

    plan = sequence.compute_plan(None, None, cfg, vocab, backend, routes)

    # Alpha and Beta are "done"; Gamma is "extract"
    assert plan.steps[0].action == "done"  # Alpha
    assert plan.steps[1].action == "done"  # Beta
    assert plan.steps[2].action == "extract"  # Gamma
    # next_action is Gamma (first non-done in order)
    assert plan.next_action is plan.steps[2]
    assert plan.next_action.group == "Gamma"


# ---------------------------------------------------------------------------
# Tolerant parsing: pages with missing/malformed properties don't crash
# ---------------------------------------------------------------------------

def test_tolerant_parsing_missing_status(monkeypatch):
    cfg = _collections_cfg("Hustling", "uncategorized")
    # Page with no status property at all
    bad_page = {"page_id": "p1", "properties": {}}
    _patch(monkeypatch, [bad_page])
    vocab = _fake_vocab()
    backend = _backend()
    routes = Routes()

    plan = sequence.compute_plan(None, None, cfg, vocab, backend, routes)
    # Should not raise; all groups end up "done"
    assert plan.done is True


# ---------------------------------------------------------------------------
# routing_enabled detection: any non-empty dict enables routing
# ---------------------------------------------------------------------------

def test_routing_enabled_by_tag(monkeypatch):
    cfg = _collections_cfg("Hustling", "uncategorized")
    _patch(monkeypatch, [_page("Tagged", ["Hustling"])])
    vocab = _fake_vocab()
    backend = _backend()
    routes = Routes(by_tag={"tips-hacks": "Some DB"})

    plan = sequence.compute_plan(None, None, cfg, vocab, backend, routes)

    step = plan.steps[0]
    assert step.action == "route"


def test_routing_enabled_by_collection(monkeypatch):
    cfg = _collections_cfg("Hustling", "uncategorized")
    _patch(monkeypatch, [_page("Tagged", ["Hustling"])])
    vocab = _fake_vocab()
    backend = _backend()
    routes = Routes(by_collection={"Hustling": "Some DB"})

    plan = sequence.compute_plan(None, None, cfg, vocab, backend, routes)

    step = plan.steps[0]
    assert step.action == "route"


# ===========================================================================
# Sequencer execution — run_first_time / run_incremental
# ===========================================================================

# Helper: build a Plan directly (avoid Notion I/O)
def _plan(steps, next_idx=None):
    """Build a Plan from a list of GroupStep dicts.

    next_idx: index of next_action (first non-done step); None = pick automatically.
    """
    step_objs = [
        sequence.GroupStep(group=s["group"], action=s["action"],
                           automated=s.get("automated", True), detail=s.get("detail", ""))
        for s in steps
    ]
    if next_idx is not None:
        next_action = step_objs[next_idx]
    else:
        next_action = next((s for s in step_objs if s.action != "done"), None)
    done = next_action is None
    return sequence.Plan(steps=step_objs, next_action=next_action, done=done)


def _done_plan():
    return _plan([{"group": "G", "action": "done"}])


def _patch_stages(monkeypatch):
    """Replace stage drains with call-recording stubs. Returns the calls dict."""
    calls = {"extract": [], "enrich": [], "route": []}

    monkeypatch.setattr(sequence, "run_extract_stage",
                        lambda env, ex, progress, **kw: calls["extract"].append(kw))
    monkeypatch.setattr(
        sequence._enrich_stage, "drain_enrich_group",
        lambda env, run_cfg, cols, vocab, backend, group, **kw: calls["enrich"].append(group),
    )
    monkeypatch.setattr(sequence, "run_route_stage",
                        lambda env, routes, cols, progress, **kw: calls["route"].append(kw))
    return calls


# ---------------------------------------------------------------------------
# dry_run: returns the plan immediately, no stages called
# ---------------------------------------------------------------------------

def test_dry_run_returns_plan_without_executing(monkeypatch):
    cfg = _collections_cfg("G", "uncategorized")
    _patch(monkeypatch, [_page("Queued", ["G"])])
    calls = _patch_stages(monkeypatch)
    vocab = _fake_vocab()
    backend = _backend(automated=True)
    routes = Routes()

    plan = sequence.run_first_time(None, SimpleNamespace(extract=None, output_language="english"),
                                   cfg, vocab, backend, routes, dry_run=True)

    assert plan.next_action is not None
    assert plan.next_action.action == "extract"
    assert calls == {"extract": [], "enrich": [], "route": []}


# ---------------------------------------------------------------------------
# Automated chain: extract → enrich → route → done
# ---------------------------------------------------------------------------

def test_automated_chain_drives_stages_in_order(monkeypatch):
    """A sequence of plans driving extract→enrich→route causes all three stages to run."""
    cfg = _collections_cfg("G", "uncategorized")
    vocab = _fake_vocab("G")
    backend = _backend(automated=True)
    routes = Routes()

    # Sequence of plans returned by successive compute_plan calls
    plan_seq = [
        _plan([{"group": "G", "action": "extract", "automated": True}]),
        _plan([{"group": "G", "action": "enrich",  "automated": True}]),
        _plan([{"group": "G", "action": "route",   "automated": True}]),
        _done_plan(),
    ]
    seq_iter = iter(plan_seq)
    monkeypatch.setattr(sequence, "compute_plan", lambda *a, **k: next(seq_iter))
    calls = _patch_stages(monkeypatch)

    run_cfg = SimpleNamespace(extract=None, output_language="english",
                               enrich=SimpleNamespace(model="m"))
    result = sequence.run_first_time(None, run_cfg, cfg, vocab, backend, routes)

    assert result.done is True
    assert len(calls["extract"]) == 1
    assert calls["enrich"] == ["G"]
    assert len(calls["route"]) == 1


# ---------------------------------------------------------------------------
# Calibrate gate inline: first-time runs the gate then re-plans to done
# ---------------------------------------------------------------------------

def test_loop_runs_calibrate_gate_then_continues(monkeypatch):
    cfg = _collections_cfg("G", "uncategorized")
    vocab = _fake_vocab()  # starts uncalibrated
    backend = _backend(automated=True)
    routes = Routes()
    plan_seq = iter([
        _plan([{"group": "G", "action": "calibrate", "automated": False}]),
        _done_plan(),
    ])
    monkeypatch.setattr(sequence, "compute_plan", lambda *a, **k: next(plan_seq))
    calls = _patch_stages(monkeypatch)
    gate_calls = {"n": 0}
    def fake_gate(env, run_cfg, *, collections_cfg, backend, group):
        gate_calls["n"] += 1
        return _fake_vocab("G")  # now calibrated
    monkeypatch.setattr(sequence, "run_calibrate_gate", fake_gate)
    run_cfg = SimpleNamespace(extract=None, output_language="english",
                              enrich=SimpleNamespace(model="m"))
    result = sequence.run_first_time(None, run_cfg, cfg, vocab, backend, routes)
    assert gate_calls["n"] == 1
    assert result.done is True
    assert calls == {"extract": [], "enrich": [], "route": []}


# ---------------------------------------------------------------------------
# Agent-filled enrich gate: automated=False enrich → returns plan, no drain called
# ---------------------------------------------------------------------------

def test_agent_filled_enrich_gate_stops(monkeypatch):
    cfg = _collections_cfg("G", "uncategorized")
    vocab = _fake_vocab("G")
    backend = _backend(automated=False, name="claude-code")
    routes = Routes()

    gate_plan = _plan([{"group": "G", "action": "enrich", "automated": False}])
    monkeypatch.setattr(sequence, "compute_plan", lambda *a, **k: gate_plan)
    calls = _patch_stages(monkeypatch)

    run_cfg = SimpleNamespace(extract=None, output_language="english",
                               enrich=SimpleNamespace(model="m"))
    result = sequence.run_first_time(None, run_cfg, cfg, vocab, backend, routes)

    assert result is gate_plan
    assert calls["enrich"] == []


# ---------------------------------------------------------------------------
# No-progress guard: same (group, action) returned twice → bail after one execution
# ---------------------------------------------------------------------------

def test_no_progress_guard_bails_after_one_execution(monkeypatch):
    """When compute_plan keeps returning the same automated step (stage made no progress),
    the sequencer must run the step ONCE, then return without looping forever."""
    cfg = _collections_cfg("G", "uncategorized")
    vocab = _fake_vocab("G")
    backend = _backend(automated=True)
    routes = Routes()

    stuck_plan = _plan([{"group": "G", "action": "extract", "automated": True}])
    monkeypatch.setattr(sequence, "compute_plan", lambda *a, **k: stuck_plan)
    calls = _patch_stages(monkeypatch)

    run_cfg = SimpleNamespace(extract=None, output_language="english",
                               enrich=SimpleNamespace(model="m"))
    # Must return (not loop forever)
    result = sequence.run_first_time(None, run_cfg, cfg, vocab, backend, routes)

    assert result is stuck_plan
    # Stage was called exactly once
    assert len(calls["extract"]) == 1
    assert calls["enrich"] == []


# ---------------------------------------------------------------------------
# incremental: calibrate → runs gate inline (same as first-time)
# ---------------------------------------------------------------------------

def test_incremental_calibrate_runs_gate(monkeypatch):
    cfg = _collections_cfg("G", "uncategorized")
    vocab = _fake_vocab()  # uncalibrated
    backend = _backend(automated=True)
    routes = Routes()

    plan_seq = iter([
        _plan([{"group": "G", "action": "calibrate", "automated": False}]),
        _done_plan(),
    ])
    monkeypatch.setattr(sequence, "compute_plan", lambda *a, **k: next(plan_seq))
    calls = _patch_stages(monkeypatch)
    gate_calls = {"n": 0}
    def fake_gate(env, run_cfg, *, collections_cfg, backend, group):
        gate_calls["n"] += 1
        return _fake_vocab("G")
    monkeypatch.setattr(sequence, "run_calibrate_gate", fake_gate)

    run_cfg = SimpleNamespace(extract=None, output_language="english",
                               enrich=SimpleNamespace(model="m"))
    result = sequence.run_incremental(None, run_cfg, cfg, vocab, backend, routes)
    assert gate_calls["n"] == 1
    assert result.done is True
    assert calls == {"extract": [], "enrich": [], "route": []}


def test_incremental_automated_chain_works(monkeypatch):
    """Incremental mode runs the same automated chain as first-time when no calibrate needed."""
    cfg = _collections_cfg("G", "uncategorized")
    vocab = _fake_vocab("G")
    backend = _backend(automated=True)
    routes = Routes()

    plan_seq = [
        _plan([{"group": "G", "action": "enrich", "automated": True}]),
        _done_plan(),
    ]
    seq_iter = iter(plan_seq)
    monkeypatch.setattr(sequence, "compute_plan", lambda *a, **k: next(seq_iter))
    calls = _patch_stages(monkeypatch)

    run_cfg = SimpleNamespace(extract=None, output_language="english",
                               enrich=SimpleNamespace(model="m"))
    result = sequence.run_incremental(None, run_cfg, cfg, vocab, backend, routes)

    assert result.done is True
    assert calls["enrich"] == ["G"]


def test_incremental_dry_run(monkeypatch):
    cfg = _collections_cfg("G", "uncategorized")
    _patch(monkeypatch, [_page("Extracted", ["G"])])
    calls = _patch_stages(monkeypatch)
    vocab = _fake_vocab("G")
    backend = _backend(automated=True)
    routes = Routes()

    run_cfg = SimpleNamespace(extract=None, output_language="english",
                               enrich=SimpleNamespace(model="m"))
    plan = sequence.run_incremental(None, run_cfg, cfg, vocab, backend, routes, dry_run=True)

    assert plan.next_action is not None
    assert calls == {"extract": [], "enrich": [], "route": []}


# ---------------------------------------------------------------------------
# _tally: Imported items NOT on extract path count in deterministic[g]
# ---------------------------------------------------------------------------

def test_tally_counts_deterministic_pending(monkeypatch):
    from insta_save.orchestrator import sequence
    cfg = type("C", (), {
        "groups": ["Lifestyle"],
        "group_of": lambda self, c: "Lifestyle",
        "is_extract_path": lambda self, cols: False,   # deterministic branch
        "enrich_group": lambda self, cols: None,
    })()
    pages = [{"status": "Imported", "collections": ["BLR"]},
             {"status": "Imported", "collections": ["BLR"]}]
    monkeypatch.setattr(sequence, "_parse_page",
                        lambda p: (p["status"], p["collections"]))
    queued, enrichable, tagged, deterministic = sequence._tally(pages, cfg)
    assert deterministic == {"Lifestyle": 2}
    assert queued == {} and enrichable == {} and tagged == {}
