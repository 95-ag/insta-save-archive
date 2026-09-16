import insta_save.stages.route as route
from insta_save.config.routes import Routes


class _Cfg:
    def group_of(self, name): return {"a-coll": "Biz"}.get(name, "uncategorized")


def _item(pid="p1", tags=None, collections=None):
    return {"page_id": pid, "source_id": pid, "tags": tags or [], "collections": collections or []}


def test_route_item_routes_on_tag(monkeypatch):
    written = {}
    monkeypatch.setattr(route, "write_route", lambda env, page_id, target: written.update({page_id: target}))
    routes = Routes(by_tag=[("tool", "ToolsDB")])
    assert route._route_item(None, _item(tags=["tool"]), routes, _Cfg()) == "routed"
    assert written == {"p1": "ToolsDB"}


def test_route_item_unrouted_when_no_mapping(monkeypatch):
    monkeypatch.setattr(route, "write_route", lambda *a: (_ for _ in ()).throw(AssertionError("should not write")))
    assert route._route_item(None, _item(tags=["x"]), Routes(), _Cfg()) == "unrouted"


def test_route_item_dry_run_does_not_write(monkeypatch):
    monkeypatch.setattr(route, "write_route", lambda *a: (_ for _ in ()).throw(AssertionError("dry-run wrote")))
    assert route._route_item(None, _item(tags=["tool"]), Routes(by_tag=[("tool", "T")]), _Cfg(), dry_run=True) == "routed"


def test_route_item_routes_on_collection(monkeypatch):
    written = {}
    monkeypatch.setattr(route, "write_route", lambda env, page_id, target: written.update({page_id: target}))
    routes = Routes(by_collection={"a-coll": "CollDB"})
    assert route._route_item(None, _item(collections=["a-coll"]), routes, _Cfg()) == "routed"
    assert written == {"p1": "CollDB"}


def test_route_item_routes_on_group(monkeypatch):
    written = {}
    monkeypatch.setattr(route, "write_route", lambda env, page_id, target: written.update({page_id: target}))
    routes = Routes(by_group={"Biz": "BizDB"})
    # "a-coll" maps to "Biz" via _Cfg.group_of
    assert route._route_item(None, _item(collections=["a-coll"]), routes, _Cfg()) == "routed"
    assert written == {"p1": "BizDB"}


def test_route_item_tag_wins_over_collection(monkeypatch):
    written = {}
    monkeypatch.setattr(route, "write_route", lambda env, page_id, target: written.update({page_id: target}))
    routes = Routes(by_tag=[("tool", "TagDB")], by_collection={"a-coll": "CollDB"})
    assert route._route_item(None, _item(tags=["tool"], collections=["a-coll"]), routes, _Cfg()) == "routed"
    assert written == {"p1": "TagDB"}


# ---------------------------------------------------------------------------
# Item 3: dry_run=True forces write_delay=0 regardless of the caller's value
# ---------------------------------------------------------------------------

def test_run_route_stage_dry_run_forces_write_delay_zero(monkeypatch):
    """When dry_run=True, run_route_stage must pass write_delay=0 to run_priority_stage
    regardless of the write_delay argument supplied by the caller."""
    captured = {}

    def _fake_runner(env, status, fn, progress, *, limit=None, group=None,
                     collections_cfg=None, stage_key=None, bar_label=None,
                     write_delay=0.0, delay_on=None):
        captured["write_delay"] = write_delay
        return {}

    monkeypatch.setattr(route, "run_priority_stage", _fake_runner)
    route.run_route_stage(None, Routes(), _Cfg(), None,
                          dry_run=True, write_delay=0.5)
    assert captured["write_delay"] == 0


def test_run_route_stage_non_dry_passes_write_delay(monkeypatch):
    """When dry_run=False, run_route_stage forwards the write_delay argument unchanged."""
    captured = {}

    def _fake_runner(env, status, fn, progress, *, limit=None, group=None,
                     collections_cfg=None, stage_key=None, bar_label=None,
                     write_delay=0.0, delay_on=None):
        captured["write_delay"] = write_delay
        return {}

    monkeypatch.setattr(route, "run_priority_stage", _fake_runner)
    route.run_route_stage(None, Routes(), _Cfg(), None,
                          dry_run=False, write_delay=0.7)
    assert captured["write_delay"] == 0.7


# ---------------------------------------------------------------------------
# Task 5: re-evaluate already-Routed items against the current routes
# ---------------------------------------------------------------------------

