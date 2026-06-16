import insta_save.stages.route as route
from insta_save.config.routes import Routes
from insta_save.orchestrator import runner as runner_mod


class _Cfg:
    def group_of(self, name): return {"a-coll": "Biz"}.get(name, "uncategorized")


def _item(pid="p1", tags=None, collections=None):
    return {"page_id": pid, "source_id": pid, "tags": tags or [], "collections": collections or []}


def test_route_item_routes_on_tag(monkeypatch):
    written = {}
    monkeypatch.setattr(route, "write_route", lambda env, page_id, target: written.update({page_id: target}))
    routes = Routes(by_tag={"tool": "ToolsDB"})
    assert route._route_item(None, _item(tags=["tool"]), routes, _Cfg()) == "routed"
    assert written == {"p1": "ToolsDB"}


def test_route_item_unrouted_when_no_mapping(monkeypatch):
    monkeypatch.setattr(route, "write_route", lambda *a: (_ for _ in ()).throw(AssertionError("should not write")))
    assert route._route_item(None, _item(tags=["x"]), Routes(), _Cfg()) == "unrouted"


def test_route_item_dry_run_does_not_write(monkeypatch):
    monkeypatch.setattr(route, "write_route", lambda *a: (_ for _ in ()).throw(AssertionError("dry-run wrote")))
    assert route._route_item(None, _item(tags=["tool"]), Routes(by_tag={"tool": "T"}), _Cfg(), dry_run=True) == "routed"


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
    routes = Routes(by_tag={"tool": "TagDB"}, by_collection={"a-coll": "CollDB"})
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
