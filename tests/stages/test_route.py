import insta_save.stages.route as route
from insta_save.config.routes import Routes


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
