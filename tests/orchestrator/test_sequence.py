"""Tests for orchestrator.sequence: compute_plan decision logic.

All tests monkeypatch query_all_pages so no Notion call is made.
"""

from types import SimpleNamespace

import pytest

from insta_save.config.collections import CollectionsConfig
from insta_save.config.routes import Routes
from insta_save.orchestrator import sequence


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
    return SimpleNamespace(AUTOMATED=automated, NAME=name)


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
