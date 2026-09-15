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

    r = route.run_reroute_stage(None, routes, _Cfg(), None)
    assert r["rerouted"] == 1 and r["unchanged"] == 1 and r["unrouted"] == 1 and r["failed"] == 0
    assert r["moves"] == {("OldDB", "NewDB"): 1, ("OldDB", None): 1}