def _routed_item(pid="p1", tags=None, collections=None, route_target="Old"):
    return {"page_id": pid, "source_id": pid, "tags": tags or [], "collections": collections or [],
            "route_target": route_target}


def test_reroute_item_unchanged_when_target_matches(monkeypatch):
    monkeypatch.setattr(route, "write_route", lambda *a: (_ for _ in ()).throw(AssertionError("should not write")))
    monkeypatch.setattr(route, "write_unroute", lambda *a: (_ for _ in ()).throw(AssertionError("should not write")))
    routes = Routes(by_tag=[("tool", "ToolsDB")])
    moves = {}
    item = _routed_item(tags=["tool"], route_target="ToolsDB")
    assert route._reroute_item(None, item, routes, _Cfg(), moves) == "unchanged"
    assert moves == {}


def test_reroute_item_rerouted_on_new_target(monkeypatch):
    written = {}
    monkeypatch.setattr(route, "write_route", lambda env, page_id, target: written.update({page_id: target}))
    routes = Routes(by_tag=[("tool", "NewDB")])
    moves = {}
    item = _routed_item(tags=["tool"], route_target="OldDB")
    assert route._reroute_item(None, item, routes, _Cfg(), moves) == "rerouted"
    assert written == {"p1": "NewDB"}
    assert moves == {("OldDB", "NewDB"): 1}


def test_reroute_item_unrouted_when_no_mapping_writes_unroute(monkeypatch):
    unrouted = {}
    monkeypatch.setattr(route, "write_route", lambda *a: (_ for _ in ()).throw(AssertionError("should not route")))
    monkeypatch.setattr(route, "write_unroute", lambda env, page_id: unrouted.update({page_id: True}))
    routes = Routes()  # no mapping at all
    moves = {}
    item = _routed_item(tags=["x"], route_target="OldDB")
    assert route._reroute_item(None, item, routes, _Cfg(), moves) == "unrouted"
    assert unrouted == {"p1": True}
    assert moves == {("OldDB", None): 1}


def test_reroute_item_none_target_and_none_current_still_unroutes(monkeypatch):
    """A Routed item stuck with route_target already None must not be 'unchanged' forever
    — if it still resolves to no route, it goes back to Tagged so distribute isn't
    perpetually blind to it."""
    unrouted = {}
    monkeypatch.setattr(route, "write_route", lambda *a: (_ for _ in ()).throw(AssertionError("should not route")))
    monkeypatch.setattr(route, "write_unroute", lambda env, page_id: unrouted.update({page_id: True}))
    moves = {}
    item = _routed_item(tags=["x"], route_target=None)
    assert route._reroute_item(None, item, Routes(), _Cfg(), moves) == "unrouted"
    assert unrouted == {"p1": True}
    assert moves == {(None, None): 1}


def test_reroute_item_dry_run_does_not_write(monkeypatch):
    monkeypatch.setattr(route, "write_route", lambda *a: (_ for _ in ()).throw(AssertionError("dry-run wrote")))
    monkeypatch.setattr(route, "write_unroute", lambda *a: (_ for _ in ()).throw(AssertionError("dry-run wrote")))
    routes = Routes(by_tag=[("tool", "NewDB")])
    moves = {}
    item = _routed_item(tags=["tool"], route_target="OldDB")
    assert route._reroute_item(None, item, routes, _Cfg(), moves, dry_run=True) == "rerouted"
    assert moves == {("OldDB", "NewDB"): 1}


def test_reroute_item_counts_repeated_moves(monkeypatch):
    monkeypatch.setattr(route, "write_route", lambda *a: None)
    routes = Routes(by_tag=[("tool", "NewDB")])
    moves = {}
    route._reroute_item(None, _routed_item(pid="p1", tags=["tool"], route_target="OldDB"), routes, _Cfg(), moves)
    route._reroute_item(None, _routed_item(pid="p2", tags=["tool"], route_target="OldDB"), routes, _Cfg(), moves)
    assert moves == {("OldDB", "NewDB"): 2}


# ---------------------------------------------------------------------------
# R5 fix 3: stale-read guard around the real write
# ---------------------------------------------------------------------------

def test_reroute_item_guarded_dry_run_makes_no_retrieve_call(monkeypatch):
    monkeypatch.setattr(route, "get_item_status",
                        lambda *a: (_ for _ in ()).throw(AssertionError("dry-run read status")))
    monkeypatch.setattr(route, "write_route", lambda *a: (_ for _ in ()).throw(AssertionError("dry-run wrote")))
    routes = Routes(by_tag=[("tool", "NewDB")])
    moves = {}
    item = _routed_item(tags=["tool"], route_target="OldDB")
    assert route._reroute_item_guarded(None, item, routes, _Cfg(), moves, dry_run=True) == "rerouted"
    assert moves == {("OldDB", "NewDB"): 1}


def test_reroute_item_guarded_unchanged_makes_no_retrieve_call(monkeypatch):
    monkeypatch.setattr(route, "get_item_status",
                        lambda *a: (_ for _ in ()).throw(AssertionError("unchanged read status")))
    routes = Routes(by_tag=[("tool", "ToolsDB")])
    moves = {}
    item = _routed_item(tags=["tool"], route_target="ToolsDB")
    assert route._reroute_item_guarded(None, item, routes, _Cfg(), moves) == "unchanged"
    assert moves == {}


def test_reroute_item_guarded_skips_stale_candidate_with_no_write(monkeypatch):
    """A candidate whose live status has already moved past Routed (e.g. distribute
    settled it) is skipped — no write, no move recorded."""
    monkeypatch.setattr(route, "get_item_status", lambda env, page_id: "Distributed")
    monkeypatch.setattr(route, "write_route", lambda *a: (_ for _ in ()).throw(AssertionError("should not write")))
    monkeypatch.setattr(route, "write_unroute", lambda *a: (_ for _ in ()).throw(AssertionError("should not write")))
    routes = Routes(by_tag=[("tool", "NewDB")])
    moves = {}
    item = _routed_item(tags=["tool"], route_target="OldDB")
    assert route._reroute_item_guarded(None, item, routes, _Cfg(), moves) == "skipped_stale"
    assert moves == {}


def test_reroute_item_guarded_writes_when_still_routed(monkeypatch):
    written = {}
    monkeypatch.setattr(route, "get_item_status", lambda env, page_id: "Routed")
    monkeypatch.setattr(route, "write_route", lambda env, page_id, target: written.update({page_id: target}))
    routes = Routes(by_tag=[("tool", "NewDB")])
    moves = {}
    item = _routed_item(tags=["tool"], route_target="OldDB")
    assert route._reroute_item_guarded(None, item, routes, _Cfg(), moves) == "rerouted"
    assert written == {"p1": "NewDB"}
    assert moves == {("OldDB", "NewDB"): 1}


def test_run_reroute_stage_reads_routed_status(monkeypatch):
    """run_reroute_stage must read the Routed status, never Tagged/Distributed/Misrouted."""
    captured = {}

    def _fake_runner(env, status, fn, progress, *, limit=None, group=None,
                     collections_cfg=None, stage_key=None, bar_label=None,
                     write_delay=0.0, delay_on=None):
        captured["status"] = status
        return {}

    monkeypatch.setattr(route, "run_priority_stage", _fake_runner)
    route.run_reroute_stage(None, Routes(), _Cfg(), None)
    assert captured["status"] == "Routed"


def test_run_reroute_stage_dry_run_forces_write_delay_zero(monkeypatch):
    captured = {}

    def _fake_runner(env, status, fn, progress, *, limit=None, group=None,
                     collections_cfg=None, stage_key=None, bar_label=None,
                     write_delay=0.0, delay_on=None):
        captured["write_delay"] = write_delay
        return {}

    monkeypatch.setattr(route, "run_priority_stage", _fake_runner)
    route.run_reroute_stage(None, Routes(), _Cfg(), None, dry_run=True, write_delay=0.5)
    assert captured["write_delay"] == 0


def test_run_reroute_stage_non_dry_passes_write_delay(monkeypatch):
    captured = {}

    def _fake_runner(env, status, fn, progress, *, limit=None, group=None,
                     collections_cfg=None, stage_key=None, bar_label=None,
                     write_delay=0.0, delay_on=None):
        captured["write_delay"] = write_delay
        return {}

    monkeypatch.setattr(route, "run_priority_stage", _fake_runner)
    route.run_reroute_stage(None, Routes(), _Cfg(), None, dry_run=False, write_delay=0.7)
    assert captured["write_delay"] == 0.7


def test_run_reroute_stage_returns_counts_and_moves(monkeypatch):
    """run_reroute_stage drives process_fn over the fake items and aggregates the
    per-item counters + moves the runner never sees itself."""
    routes = Routes(by_tag=[("tool", "NewDB")])
    items = [
        _routed_item(pid="p1", tags=["tool"], route_target="OldDB"),   # rerouted OldDB->NewDB
        _routed_item(pid="p2", tags=["tool"], route_target="NewDB"),   # unchanged
        _routed_item(pid="p3", tags=[], route_target="OldDB"),         # unrouted OldDB->None
    ]

    def _fake_runner(env, status, fn, progress, *, limit=None, group=None,
                     collections_cfg=None, stage_key=None, bar_label=None,
                     write_delay=0.0, delay_on=None):
        counts = {}
        for it in items:
            counter = fn(env, it, None)
            counts[counter] = counts.get(counter, 0) + 1
        counts.setdefault("failed", 0)
        return counts

    monkeypatch.setattr(route, "run_priority_stage", _fake_runner)
    monkeypatch.setattr(route, "write_route", lambda *a: None)
    monkeypatch.setattr(route, "write_unroute", lambda *a: None)
    # run_reroute_stage now drives _reroute_item_guarded, which re-reads live status
    # before a real write (R5 fix 3) — every item here is still genuinely Routed.
    monkeypatch.setattr(route, "get_item_status", lambda env, page_id: "Routed")

    r = route.run_reroute_stage(None, routes, _Cfg(), None)
    assert r["rerouted"] == 1 and r["unchanged"] == 1 and r["unrouted"] == 1 and r["failed"] == 0
    assert r["moves"] == {("OldDB", "NewDB"): 1, ("OldDB", None): 1}


# ---------------------------------------------------------------------------
# P4: the item's primary topic decides the target when it has a by_tag rule
# ---------------------------------------------------------------------------

_P4_ROUTES = Routes(by_tag=[("web-dev", "WebDB"), ("ui-ux", "UiDB")])


def test_route_item_routes_on_primary_topic_over_a_higher_ranked_tag(monkeypatch):
    written = {}
    monkeypatch.setattr(route, "write_route", lambda env, page_id, target: written.update({page_id: target}))
    item = _item(tags=["web-dev", "ui-ux"])
    item["primary_topic"] = "ui-ux"
    assert route._route_item(None, item, _P4_ROUTES, _Cfg()) == "routed"
    assert written == {"p1": "UiDB"}


def test_route_item_falls_back_to_tag_order_without_a_primary_topic(monkeypatch):
    written = {}
    monkeypatch.setattr(route, "write_route", lambda env, page_id, target: written.update({page_id: target}))
    assert route._route_item(None, _item(tags=["web-dev", "ui-ux"]), _P4_ROUTES, _Cfg()) == "routed"
    assert written == {"p1": "WebDB"}


def test_reroute_item_moves_an_item_its_primary_topic_points_elsewhere(monkeypatch):
    written = {}
    monkeypatch.setattr(route, "write_route", lambda env, page_id, target: written.update({page_id: target}))
    moves, decided = {}, {}
    item = _routed_item(tags=["web-dev", "ui-ux"], route_target="WebDB")
    item["primary_topic"] = "ui-ux"
    assert route._reroute_item(None, item, _P4_ROUTES, _Cfg(), moves, decided=decided) == "rerouted"
    assert written == {"p1": "UiDB"}
    assert moves == {("WebDB", "UiDB"): 1}
    assert decided == {"primary": 1}


def test_reroute_item_records_an_order_decision_without_a_primary_rule(monkeypatch):
    monkeypatch.setattr(route, "write_route", lambda *a: None)
    moves, decided = {}, {}
    item = _routed_item(tags=["web-dev"], route_target="OldDB")
    item["primary_topic"] = "unmapped-topic"
    assert route._reroute_item(None, item, _P4_ROUTES, _Cfg(), moves, decided=decided) == "rerouted"
    assert moves == {("OldDB", "WebDB"): 1}
    assert decided == {"order": 1}


def test_reroute_item_records_a_primary_decision_even_when_tag_order_agrees(monkeypatch):
    """The primary topic decided it, so it is reported as such — even though plain tag
    order would have picked the same target."""
    monkeypatch.setattr(route, "write_route", lambda *a: None)
    moves, decided = {}, {}
    item = _routed_item(tags=["web-dev"], route_target="OldDB")
    item["primary_topic"] = "web-dev"
    assert route._reroute_item(None, item, _P4_ROUTES, _Cfg(), moves, decided=decided) == "rerouted"
    assert decided == {"primary": 1}


def test_reroute_item_records_no_decision_for_unchanged_or_unrouted(monkeypatch):
    monkeypatch.setattr(route, "write_unroute", lambda *a: None)
    decided = {}
    unchanged = _routed_item(pid="p1", tags=["web-dev"], route_target="WebDB")
    assert route._reroute_item(None, unchanged, _P4_ROUTES, _Cfg(), {}, decided=decided) == "unchanged"
    lost = _routed_item(pid="p2", tags=["x"], route_target="WebDB")
    assert route._reroute_item(None, lost, _P4_ROUTES, _Cfg(), {}, decided=decided) == "unrouted"
    assert decided == {}


def test_reroute_item_dry_run_records_the_decision_without_writing(monkeypatch):
    monkeypatch.setattr(route, "write_route", lambda *a: (_ for _ in ()).throw(AssertionError("dry-run wrote")))
    moves, decided = {}, {}
    item = _routed_item(tags=["web-dev", "ui-ux"], route_target="WebDB")
    item["primary_topic"] = "ui-ux"
    assert route._reroute_item(None, item, _P4_ROUTES, _Cfg(), moves,
                               decided=decided, dry_run=True) == "rerouted"
    assert moves == {("WebDB", "UiDB"): 1} and decided == {"primary": 1}


def test_reroute_item_guarded_does_not_record_a_decision_for_a_stale_skip(monkeypatch):
    """The guard previews the decision before re-reading the live status — that preview
    must not leak into the reported counts when the write is then skipped."""
    monkeypatch.setattr(route, "get_item_status", lambda env, page_id: "Distributed")
    monkeypatch.setattr(route, "write_route", lambda *a: (_ for _ in ()).throw(AssertionError("should not write")))
    moves, decided = {}, {}
    item = _routed_item(tags=["web-dev"], route_target="OldDB")
    item["primary_topic"] = "web-dev"
    assert route._reroute_item_guarded(None, item, _P4_ROUTES, _Cfg(), moves,
                                       decided=decided) == "skipped_stale"
    assert moves == {} and decided == {}


def test_reroute_item_guarded_records_the_decision_once_when_it_writes(monkeypatch):
    monkeypatch.setattr(route, "get_item_status", lambda env, page_id: "Routed")
    monkeypatch.setattr(route, "write_route", lambda *a: None)
    moves, decided = {}, {}
    item = _routed_item(tags=["web-dev", "ui-ux"], route_target="WebDB")
    item["primary_topic"] = "ui-ux"
    assert route._reroute_item_guarded(None, item, _P4_ROUTES, _Cfg(), moves,
                                       decided=decided) == "rerouted"
    assert decided == {"primary": 1}


def test_run_reroute_stage_returns_decided_by_counts(monkeypatch):
    items = [
        _routed_item(pid="p1", tags=["web-dev", "ui-ux"], route_target="WebDB"),  # primary → UiDB
        _routed_item(pid="p2", tags=["web-dev", "ui-ux"], route_target="OldDB"),  # order → WebDB
        _routed_item(pid="p3", tags=["web-dev"], route_target="WebDB"),           # unchanged
    ]
    items[0]["primary_topic"] = "ui-ux"

    def _fake_runner(env, status, fn, progress, *, limit=None, group=None,
                     collections_cfg=None, stage_key=None, bar_label=None,
                     write_delay=0.0, delay_on=None):
        counts = {}
        for it in items:
            counter = fn(env, it, None)
            counts[counter] = counts.get(counter, 0) + 1
        counts.setdefault("failed", 0)
        return counts

    monkeypatch.setattr(route, "run_priority_stage", _fake_runner)
    monkeypatch.setattr(route, "write_route", lambda *a: None)
    monkeypatch.setattr(route, "get_item_status", lambda env, page_id: "Routed")

    r = route.run_reroute_stage(None, _P4_ROUTES, _Cfg(), None)
    assert r["rerouted"] == 2 and r["unchanged"] == 1
    assert r["decided_by"] == {"primary": 1, "order": 1}


def test_run_reroute_stage_reports_zero_decisions_when_nothing_moves(monkeypatch):
    def _fake_runner(env, status, fn, progress, *, limit=None, group=None,
                     collections_cfg=None, stage_key=None, bar_label=None,
                     write_delay=0.0, delay_on=None):
        return {"failed": 0}

    monkeypatch.setattr(route, "run_priority_stage", _fake_runner)
    r = route.run_reroute_stage(None, _P4_ROUTES, _Cfg(), None)
    assert r["decided_by"] == {"primary": 0, "order": 0}
