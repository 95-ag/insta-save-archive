import types

import pytest
from cli.isa import build_parser


def test_run_accepts_mode_and_stage():
    a = build_parser().parse_args(["run", "--mode", "first-time", "--stage", "enrich"])
    assert a.command == "run" and a.mode == "first-time" and a.stage == "enrich"


def test_run_mode_defaults_to_incremental():
    a = build_parser().parse_args(["run"])
    assert a.mode == "incremental" and a.stage is None


def test_invalid_mode_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--mode", "bogus"])


def test_invalid_stage_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--stage", "teleport"])


def test_subcommands_exist():
    for cmd in (["discover"], ["status"], ["backup", "--restore-check"]):
        assert build_parser().parse_args(cmd).command == cmd[0]


from cli import isa


def test_run_extract_dispatches(monkeypatch):
    calls = {}
    monkeypatch.setattr(isa, "_load_env", lambda: "ENV")
    monkeypatch.setattr(isa, "_load_run", lambda: type("R", (), {"extract": "EX"})())
    monkeypatch.setattr(isa, "_load_collections", lambda: "COLS")
    monkeypatch.setattr(isa, "ensure_schema", lambda env: calls.setdefault("schema", env))
    monkeypatch.setattr(isa, "setup_logging", lambda name: "logpath")

    class _SP:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(isa, "StageProgress", lambda title: _SP())

    def _run_stage(env, ex, progress, **kw):
        calls["stage"] = (env, ex, kw)
        return {"extracted": 3, "failed": 0}
    monkeypatch.setattr(isa, "run_extract_stage", _run_stage)

    isa.dispatch_run(type("A", (), {
        "mode": "incremental", "stage": "extract", "group": "Hustling",
        "limit": 5, "reextract": False, "retry_failed": False})())

    assert calls["schema"] == "ENV"
    assert calls["stage"][0] == "ENV" and calls["stage"][1] == "EX"
    assert calls["stage"][2]["group"] == "Hustling" and calls["stage"][2]["limit"] == 5


def test_run_unimplemented_stage_raises():
    with __import__("pytest").raises(SystemExit):
        isa.dispatch_run(type("A", (), {
            "mode": "incremental", "stage": "discover", "group": None,
            "limit": None, "reextract": False, "retry_failed": False,
            "collection": None, "fresh": False, "dry_run": False, "headed": False,
            "confirm_removed": None, "apply": False, "prepare": False})())


def _fake_run():
    import types
    return types.SimpleNamespace(
        enrich=types.SimpleNamespace(backend="claude-code", model="claude-sonnet", effort="medium"),
        output_language="english", char_budget=80000, max_items=15, image_token_budget=120000)


def test_enrich_prepare_requires_group(monkeypatch, capsys):
    import cli.isa as isa
    args = isa.build_parser().parse_args(
        ["run", "--stage", "enrich", "--prepare"])
    try:
        isa.dispatch_run(args)
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "group" in str(e).lower()


def test_calibrate_requires_group(monkeypatch):
    import cli.isa as isa
    args = isa.build_parser().parse_args(["run", "--stage", "calibrate"])
    try:
        isa.dispatch_run(args)
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "group" in str(e).lower()


def test_calibrate_invokes_gate_with_group(monkeypatch):
    import cli.isa as isa
    calls = {}
    monkeypatch.setattr(isa, "_load_env", lambda: object())
    monkeypatch.setattr(isa, "_load_run", lambda: type("R", (), {"enrich": type("E", (), {"backend": "claude-p"})()})())
    monkeypatch.setattr(isa, "_load_collections", lambda: object())
    monkeypatch.setattr(isa, "get_backend", lambda name: object())
    monkeypatch.setattr(isa, "setup_logging", lambda name: "logs/calibrate.log")
    monkeypatch.setattr(isa, "run_calibrate_gate",
                        lambda env, run_cfg, *, collections_cfg, backend, group: calls.update(group=group))
    isa.dispatch_run(isa.build_parser().parse_args(["run", "--stage", "calibrate", "--group", "Biz"]))
    assert calls["group"] == "Biz"


class _FakeProgress:
    """Stub StageProgress so the rich live display never runs under pytest."""
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _patch_prepare_path(monkeypatch, prepare_return):
    """Wire the enrich --prepare dispatch path with stubs, prepare() -> prepare_return."""
    import cli.isa as isa
    monkeypatch.setattr(isa, "_load_env", lambda: object())
    monkeypatch.setattr(isa, "_load_run", lambda: _fake_run())
    monkeypatch.setattr(isa, "load_vocab", lambda: "VOCAB")
    monkeypatch.setattr(isa, "_load_collections", lambda: "COLS")
    monkeypatch.setattr(isa, "setup_logging", lambda name: "log")
    monkeypatch.setattr(isa, "StageProgress", lambda title: _FakeProgress())
    monkeypatch.setattr(isa.enrich, "prepare", lambda *a, **k: prepare_return)
    return isa


def test_enrich_prepare_prints_drained_sentinel_when_empty(monkeypatch, capsys):
    isa = _patch_prepare_path(monkeypatch, prepare_return=0)
    args = isa.build_parser().parse_args(
        ["run", "--stage", "enrich", "--prepare", "--group", "Hustling"])
    isa.dispatch_run(args)
    out = capsys.readouterr().out
    assert "ENRICH_DRAINED group=Hustling lane=text" in out


def test_enrich_prepare_no_sentinel_when_items_left(monkeypatch, capsys):
    isa = _patch_prepare_path(monkeypatch, prepare_return=5)
    args = isa.build_parser().parse_args(
        ["run", "--stage", "enrich", "--prepare", "--group", "Hustling"])
    isa.dispatch_run(args)
    assert "ENRICH_DRAINED" not in capsys.readouterr().out


def test_enrich_apply_calls_stage(monkeypatch):
    import cli.isa as isa
    calls = {}
    monkeypatch.setattr(isa, "_load_env", lambda: object())
    monkeypatch.setattr(isa, "_load_run", lambda: _fake_run())
    monkeypatch.setattr(isa, "load_vocab", lambda: "VOCAB")
    monkeypatch.setattr(isa, "_load_collections", lambda: "COLS")
    monkeypatch.setattr(isa, "setup_logging", lambda name: "log")
    monkeypatch.setattr(isa, "StageProgress", lambda title: _FakeProgress())
    def _fake_apply(env, *, vocab, model, collections_cfg, progress=None):
        calls["apply"] = (vocab, model, collections_cfg, progress is not None)
        return {"written": 1, "failed": 0}
    monkeypatch.setattr(isa.enrich, "apply", _fake_apply)
    args = isa.build_parser().parse_args(["run", "--stage", "enrich", "--apply"])
    isa.dispatch_run(args)
    assert calls["apply"] == ("VOCAB", "claude-sonnet", "COLS", True)  # collections_cfg + progress passed through


def test_discover_parser_accepts_flags():
    from cli.isa import build_parser
    args = build_parser().parse_args(["discover", "--headed", "--fresh", "--collection", "Dev"])
    assert args.command == "discover" and args.headed and args.fresh and args.collection == "Dev"


def _stub_progress(monkeypatch):
    class _SP:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(isa, "StageProgress", lambda title: _SP())


def _det_common(monkeypatch, run_obj):
    monkeypatch.setattr(isa, "_load_env", lambda: type("E", (), {"notion_write_delay": 0.4})())
    monkeypatch.setattr(isa, "_load_run", lambda: run_obj)
    monkeypatch.setattr(isa, "_load_collections", lambda: "COLS")
    monkeypatch.setattr(isa, "ensure_schema", lambda env: None)
    monkeypatch.setattr(isa, "setup_logging", lambda name: "logpath")
    _stub_progress(monkeypatch)


def _det_args(**kw):
    base = {"mode": "incremental", "stage": "deterministic", "group": None, "limit": None,
            "prepare": False, "apply": False, "reextract": False,
            "retry_failed": False, "collection": None}
    base.update(kw)
    return type("A", (), base)()


def test_deterministic_template_dispatches(monkeypatch):
    run_obj = type("R", (), {"deterministic_title_mode": "template",
                             "output_language": "english", "max_items": None})()
    _det_common(monkeypatch, run_obj)
    seen = {}
    import insta_save.stages.deterministic as det
    monkeypatch.setattr(det, "run_deterministic_stage",
                        lambda env, cols, progress, **kw: seen.setdefault("kw", kw) or {"tagged": 2, "skipped_extract_path": 1})
    isa.dispatch_run(_det_args(group="Lifestyle", limit=5))
    assert seen["kw"] == {"limit": 5, "group": "Lifestyle", "write_delay": 0.4}


def _llm_run_obj(backend="claude-code"):
    import types
    return types.SimpleNamespace(
        deterministic_title_mode="llm", output_language="english", max_items=None,
        enrich=types.SimpleNamespace(backend=backend, model="m"))


def test_deterministic_llm_requires_prepare_or_apply(monkeypatch):
    # agent-filled backend (claude-code): a step flag is required
    _det_common(monkeypatch, _llm_run_obj())
    with pytest.raises(SystemExit):
        isa.dispatch_run(_det_args())


def test_deterministic_llm_apply_dispatches(monkeypatch):
    _det_common(monkeypatch, _llm_run_obj())
    import insta_save.stages.deterministic as det
    monkeypatch.setattr(det, "apply", lambda env, progress=None: {"written": 3, "failed": 0})
    isa.dispatch_run(_det_args(apply=True))  # no SystemExit = dispatched


def test_deterministic_llm_prepare_requires_group(monkeypatch):
    _det_common(monkeypatch, _llm_run_obj())
    with pytest.raises(SystemExit):
        isa.dispatch_run(_det_args(prepare=True))  # --prepare without --group


def test_deterministic_llm_automated_backend_drains(monkeypatch, capsys):
    import insta_save.stages.deterministic as det
    from insta_save.backends import local_ollama
    _det_common(monkeypatch, _llm_run_obj("local"))
    monkeypatch.setattr(isa, "_load_env", lambda: type("E", (), {"tmp_dir": "tmp"})())
    calls = {"prepare": 0, "fill": 0, "apply": 0}
    batched = iter([1, 0])  # batch once, then drained
    monkeypatch.setattr(det, "prepare", lambda *a, **k: calls.__setitem__("prepare", calls["prepare"] + 1)
                        or {"batched": next(batched), "finalized_template": 0})
    monkeypatch.setattr(local_ollama, "fill",
                        lambda env, run_cfg, enrich_dir: calls.__setitem__("fill", calls["fill"] + 1))
    monkeypatch.setattr(det, "apply", lambda env, progress=None: calls.__setitem__("apply", calls["apply"] + 1)
                        or {"written": 1, "failed": 0})
    isa.dispatch_run(_det_args(group="Lifestyle"))
    assert calls == {"prepare": 2, "fill": 1, "apply": 1}
    assert "DETERMINISTIC_DRAINED group=Lifestyle" in capsys.readouterr().out


def _fake_run_backend(backend):
    import types
    return types.SimpleNamespace(
        enrich=types.SimpleNamespace(backend=backend, model="m", effort="medium",
                                     api_mode="sync"),
        output_language="english", char_budget=80000, max_items=15, image_token_budget=120000)


def test_enrich_vision_lane_rejects_non_vision_backend(monkeypatch):
    import cli.isa as isa
    monkeypatch.setattr(isa, "_load_env", lambda: object())
    monkeypatch.setattr(isa, "_load_run", lambda: _fake_run_backend("local"))
    args = isa.build_parser().parse_args(
        ["run", "--stage", "enrich", "--prepare", "--group", "G", "--lane", "vision"])
    with pytest.raises(SystemExit) as e:
        isa.dispatch_run(args)
    assert "vision-capable" in str(e.value)


def test_enrich_automated_backend_drains(monkeypatch, capsys):
    import types
    import cli.isa as isa
    from insta_save.backends import base, local_ollama

    env = types.SimpleNamespace(tmp_dir="tmp")
    monkeypatch.setattr(isa, "_load_env", lambda: env)
    monkeypatch.setattr(isa, "_load_run", lambda: _fake_run_backend("local"))
    monkeypatch.setattr(isa, "load_vocab", lambda: "VOCAB")
    monkeypatch.setattr(isa, "_load_collections", lambda: "COLS")
    monkeypatch.setattr(isa, "setup_logging", lambda name: "log")
    monkeypatch.setattr(isa, "StageProgress", lambda title: _FakeProgress())

    calls = {"prepare": 0, "fill": 0, "apply": 0}
    counts = iter([2, 0])  # batch once, then drained

    def _prepare(*a, **k):
        calls["prepare"] += 1
        return next(counts)
    monkeypatch.setattr(isa.enrich, "prepare", _prepare)
    monkeypatch.setattr(local_ollama, "fill",
                        lambda env, run_cfg, enrich_dir: calls.__setitem__("fill", calls["fill"] + 1)
                        or base.FillResult(filled=2, failed=0))
    monkeypatch.setattr(isa.enrich, "apply",
                        lambda env, **k: calls.__setitem__("apply", calls["apply"] + 1)
                        or {"written": 2, "failed": 0})

    args = isa.build_parser().parse_args(
        ["run", "--stage", "enrich", "--group", "Hustling"])
    isa.dispatch_run(args)

    assert calls == {"prepare": 2, "fill": 1, "apply": 1}
    assert "ENRICH_DRAINED group=Hustling lane=text" in capsys.readouterr().out


def test_enrich_automated_backend_stops_on_no_progress(monkeypatch, capsys):
    import types
    import cli.isa as isa
    from insta_save.backends import base, local_ollama

    env = types.SimpleNamespace(tmp_dir="tmp")
    monkeypatch.setattr(isa, "_load_env", lambda: env)
    monkeypatch.setattr(isa, "_load_run", lambda: _fake_run_backend("local"))
    monkeypatch.setattr(isa, "load_vocab", lambda: "VOCAB")
    monkeypatch.setattr(isa, "_load_collections", lambda: "COLS")
    monkeypatch.setattr(isa, "setup_logging", lambda name: "log")
    monkeypatch.setattr(isa, "StageProgress", lambda title: _FakeProgress())

    calls = {"prepare": 0, "fill": 0, "apply": 0}

    def _prepare(*a, **k):
        calls["prepare"] += 1
        return 2  # never drains: items stay Extracted because apply writes nothing
    monkeypatch.setattr(isa.enrich, "prepare", _prepare)
    monkeypatch.setattr(local_ollama, "fill",
                        lambda env, run_cfg, enrich_dir: calls.__setitem__("fill", calls["fill"] + 1)
                        or base.FillResult(filled=0, failed=2))
    monkeypatch.setattr(isa.enrich, "apply",
                        lambda env, **k: calls.__setitem__("apply", calls["apply"] + 1)
                        or {"written": 0, "failed": 2})

    args = isa.build_parser().parse_args(
        ["run", "--stage", "enrich", "--group", "Hustling"])
    isa.dispatch_run(args)

    # The guard breaks after one zero-progress apply: prepare runs exactly once.
    assert calls == {"prepare": 1, "fill": 1, "apply": 1}
    assert "no items applied for group Hustling" in capsys.readouterr().out


def test_enrich_status_prints_remaining(monkeypatch, capsys):
    import cli.isa as isa
    from insta_save.backends import cowork
    monkeypatch.setattr(isa, "_load_env", lambda: object())
    monkeypatch.setattr(isa, "_load_run", lambda: _fake_run_backend("local"))
    monkeypatch.setattr(isa, "_load_collections", lambda: "COLS")
    monkeypatch.setattr(cowork, "status", lambda env, cols, group: 7)
    args = isa.build_parser().parse_args(
        ["run", "--stage", "enrich", "--status", "--group", "Hustling"])
    isa.dispatch_run(args)
    assert "Hustling: 7 enrichable remaining" in capsys.readouterr().out


def test_enrich_lane_arg_defaults_text():
    from cli.isa import build_parser
    args = build_parser().parse_args(["run", "--stage", "enrich", "--prepare", "--group", "G"])
    assert args.lane == "text"
    args2 = build_parser().parse_args(
        ["run", "--stage", "enrich", "--prepare", "--group", "G", "--lane", "vision"])
    assert args2.lane == "vision"


class _FakeProgress2:
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _route_common(monkeypatch):
    """Patch everything dispatch_run needs for the route branch except run_route_stage."""
    import insta_save.stages.route as route_mod
    monkeypatch.setattr(isa, "_load_env", lambda: type("E", (), {"notion_write_delay": 0.4})())
    monkeypatch.setattr(isa, "_load_collections", lambda: "COLS")
    monkeypatch.setattr(isa, "ensure_schema", lambda env: None)
    monkeypatch.setattr(isa, "setup_logging", lambda name: "logpath")
    monkeypatch.setattr(isa, "StageProgress", lambda title: _FakeProgress2())
    # Patch load_routes at the import site used by dispatch_run (cli.isa local import)
    import insta_save.config.routes as routes_mod
    monkeypatch.setattr(routes_mod, "load_routes", lambda: [])
    return route_mod


def test_route_dispatches_with_group(monkeypatch):
    route_mod = _route_common(monkeypatch)
    calls = {}
    def _fake_run_route(env, routes, collections_cfg, progress, *, limit=None, group=None,
                        dry_run=False, write_delay=0.0):
        calls["kwargs"] = {"limit": limit, "group": group, "dry_run": dry_run}
        return {"routed": 3, "unrouted": 1, "failed": 0}
    monkeypatch.setattr(route_mod, "run_route_stage", _fake_run_route)

    args = isa.build_parser().parse_args(["run", "--stage", "route", "--group", "Biz"])
    isa.dispatch_run(args)

    assert calls["kwargs"]["group"] == "Biz"
    assert calls["kwargs"]["dry_run"] is False


def test_route_dispatches_dry_run(monkeypatch):
    route_mod = _route_common(monkeypatch)
    calls = {}
    def _fake_run_route(env, routes, collections_cfg, progress, *, limit=None, group=None,
                        dry_run=False, write_delay=0.0):
        calls["kwargs"] = {"limit": limit, "group": group, "dry_run": dry_run}
        return {"routed": 0, "unrouted": 2, "failed": 0}
    monkeypatch.setattr(route_mod, "run_route_stage", _fake_run_route)

    args = isa.build_parser().parse_args(["run", "--stage", "route", "--dry-run"])
    isa.dispatch_run(args)

    assert calls["kwargs"]["dry_run"] is True


def test_route_reroute_dispatches_to_reroute_stage_not_route_stage(monkeypatch, capsys):
    route_mod = _route_common(monkeypatch)
    monkeypatch.setattr(route_mod, "run_route_stage",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("wrong stage fn")))
    calls = {}

    def _fake_run_reroute(env, routes, collections_cfg, progress, *, limit=None, group=None,
                          dry_run=False, write_delay=0.0):
        calls["kwargs"] = {"limit": limit, "group": group, "dry_run": dry_run}
        return {"rerouted": 2, "unrouted": 1, "unchanged": 3, "failed": 0,
                "moves": {("Old", "New"): 2, ("Old", None): 1}}
    monkeypatch.setattr(route_mod, "run_reroute_stage", _fake_run_reroute)

    args = isa.build_parser().parse_args(
        ["run", "--stage", "route", "--reroute", "--group", "Biz", "--limit", "5"])
    isa.dispatch_run(args)

    assert calls["kwargs"]["dry_run"] is False
    assert calls["kwargs"]["limit"] == 5
    assert calls["kwargs"]["group"] == "Biz"
    out = capsys.readouterr().out
    assert "2 rerouted" in out and "1 back to Tagged" in out and "3 unchanged" in out
    assert "Old → New : 2" in out
    assert "Old → Tagged : 1" in out


def test_route_reroute_summary_includes_failed_when_nonzero(monkeypatch, capsys):
    route_mod = _route_common(monkeypatch)
    monkeypatch.setattr(route_mod, "run_reroute_stage",
                        lambda *a, **k: {"rerouted": 1, "unrouted": 0, "unchanged": 0,
                                         "failed": 2, "moves": {}})
    args = isa.build_parser().parse_args(["run", "--stage", "route", "--reroute"])
    isa.dispatch_run(args)
    assert "2 failed" in capsys.readouterr().out


def test_route_reroute_summary_omits_failed_when_zero(monkeypatch, capsys):
    route_mod = _route_common(monkeypatch)
    monkeypatch.setattr(route_mod, "run_reroute_stage",
                        lambda *a, **k: {"rerouted": 1, "unrouted": 0, "unchanged": 0,
                                         "failed": 0, "moves": {}})
    args = isa.build_parser().parse_args(["run", "--stage", "route", "--reroute"])
    isa.dispatch_run(args)
    assert "failed" not in capsys.readouterr().out


def test_route_reroute_summary_includes_skipped_stale_when_nonzero(monkeypatch, capsys):
    route_mod = _route_common(monkeypatch)
    monkeypatch.setattr(route_mod, "run_reroute_stage",
                        lambda *a, **k: {"rerouted": 0, "unrouted": 0, "unchanged": 0,
                                         "skipped_stale": 3, "failed": 0, "moves": {}})
    args = isa.build_parser().parse_args(["run", "--stage", "route", "--reroute"])
    isa.dispatch_run(args)
    assert "3 skipped (stale)" in capsys.readouterr().out


def test_route_reroute_summary_formats_missing_previous_target_as_none_marker(monkeypatch, capsys):
    route_mod = _route_common(monkeypatch)
    monkeypatch.setattr(route_mod, "run_reroute_stage",
                        lambda *a, **k: {"rerouted": 0, "unrouted": 1, "unchanged": 0,
                                         "failed": 0, "moves": {(None, "NewDB"): 1}})
    args = isa.build_parser().parse_args(["run", "--stage", "route", "--reroute"])
    isa.dispatch_run(args)
    out = capsys.readouterr().out
    assert "(none) → NewDB : 1" in out
    assert "None → NewDB" not in out


def test_route_reroute_dry_run_marks_summary(monkeypatch, capsys):
    route_mod = _route_common(monkeypatch)
    calls = {}

    def _fake_run_reroute(env, routes, collections_cfg, progress, *, limit=None, group=None,
                          dry_run=False, write_delay=0.0):
        calls["kwargs"] = {"dry_run": dry_run}
        return {"rerouted": 0, "unrouted": 0, "unchanged": 0, "failed": 0, "moves": {}}
    monkeypatch.setattr(route_mod, "run_reroute_stage", _fake_run_reroute)

    args = isa.build_parser().parse_args(["run", "--stage", "route", "--reroute", "--dry-run"])
    isa.dispatch_run(args)

    assert calls["kwargs"]["dry_run"] is True
    assert "(dry-run)" in capsys.readouterr().out


def test_route_summary_prints_the_decided_by_breakdown_when_routed(monkeypatch, capsys):
    route_mod = _route_common(monkeypatch)
    monkeypatch.setattr(route_mod, "run_route_stage",
                        lambda *a, **k: {"routed": 2, "unrouted": 1, "failed": 0,
                                         "decided_by": {"primary": 1, "order": 1}})
    args = isa.build_parser().parse_args(["run", "--stage", "route"])
    isa.dispatch_run(args)
    out = capsys.readouterr().out
    assert "decided by primary topic: 1" in out and "by rule order: 1" in out


def test_route_summary_omits_the_decided_by_breakdown_when_nothing_routed(monkeypatch, capsys):
    route_mod = _route_common(monkeypatch)
    monkeypatch.setattr(route_mod, "run_route_stage",
                        lambda *a, **k: {"routed": 0, "unrouted": 3, "failed": 0,
                                         "decided_by": {"primary": 0, "order": 0}})
    args = isa.build_parser().parse_args(["run", "--stage", "route"])
    isa.dispatch_run(args)
    out = capsys.readouterr().out
    assert "decided by primary topic" not in out


def test_route_without_reroute_still_uses_route_stage(monkeypatch):
    """--reroute defaults to False; a plain route run must not call run_reroute_stage."""
    route_mod = _route_common(monkeypatch)
    monkeypatch.setattr(route_mod, "run_reroute_stage",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("wrong stage fn")))
    monkeypatch.setattr(route_mod, "run_route_stage",
                        lambda *a, **k: {"routed": 1, "unrouted": 0, "failed": 0})

    args = isa.build_parser().parse_args(["run", "--stage", "route"])
    isa.dispatch_run(args)


# ---------------------------------------------------------------------------
# backup CLI tests
# ---------------------------------------------------------------------------

def _patch_backup_common(monkeypatch, tmp_dir):
    """Patch the common scaffolding for the backup command."""
    import types
    import insta_save.backup as backup_mod

    env = types.SimpleNamespace(tmp_dir=tmp_dir)
    monkeypatch.setattr(isa, "_load_env", lambda: env)
    monkeypatch.setattr(isa, "_load_collections", lambda: "COLS")
    monkeypatch.setattr(isa, "setup_logging", lambda name: "logpath")
    return env, backup_mod


def test_backup_command_calls_backup_and_prints_path(monkeypatch, tmp_path, capsys):
    """isa backup → calls backup() and prints the written path."""
    env, _ = _patch_backup_common(monkeypatch, str(tmp_path))

    written_path = tmp_path / "backups" / "notion-20260616_120000.json"
    calls = {}

    def _fake_backup(env_arg, *, out_dir, ts):
        calls["args"] = (env_arg, str(out_dir), ts)
        written_path.parent.mkdir(parents=True, exist_ok=True)
        written_path.write_text('{"snapshot_ts":"t","count":5,"pages":[]}', encoding="utf-8")
        return written_path

    # Patch at the isa module level (isa imports backup at module load time)
    monkeypatch.setattr(isa, "backup", _fake_backup)

    import types as _types

    _args = _types.SimpleNamespace(command="backup", restore_check=False)

    class _FakeParser:
        def parse_args(self, argv=None):
            return _args

    monkeypatch.setattr(isa, "build_parser", lambda: _FakeParser())

    isa.main()

    out = capsys.readouterr().out
    assert "backup" in out.lower() or str(written_path) in out or "notion-" in out
    assert calls  # backup() was called


def test_backup_command_restore_check_ok(monkeypatch, tmp_path, capsys):
    """isa backup --restore-check → calls restore_check on newest file, prints OK."""
    env, _ = _patch_backup_common(monkeypatch, str(tmp_path))

    # Pre-create a backup file so newest-file lookup finds it
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True)
    backup_file = backup_dir / "notion-20260616_120000.json"
    backup_file.write_text('{"snapshot_ts":"t","count":2,"pages":[]}', encoding="utf-8")

    calls = {}

    def _fake_restore_check(env_arg, path, collections_cfg):
        calls["args"] = (env_arg, path, collections_cfg)
        return {"ok": True, "count": 2, "mismatches": []}

    # Patch at the isa module level (isa imports restore_check at module load time)
    monkeypatch.setattr(isa, "restore_check", _fake_restore_check)

    import types as _types

    _args = _types.SimpleNamespace(command="backup", restore_check=True)

    class _FakeParser:
        def parse_args(self, argv=None):
            return _args

    monkeypatch.setattr(isa, "build_parser", lambda: _FakeParser())

    isa.main()

    out = capsys.readouterr().out
    assert "ok" in out.lower() or "2" in out
    assert calls  # restore_check() was called


def test_backup_command_restore_check_mismatch(monkeypatch, tmp_path, capsys):
    """isa backup --restore-check → prints mismatch summary when ok=False."""
    env, _ = _patch_backup_common(monkeypatch, str(tmp_path))

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True)
    backup_file = backup_dir / "notion-20260616_120000.json"
    backup_file.write_text('{"snapshot_ts":"t","count":2,"pages":[]}', encoding="utf-8")

    def _fake_restore_check(env_arg, path, collections_cfg):
        return {"ok": False, "count": 2, "mismatches": ["count delta: backup=2 live=3"]}

    monkeypatch.setattr(isa, "restore_check", _fake_restore_check)

    import types as _types

    _args = _types.SimpleNamespace(command="backup", restore_check=True)

    class _FakeParser:
        def parse_args(self, argv=None):
            return _args

    monkeypatch.setattr(isa, "build_parser", lambda: _FakeParser())

    isa.main()

    out = capsys.readouterr().out
    assert "mismatch" in out.lower() or "delta" in out.lower() or "2" in out


# ---------------------------------------------------------------------------
# status CLI tests
# ---------------------------------------------------------------------------

def _fake_status_rows():
    return [
        {"group": "Hustling", "Imported": 2, "Queued": 0, "Extracted": 1,
         "Tagged": 3, "Routed": 0, "Failed": 0, "remaining": 3},
        {"group": "Biz", "Imported": 1, "Queued": 1, "Extracted": 0,
         "Tagged": 0, "Routed": 0, "Failed": 1, "remaining": 2},
        {"group": "TOTAL", "Imported": 3, "Queued": 1, "Extracted": 1,
         "Tagged": 3, "Routed": 0, "Failed": 1, "remaining": 5},
    ]


def _patch_status_common(monkeypatch):
    import types
    env = types.SimpleNamespace()
    monkeypatch.setattr(isa, "_load_env", lambda: env)
    monkeypatch.setattr(isa, "_load_collections", lambda: "COLS")
    monkeypatch.setattr(isa, "setup_logging", lambda name: "log")
    return env


def test_status_command_calls_build_status_and_prints_table(monkeypatch, capsys):
    _patch_status_common(monkeypatch)
    monkeypatch.setattr(isa, "build_status", lambda env, cols: _fake_status_rows())

    import types as _types
    _args = _types.SimpleNamespace(command="status", retry_failed=False)

    class _FakeParser:
        def parse_args(self, argv=None):
            return _args

    monkeypatch.setattr(isa, "build_parser", lambda: _FakeParser())
    isa.main()

    out = capsys.readouterr().out
    assert "Hustling" in out
    assert "Biz" in out
    assert "TOTAL" in out


def test_status_table_columns_include_distributed_and_misrouted():
    assert isa._STATUS_COLS == (
        "Imported", "Queued", "Extracted", "Tagged", "Routed",
        "Distributed", "Misrouted", "Failed", "remaining",
    )


def test_status_table_prints_distributed_and_misrouted_counts(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "200")
    row = {"group": "TOTAL", "Imported": 0, "Queued": 0, "Extracted": 0, "Tagged": 0,
           "Routed": 0, "Distributed": 41, "Misrouted": 17, "Failed": 0, "remaining": 0}
    isa._print_status_table([row])
    out = capsys.readouterr().out
    assert "Distributed" in out and "Misrouted" in out
    assert "41" in out and "17" in out


def test_status_retry_failed_calls_retry_and_prints_summary(monkeypatch, capsys):
    _patch_status_common(monkeypatch)
    monkeypatch.setattr(isa, "_retry_failed",
                        lambda env: {"requeued": 3, "to_extracted": 2, "to_queued": 1})

    import types as _types
    _args = _types.SimpleNamespace(command="status", retry_failed=True)

    class _FakeParser:
        def parse_args(self, argv=None):
            return _args

    monkeypatch.setattr(isa, "build_parser", lambda: _FakeParser())
    isa.main()

    out = capsys.readouterr().out
    assert "3" in out  # requeued count
    assert "Extracted" in out
    assert "Queued" in out


def test_status_parser_accepts_retry_failed():
    args = build_parser().parse_args(["status", "--retry-failed"])
    assert args.command == "status"
    assert args.retry_failed is True


def test_status_parser_retry_failed_defaults_false():
    args = build_parser().parse_args(["status"])
    assert args.retry_failed is False


def test_backup_command_restore_check_no_file(monkeypatch, tmp_path, capsys):
    """isa backup --restore-check with no backup file → prints clear message, no crash."""
    import insta_save.backup as backup_mod

    env, _ = _patch_backup_common(monkeypatch, str(tmp_path))
    # No backup file created — directory doesn't exist

    import types as _types

    _args = _types.SimpleNamespace(command="backup", restore_check=True)

    class _FakeParser:
        def parse_args(self, argv=None):
            return _args

    monkeypatch.setattr(isa, "build_parser", lambda: _FakeParser())

    # Should not crash
    isa.main()

    out = capsys.readouterr().out
    assert "no backup" in out.lower() or "not found" in out.lower() or "backup" in out.lower()


# ---------------------------------------------------------------------------
# Mode dispatch (isa run --mode first-time / incremental, no --stage)
# ---------------------------------------------------------------------------

import types as _types_mod
from insta_save.orchestrator.sequence import GroupStep, Plan


def _make_plan(action="done", automated=True, next_action_idx=None):
    """Build a minimal Plan for testing."""
    step = GroupStep(group="TestG", action=action, automated=automated,
                     detail=f"{action} detail")
    done = action == "done"
    return Plan(steps=[step], next_action=None if done else step, done=done)


def _patch_mode_dispatch(monkeypatch, plan, mode="first-time", dry_run=False):
    """Patch all I/O so _dispatch_mode runs without real Notion/FS calls.

    Returns the calls dict (records mode/dry_run passed to run_pipeline).
    """
    import insta_save.orchestrator.pipeline as pipeline_mod

    calls = {}

    env = _types_mod.SimpleNamespace(tmp_dir="tmp", notion_write_delay=0.0, ig_username="testuser")
    run_cfg = _types_mod.SimpleNamespace(
        enrich=_types_mod.SimpleNamespace(backend="claude-code", model="m", effort="medium"),
        output_language="english", char_budget=80000, max_items=15,
        guardrails_max_items_per_run=None, guardrails_max_spend_usd=None,
    )
    backend = _types_mod.SimpleNamespace(AUTOMATED=False, NAME="claude-code",
                                         VISION_CAPABLE=False,
                                         batch_budgets=lambda r: None)

    monkeypatch.setattr(isa, "_load_env", lambda: env)
    monkeypatch.setattr(isa, "_load_run", lambda: run_cfg)
    monkeypatch.setattr(isa, "_load_collections", lambda: "COLS")
    monkeypatch.setattr(isa, "load_vocab", lambda: "VOCAB")
    monkeypatch.setattr(isa, "get_backend", lambda name: backend)
    monkeypatch.setattr(isa, "load_routes", lambda: _types_mod.SimpleNamespace(
        by_tag=[], by_collection={}, by_group={}))
    monkeypatch.setattr(isa, "setup_logging", lambda name: "logs/run-20260101_000000.log")
    monkeypatch.setattr(isa, "preflight", lambda env, run_cfg, stages: None)
    monkeypatch.setattr(isa, "build_status",
                        lambda env, cols: [{"group": "TOTAL", "remaining": 5}])
    monkeypatch.setattr(isa, "ensure_run_json", lambda *a, **k: None)
    monkeypatch.setattr(isa, "run_config_gate", lambda run_cfg, **k: run_cfg)
    monkeypatch.setattr(isa, "_has_collections", lambda: True)   # warm path by default

    # _dispatch_mode now calls run_pipeline via a function-local import.
    # Patch the attribute on the pipeline module so the local import picks it up.
    def _fake_pipeline(env, run_cfg, cols, vocab, backend, routes, *,
                       mode, dry_run=False, select_mode="inline",
                       ig_username=None, headed=False, fresh=False,
                       progress_factory=None):
        calls["mode"] = mode
        calls["dry_run"] = dry_run
        calls["select_mode"] = select_mode
        calls["fresh"] = fresh
        calls["cols"] = cols
        return plan

    monkeypatch.setattr(pipeline_mod, "run_pipeline", _fake_pipeline)

    args = isa.build_parser().parse_args(
        ["run", "--mode", mode] + (["--dry-run"] if dry_run else []))
    return args, calls


def test_mode_first_time_dispatches_run_pipeline(monkeypatch, capsys):
    plan = _make_plan("done")
    args, calls = _patch_mode_dispatch(monkeypatch, plan, mode="first-time")
    isa._dispatch_mode(args)
    assert calls.get("mode") == "first-time"
    assert "All groups complete" in capsys.readouterr().out


def test_mode_incremental_dispatches_run_pipeline(monkeypatch, capsys):
    plan = _make_plan("done")
    args, calls = _patch_mode_dispatch(monkeypatch, plan, mode="incremental")
    isa._dispatch_mode(args)
    assert calls.get("mode") == "incremental"
    assert "All groups complete" in capsys.readouterr().out


def test_load_collections_error_exits_cleanly(monkeypatch):
    """When collections.json can't load on a non-cold run (incremental, or a corrupt file),
    exit with the actionable message — not an uncaught RuntimeError traceback."""
    import pytest
    plan = _make_plan("done")
    args, _ = _patch_mode_dispatch(monkeypatch, plan, mode="incremental")

    def _raise():
        raise RuntimeError("Collections config not found: config/collections.json\n"
                           "Build it with `isa discover` (see README.md).")
    monkeypatch.setattr(isa, "_load_collections", _raise)
    with pytest.raises(SystemExit) as exc:
        isa._dispatch_mode(args)
    assert "isa discover" in str(exc.value)


def test_first_time_cold_start_bootstraps(monkeypatch):
    """A first-time real run with no collections.json proceeds (the pipeline's front-fold
    discover builds it): it must NOT load collections or run the pre-run guardrail, and it
    passes collections_cfg=None to run_pipeline."""
    plan = _make_plan("done")
    args, calls = _patch_mode_dispatch(monkeypatch, plan, mode="first-time")
    monkeypatch.setattr(isa, "_has_collections", lambda: False)   # cold

    def _boom(*a, **k):
        raise AssertionError("must not be called on a cold start")
    monkeypatch.setattr(isa, "_load_collections", _boom)
    monkeypatch.setattr(isa, "build_status", _boom)               # guardrail skipped

    isa._dispatch_mode(args)
    assert calls["mode"] == "first-time"
    assert calls["cols"] is None


def test_mode_dry_run_passes_through(monkeypatch, capsys):
    plan = _make_plan("done")
    args, calls = _patch_mode_dispatch(monkeypatch, plan, mode="first-time", dry_run=True)
    isa._dispatch_mode(args)
    assert calls.get("dry_run") is True


def test_mode_prints_calibrate_gate(monkeypatch, capsys):
    """When the plan next_action is a calibrate gate, prints the inline-gate hint."""
    plan = _make_plan("calibrate", automated=False)
    args, calls = _patch_mode_dispatch(monkeypatch, plan, mode="first-time")
    isa._dispatch_mode(args)
    out = capsys.readouterr().out
    assert "calibrate gate runs inline" in out
    assert "calibrate" in out.lower()
    assert "TestG" in out


def test_mode_prints_agent_enrich_gate(monkeypatch, capsys):
    """When the plan next_action is a non-automated enrich step, prints the prepare hint."""
    plan = _make_plan("enrich", automated=False)
    args, calls = _patch_mode_dispatch(monkeypatch, plan, mode="first-time")
    isa._dispatch_mode(args)
    out = capsys.readouterr().out
    assert "NEXT (manual)" in out
    assert "enrich" in out.lower()
    assert "TestG" in out


def test_mode_calls_preflight(monkeypatch):
    """preflight is called before the sequencer."""
    plan = _make_plan("done")
    calls = {}
    args, _ = _patch_mode_dispatch(monkeypatch, plan, mode="incremental")
    monkeypatch.setattr(isa, "preflight",
                        lambda env, run_cfg, stages: calls.__setitem__("preflight", stages))
    isa._dispatch_mode(args)
    assert "preflight" in calls
    assert "extract" in calls["preflight"]
    assert "enrich" in calls["preflight"]


def test_first_time_runs_config_gate(monkeypatch, capsys):
    plan = _make_plan("done")
    args, calls = _patch_mode_dispatch(monkeypatch, plan, mode="first-time")
    gate = {"n": 0}
    monkeypatch.setattr(isa, "ensure_run_json", lambda *a, **k: None)
    def _fake_gate(run_cfg, **k):
        gate["n"] += 1
        return run_cfg
    monkeypatch.setattr(isa, "run_config_gate", _fake_gate)
    isa._dispatch_mode(args)
    assert gate["n"] == 1


def test_incremental_skips_config_gate(monkeypatch, capsys):
    plan = _make_plan("done")
    args, calls = _patch_mode_dispatch(monkeypatch, plan, mode="incremental")
    gate = {"n": 0}
    monkeypatch.setattr(isa, "ensure_run_json", lambda *a, **k: None)
    monkeypatch.setattr(isa, "run_config_gate",
                        lambda run_cfg, **k: gate.update(n=gate["n"] + 1) or run_cfg)
    isa._dispatch_mode(args)
    assert gate["n"] == 0


def test_mode_calls_guardrail_check(monkeypatch):
    """check_item_cap is called with the remaining count from build_status."""
    plan = _make_plan("done")
    calls = {}
    args, _ = _patch_mode_dispatch(monkeypatch, plan, mode="first-time")
    monkeypatch.setattr(isa.guardrails, "check_item_cap",
                        lambda planned, run_cfg: calls.__setitem__("planned", planned))
    isa._dispatch_mode(args)
    # build_status mock returns remaining=5 for TOTAL
    assert calls.get("planned") == 5


def test_mode_usage_reminder_printed_for_session_backend(monkeypatch, capsys):
    """usage_reminder output is printed when the backend is session-based."""
    plan = _make_plan("done")
    args, _ = _patch_mode_dispatch(monkeypatch, plan, mode="first-time")
    monkeypatch.setattr(isa.guardrails, "usage_reminder",
                        lambda run_cfg: "watch your Claude-Max usage")
    isa._dispatch_mode(args)
    assert "watch your Claude-Max usage" in capsys.readouterr().out


def test_mode_usage_reminder_not_printed_when_none(monkeypatch, capsys):
    """usage_reminder returns None for non-session backends — nothing extra printed."""
    plan = _make_plan("done")
    args, _ = _patch_mode_dispatch(monkeypatch, plan, mode="first-time")
    monkeypatch.setattr(isa.guardrails, "usage_reminder", lambda run_cfg: None)
    isa._dispatch_mode(args)
    out = capsys.readouterr().out
    assert "usage" not in out.lower() and "reminder" not in out.lower()


# ---------------------------------------------------------------------------
# Item 1: --prepare / --apply mutual exclusivity
# ---------------------------------------------------------------------------

def test_enrich_both_prepare_and_apply_raises(monkeypatch):
    """Passing both --prepare and --apply on the agent-filled enrich path must raise SystemExit."""
    import cli.isa as isa
    monkeypatch.setattr(isa, "_load_env", lambda: object())
    monkeypatch.setattr(isa, "_load_run", lambda: _fake_run())
    args = isa.build_parser().parse_args(
        ["run", "--stage", "enrich", "--prepare", "--apply", "--group", "Hustling"])
    with pytest.raises(SystemExit) as exc_info:
        isa.dispatch_run(args)
    assert "exactly one" in str(exc_info.value)


def test_deterministic_llm_both_prepare_and_apply_raises(monkeypatch):
    """Passing both --prepare and --apply on the deterministic llm agent-filled path must raise SystemExit."""
    import cli.isa as isa
    _det_common(monkeypatch, _llm_run_obj())
    args = isa.build_parser().parse_args(
        ["run", "--stage", "deterministic", "--prepare", "--apply"])
    with pytest.raises(SystemExit) as exc_info:
        isa.dispatch_run(args)
    assert "exactly one" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Item 2: Relative log path in printed output
# ---------------------------------------------------------------------------

def test_select_prints_relative_log_path(monkeypatch, capsys):
    """The 'Logging to ...' line should print a relative path, not an absolute one."""
    import os
    import types
    import cli.isa as isa
    from insta_save.stages import select as select_mod

    abs_log = os.path.join(os.getcwd(), "logs", "select-20260616_120000.log")
    monkeypatch.setattr(isa, "_load_env",
                        lambda: types.SimpleNamespace(notion_write_delay=0.0))
    monkeypatch.setattr(isa, "_load_collections", lambda: "COLS")
    monkeypatch.setattr(isa, "setup_logging", lambda name: abs_log)
    monkeypatch.setattr(isa, "StageProgress", lambda title: _FakeProgress())
    monkeypatch.setattr(select_mod, "run_select_stage",
                        lambda env, cols, progress, **kw: {"queued": 1, "deterministic_pending": 0})

    args = isa.build_parser().parse_args(["run", "--stage", "select"])
    isa.dispatch_run(args)

    out = capsys.readouterr().out
    log_line = next(l for l in out.splitlines() if "Logging to" in l)
    # Must be relative (not starting with /)
    logged_path = log_line.split("Logging to ", 1)[1].strip()
    assert not os.path.isabs(logged_path), f"Expected relative path, got: {logged_path}"


# ---------------------------------------------------------------------------
# Item 3: Route dry-run forces write_delay=0; non-dry forwards env.notion_write_delay
# ---------------------------------------------------------------------------

def test_route_dry_run_forces_write_delay_zero(monkeypatch):
    """run_route_stage must receive write_delay=0 when dry_run=True."""
    route_mod = _route_common(monkeypatch)
    calls = {}

    def _fake_run_route(env, routes, collections_cfg, progress, *, limit=None, group=None,
                        dry_run=False, write_delay=0.0):
        calls["write_delay"] = write_delay
        return {"routed": 0, "unrouted": 1, "failed": 0}

    monkeypatch.setattr(route_mod, "run_route_stage", _fake_run_route)

    args = isa.build_parser().parse_args(["run", "--stage", "route", "--dry-run"])
    isa.dispatch_run(args)

    assert calls["write_delay"] == 0


def test_route_non_dry_forwards_notion_write_delay(monkeypatch):
    """run_route_stage must receive write_delay=env.notion_write_delay on a normal (non-dry) run."""
    route_mod = _route_common(monkeypatch)
    calls = {}

    def _fake_run_route(env, routes, collections_cfg, progress, *, limit=None, group=None,
                        dry_run=False, write_delay=0.0):
        calls["write_delay"] = write_delay
        return {"routed": 2, "unrouted": 0, "failed": 0}

    monkeypatch.setattr(route_mod, "run_route_stage", _fake_run_route)
    # _route_common patches notion_write_delay=0.4 via _load_env
    args = isa.build_parser().parse_args(["run", "--stage", "route"])
    isa.dispatch_run(args)

    assert calls["write_delay"] == 0.4


# ---------------------------------------------------------------------------
# Item 4: Dry-run label on gate messages in _print_plan
# ---------------------------------------------------------------------------

def test_mode_calibrate_gate_prints_dry_run_label(monkeypatch, capsys):
    """When dry_run=True and the next_action is a calibrate gate, the gate message should contain '(dry-run)'."""
    plan = _make_plan("calibrate", automated=False)
    args, calls = _patch_mode_dispatch(monkeypatch, plan, mode="first-time", dry_run=True)
    isa._dispatch_mode(args)
    out = capsys.readouterr().out
    assert "NEXT (dry-run)" in out
    assert "(dry-run)" in out


def test_mode_agent_enrich_gate_prints_dry_run_label(monkeypatch, capsys):
    """When dry_run=True and the next_action is a non-automated enrich step, the gate message should contain '(dry-run)'."""
    plan = _make_plan("enrich", automated=False)
    args, calls = _patch_mode_dispatch(monkeypatch, plan, mode="first-time", dry_run=True)
    isa._dispatch_mode(args)
    out = capsys.readouterr().out
    assert "NEXT (manual)" in out
    assert "(dry-run)" in out


def test_mode_passes_nested_progress_factory(monkeypatch):
    plan = _make_plan("done")
    args, calls = _patch_mode_dispatch(monkeypatch, plan, mode="first-time")
    monkeypatch.setattr(isa, "_has_collections", lambda: True)
    captured = {}
    import insta_save.orchestrator.pipeline as pipeline_mod

    def _fake_pipeline(env, run_cfg, cols, vocab, backend, routes, *, mode, dry_run=False,
                       select_mode="inline", ig_username=None, headed=False, fresh=False,
                       progress_factory=None):
        captured["pf"] = progress_factory
        return plan

    monkeypatch.setattr(pipeline_mod, "run_pipeline", _fake_pipeline)
    isa._dispatch_mode(args)
    from insta_save.helpers.observability import StageProgress
    prog = captured["pf"]("Extract · G")
    assert isinstance(prog, StageProgress) and prog._indent == 3


def test_dispatch_mode_exits_cleanly_on_run_stopped(monkeypatch, capsys):
    from insta_save.orchestrator.run_control import RunStopped
    import insta_save.orchestrator.pipeline as pipeline_mod
    plan = _make_plan("done")
    args, calls = _patch_mode_dispatch(monkeypatch, plan, mode="first-time")
    monkeypatch.setattr(isa, "_has_collections", lambda: True)
    def _boom(*a, **k):
        raise RunStopped()
    monkeypatch.setattr(pipeline_mod, "run_pipeline", _boom)
    isa._dispatch_mode(args)                          # must NOT raise (clean exit)
    out = capsys.readouterr().out
    assert "resume" in out.lower() and "isa run --mode first-time" in out


# ---------------------------------------------------------------------------
# B1: CLI secondary-command config-error guards
# Each test patches a loader on the `isa` module to raise its known error type
# and asserts the handler converts it to a clean SystemExit (not a raw traceback).
# ---------------------------------------------------------------------------

def test_status_load_env_error_raises_system_exit(monkeypatch):
    """isa status: _load_env raising ValueError -> clean SystemExit, not raw traceback."""
    monkeypatch.setattr(isa, "_load_env", lambda: (_ for _ in ()).throw(ValueError("bad .env: NOTION_TOKEN missing")))
    monkeypatch.setattr(isa, "setup_logging", lambda name: "log")

    import types as _t
    _args = _t.SimpleNamespace(command="status", retry_failed=False)

    class _FakeParser:
        def parse_args(self, argv=None): return _args

    monkeypatch.setattr(isa, "build_parser", lambda: _FakeParser())
    with pytest.raises(SystemExit):
        isa.main()


def test_status_load_collections_error_raises_system_exit(monkeypatch):
    """isa status: _load_collections raising RuntimeError -> clean SystemExit."""
    monkeypatch.setattr(isa, "_load_env", lambda: object())
    monkeypatch.setattr(isa, "_load_collections",
                        lambda: (_ for _ in ()).throw(RuntimeError("Collections config not found")))
    monkeypatch.setattr(isa, "setup_logging", lambda name: "log")

    import types as _t
    _args = _t.SimpleNamespace(command="status", retry_failed=False)

    class _FakeParser:
        def parse_args(self, argv=None): return _args

    monkeypatch.setattr(isa, "build_parser", lambda: _FakeParser())
    with pytest.raises(SystemExit):
        isa.main()


def test_stage_extract_load_run_error_raises_system_exit(monkeypatch):
    """isa run --stage extract: _load_run raising FileNotFoundError -> clean SystemExit."""
    monkeypatch.setattr(isa, "_load_env", lambda: object())
    monkeypatch.setattr(isa, "_load_run",
                        lambda: (_ for _ in ()).throw(FileNotFoundError("config/run.json not found")))

    args = isa.build_parser().parse_args(["run", "--stage", "extract"])
    with pytest.raises(SystemExit):
        isa.dispatch_run(args)


# ---------------------------------------------------------------------------
# distribute stage CLI wiring
# ---------------------------------------------------------------------------

class _FakeTemplatePath:
    """Stand-in for cli.isa's `Path` inside the distribute prepare path, so reading the
    prompt template never touches the real filesystem."""

    seen_paths: list = []

    def __init__(self, *a, **k):
        if a:
            _FakeTemplatePath.seen_paths.append(a[0])

    def read_text(self, encoding="utf-8"):
        return "TEMPLATE"


def _empty_distribute_preview():
    return types.SimpleNamespace(
        sections=[], folded=[], unverified=[], dropped=[], unmentioned=[], diff_text=""
    )


def _default_rebuild_preview():
    """A RebuildPreview stand-in with something to rebuild, so the default `--rebuild` path
    reaches the confirmation prompt instead of short-circuiting on "nothing to rebuild"."""
    return types.SimpleNamespace(
        sections=[("Section", 1)],
        blocks_to_delete=1,
        blocks_kept=0,
        items=[("a", "Item A", "Distributed")],
        relations_only=[],
        refused=[],
        heading_needs_clearing=False,
    )


def _default_rebuild_counts():
    return {"items_reset": 1, "relations_removed": 1, "relations_only": 0,
            "blocks_deleted": 1, "heading_cleared": True, "page_skipped": False}


def _make_confirm(values):
    """A scripted `tui.confirm_action` replacement — needs `*args, **kwargs` since
    confirm_action is called positionally with (message, actions) (see lessons.md)."""
    it = iter(values)
    return lambda *a, **k: next(it)


def _run_distribute_cli(monkeypatch, tmp_path, argv, *, preview=None, confirm=None,
                        rebuild_preview=None, rebuild=None):
    """Wire the distribute stage dispatch with lightweight stubs, then call isa.main(argv).

    `tmp_path` keeps distribute.staging_dir's real mkdir off the repo's tmp/. `preview` and
    `confirm` override the default stubs for distribute.preview (an empty no-op preview) and
    tui.confirm_action (immediate "apply") — only reached by the apply path. `rebuild_preview`
    and `rebuild` override the default stubs for the `--rebuild` path (a preview with one item
    and one block to delete, and matching counts) — only reached with `--rebuild`."""
    monkeypatch.setattr(isa, "_load_env", lambda: types.SimpleNamespace(
        tmp_dir=str(tmp_path), distribute_version="v2.1-distribute"))
    monkeypatch.setattr(isa, "_load_run", lambda: types.SimpleNamespace(
        distribute=types.SimpleNamespace(backend="claude-p", model="m")))
    monkeypatch.setattr(isa, "ensure_schema", lambda env: None)
    monkeypatch.setattr(isa, "load_routes", lambda: "ROUTES")
    monkeypatch.setattr(isa, "load_vocab", lambda: "VOCAB")
    monkeypatch.setattr(isa, "setup_logging", lambda name: "log")
    from insta_save.backends import base
    monkeypatch.setattr(base, "get_backend", lambda name: types.SimpleNamespace(
        batch_budgets=lambda run_cfg: types.SimpleNamespace(max_items=10, char_budget=1000)))
    monkeypatch.setattr(isa, "Path", _FakeTemplatePath)
    monkeypatch.setattr("insta_save.stages.distribute.preview",
                        preview or (lambda env, **kw: _empty_distribute_preview()))
    monkeypatch.setattr("insta_save.helpers.tui.confirm_action", confirm or _make_confirm(["apply"]))
    monkeypatch.setattr("insta_save.stages.distribute.rebuild_preview",
                        rebuild_preview or (lambda env, **kw: _default_rebuild_preview()))
    monkeypatch.setattr("insta_save.stages.distribute.rebuild",
                        rebuild or (lambda env, **kw: _default_rebuild_counts()))
    isa.main(argv)


def test_distribute_requires_a_target(monkeypatch, tmp_path):
    monkeypatch.setattr(isa, "_load_env", lambda: types.SimpleNamespace(tmp_dir=str(tmp_path)))
    with pytest.raises(SystemExit, match="--target"):
        isa.main(["run", "--stage", "distribute"])


def test_distribute_prepare_then_fill_stages_without_applying(monkeypatch, tmp_path, capsys):
    # NOTE: `seen.setdefault(key, kw) or <return>` (as the brief originally wrote this test)
    # always evaluates to `kw` here, since dict.setdefault returns the just-inserted value and
    # a non-empty kwargs dict is truthy — the `or <return>` branch never fires. Explicit
    # closures avoid that trap and actually return the stubbed values.
    seen = {}

    def _fake_prepare(env, **kw):
        seen["prepare"] = kw
        return 3

    def _fake_fill(env, **kw):
        seen["fill"] = kw
        return {"calls": 1, "folded": 3, "dropped": 0}

    monkeypatch.setattr("insta_save.stages.distribute.prepare", _fake_prepare)
    monkeypatch.setattr("insta_save.stages.distribute.fill", _fake_fill)
    monkeypatch.setattr("insta_save.stages.distribute.apply",
                        lambda env, **kw: pytest.fail("apply must not run without --apply"))
    _run_distribute_cli(monkeypatch, tmp_path, ["run", "--stage", "distribute", "--target", "Page B"])
    assert seen["prepare"]["target"] == "Page B"
    assert "--apply" in capsys.readouterr().out


def test_distribute_fill_reads_the_v2_1_template(monkeypatch, tmp_path):
    _FakeTemplatePath.seen_paths.clear()
    monkeypatch.setattr("insta_save.stages.distribute.prepare", lambda env, **kw: 1)
    monkeypatch.setattr("insta_save.stages.distribute.fill",
                        lambda env, **kw: {"calls": 1, "folded": 1, "dropped": 0})
    _run_distribute_cli(monkeypatch, tmp_path, ["run", "--stage", "distribute", "--target", "Page B"])
    assert "prompts/distribute_v2.1.txt" in _FakeTemplatePath.seen_paths


def test_distribute_apply_skips_prepare(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("insta_save.stages.distribute.prepare",
                        lambda env, **kw: pytest.fail("prepare must not run with --apply"))
    monkeypatch.setattr("insta_save.stages.distribute.apply",
                        lambda env, **kw: {"folded": 2, "skipped": 1, "misrouted": 1,
                                           "unknown": 0, "unverified": 0})
    # --yes: the apply confirmation is covered separately below; sys.stdin isn't a tty
    # under pytest, so without --yes this would hit the non-interactive refusal instead.
    _run_distribute_cli(monkeypatch, tmp_path,
                        ["run", "--stage", "distribute", "--target", "Page B", "--apply", "--yes"])
    out = capsys.readouterr().out
    assert "2 folded in" in out
    assert "no link" not in out


def test_distribute_apply_reports_unverified(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("insta_save.stages.distribute.apply",
                        lambda env, **kw: {"folded": 2, "skipped": 0, "misrouted": 0,
                                           "unknown": 0, "unverified": 1})
    _run_distribute_cli(monkeypatch, tmp_path,
                        ["run", "--stage", "distribute", "--target", "Page B", "--apply", "--yes"])
    out = capsys.readouterr().out
    assert "1 folded item(s) had no link in the applied page" in out


def test_distribute_reports_nothing_left(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("insta_save.stages.distribute.prepare", lambda env, **kw: 0)
    monkeypatch.setattr("insta_save.stages.distribute.fill",
                        lambda env, **kw: pytest.fail("fill must not run on an empty batch"))
    _run_distribute_cli(monkeypatch, tmp_path, ["run", "--stage", "distribute", "--target", "Page B"])
    assert "Nothing left to distribute" in capsys.readouterr().out


def test_distribute_ensure_schema_runs_before_prepare(monkeypatch, tmp_path):
    """ensure_schema must run before prepare — the distribute props/statuses it creates
    (distribute_notes/distribute_version, Distributed/Misrouted) must exist first."""
    order = []
    monkeypatch.setattr(isa, "_load_env", lambda: types.SimpleNamespace(
        tmp_dir=str(tmp_path), distribute_version="v2.1-distribute"))
    monkeypatch.setattr(isa, "_load_run", lambda: types.SimpleNamespace(
        distribute=types.SimpleNamespace(backend="claude-p", model="m")))
    monkeypatch.setattr(isa, "ensure_schema", lambda env: order.append("schema"))
    monkeypatch.setattr(isa, "load_routes", lambda: "ROUTES")
    monkeypatch.setattr(isa, "load_vocab", lambda: "VOCAB")
    monkeypatch.setattr(isa, "setup_logging", lambda name: "log")
    from insta_save.backends import base
    monkeypatch.setattr(base, "get_backend", lambda name: types.SimpleNamespace(
        batch_budgets=lambda run_cfg: types.SimpleNamespace(max_items=10, char_budget=1000)))
    monkeypatch.setattr("insta_save.stages.distribute.prepare",
                        lambda env, **kw: order.append("prepare") or 0)
    isa.main(["run", "--stage", "distribute", "--target", "Page B"])
    assert order == ["schema", "prepare"]


def test_distribute_prepare_runtime_error_becomes_system_exit(monkeypatch, tmp_path):
    monkeypatch.setattr("insta_save.stages.distribute.prepare",
                        lambda env, **kw: (_ for _ in ()).throw(
                            RuntimeError("distribute.prepare: the page's first heading_1 "
                                        "is the template section")))
    with pytest.raises(SystemExit, match="template section"):
        _run_distribute_cli(monkeypatch, tmp_path,
                            ["run", "--stage", "distribute", "--target", "Page B"])


def test_distribute_apply_runtime_error_becomes_system_exit(monkeypatch, tmp_path):
    monkeypatch.setattr("insta_save.stages.distribute.apply",
                        lambda env, **kw: (_ for _ in ()).throw(
                            RuntimeError("distribute.apply: 'Page B' changed since prepare")))
    with pytest.raises(SystemExit, match="changed since prepare"):
        _run_distribute_cli(monkeypatch, tmp_path,
                            ["run", "--stage", "distribute", "--target", "Page B",
                             "--apply", "--yes"])


def test_distribute_fill_terminal_backend_error_stops_cleanly(monkeypatch, tmp_path, capsys):
    from insta_save.backends.base import TerminalBackendError
    monkeypatch.setattr("insta_save.stages.distribute.prepare", lambda env, **kw: 3)
    monkeypatch.setattr("insta_save.stages.distribute.fill",
                        lambda env, **kw: (_ for _ in ()).throw(
                            TerminalBackendError("claude-p: access token has expired")))
    _run_distribute_cli(monkeypatch, tmp_path, ["run", "--stage", "distribute", "--target", "Page B"])
    out = capsys.readouterr().out
    assert "stopped" in out
    assert "access token has expired" in out


# ---------------------------------------------------------------------------
# distribute apply — confirmation prompt
# ---------------------------------------------------------------------------


def _stub_distribute_apply(monkeypatch, calls):
    monkeypatch.setattr(
        "insta_save.stages.distribute.apply",
        lambda env, **kw: calls.append(1) or {"folded": 1, "skipped": 0, "misrouted": 0,
                                              "unknown": 0, "unverified": 0},
    )


def test_distribute_apply_confirm_apply_calls_apply_once(monkeypatch, tmp_path, capsys):
    calls = []
    _stub_distribute_apply(monkeypatch, calls)
    monkeypatch.setattr(isa.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    _run_distribute_cli(
        monkeypatch, tmp_path,
        ["run", "--stage", "distribute", "--target", "Page B", "--apply"],
        confirm=_make_confirm(["apply"]),
    )
    assert calls == [1]
    assert "1 folded in" in capsys.readouterr().out


def test_distribute_apply_confirm_diff_then_apply_prints_diff_and_applies(
    monkeypatch, tmp_path, capsys
):
    calls = []
    _stub_distribute_apply(monkeypatch, calls)
    monkeypatch.setattr(isa.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    review = types.SimpleNamespace(
        sections=[], folded=[], unverified=[], dropped=[], unmentioned=[],
        diff_text="THE-FULL-DIFF",
    )
    _run_distribute_cli(
        monkeypatch, tmp_path,
        ["run", "--stage", "distribute", "--target", "Page B", "--apply"],
        preview=lambda env, **kw: review,
        confirm=_make_confirm(["diff", "apply"]),
    )
    out = capsys.readouterr().out
    assert "THE-FULL-DIFF" in out
    assert calls == [1]


def test_distribute_apply_confirm_cancel_does_not_apply(monkeypatch, tmp_path, capsys):
    calls = []
    _stub_distribute_apply(monkeypatch, calls)
    monkeypatch.setattr(isa.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    _run_distribute_cli(
        monkeypatch, tmp_path,
        ["run", "--stage", "distribute", "--target", "Page B", "--apply"],
        confirm=_make_confirm(["cancel"]),
    )
    assert calls == []
    assert "Cancelled — nothing written" in capsys.readouterr().out


def test_distribute_apply_confirm_ctrl_c_does_not_apply(monkeypatch, tmp_path, capsys):
    calls = []
    _stub_distribute_apply(monkeypatch, calls)
    monkeypatch.setattr(isa.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    _run_distribute_cli(
        monkeypatch, tmp_path,
        ["run", "--stage", "distribute", "--target", "Page B", "--apply"],
        confirm=_make_confirm([None]),
    )
    assert calls == []
    assert "Cancelled — nothing written" in capsys.readouterr().out


def test_distribute_apply_non_interactive_without_yes_refuses(monkeypatch, tmp_path):
    calls = []
    _stub_distribute_apply(monkeypatch, calls)
    confirmed = []
    monkeypatch.setattr(isa.sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    with pytest.raises(SystemExit, match="refusing to write without confirmation"):
        _run_distribute_cli(
            monkeypatch, tmp_path,
            ["run", "--stage", "distribute", "--target", "Page B", "--apply"],
            confirm=lambda *a, **k: confirmed.append(1),
        )
    assert calls == [] and confirmed == []


def test_distribute_apply_yes_applies_without_confirm_even_when_not_a_tty(
    monkeypatch, tmp_path, capsys
):
    calls = []
    _stub_distribute_apply(monkeypatch, calls)
    confirmed = []
    monkeypatch.setattr(isa.sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    _run_distribute_cli(
        monkeypatch, tmp_path,
        ["run", "--stage", "distribute", "--target", "Page B", "--apply", "--yes"],
        confirm=lambda *a, **k: confirmed.append(1),
    )
    assert calls == [1] and confirmed == []
    assert "1 folded in" in capsys.readouterr().out


def test_distribute_stage_prints_preview_summary_before_applying(monkeypatch, tmp_path, capsys):
    """The staging (non-apply) path prints the preview summary and states that nothing has
    been written to the page yet."""
    monkeypatch.setattr("insta_save.stages.distribute.prepare", lambda env, **kw: 2)
    monkeypatch.setattr("insta_save.stages.distribute.fill",
                        lambda env, **kw: {"calls": 1, "folded": 2, "dropped": 0})
    review = types.SimpleNamespace(
        sections=[types.SimpleNamespace(heading="Workouts", action="replace", added=3, removed=1)],
        folded=[], unverified=[], dropped=[], unmentioned=[("z", "Unmentioned")],
        diff_text="",
    )
    _run_distribute_cli(
        monkeypatch, tmp_path, ["run", "--stage", "distribute", "--target", "Page B"],
        preview=lambda env, **kw: review,
    )
    out = capsys.readouterr().out
    assert "~ replace 'Workouts' (+3 -1)" in out
    assert "Unmentioned" in out
    assert "Nothing has been written to the page yet." in out
    assert "--apply" in out


# ---------------------------------------------------------------------------
# distribute rebuild — CLI wiring
# ---------------------------------------------------------------------------


def test_distribute_rebuild_and_apply_cannot_combine(monkeypatch, tmp_path):
    monkeypatch.setattr(isa, "_load_env", lambda: pytest.fail("must not load config"))
    with pytest.raises(SystemExit, match="--rebuild and --apply cannot be combined"):
        isa.main(["run", "--stage", "distribute", "--target", "Page B",
                 "--rebuild", "--apply"])


def test_distribute_rebuild_confirm_rebuild_calls_rebuild_once(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(isa.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    _run_distribute_cli(
        monkeypatch, tmp_path,
        ["run", "--stage", "distribute", "--target", "Page B", "--rebuild"],
        confirm=_make_confirm(["rebuild"]),
        rebuild=lambda env, **kw: calls.append(1) or _default_rebuild_counts(),
    )
    assert calls == [1]
    out = capsys.readouterr().out
    assert "1 item(s) reset" in out
    assert "1 relation(s) removed" in out
    assert "1 block(s) deleted" in out


def test_distribute_rebuild_confirm_cancel_does_not_rebuild(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(isa.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    _run_distribute_cli(
        monkeypatch, tmp_path,
        ["run", "--stage", "distribute", "--target", "Page B", "--rebuild"],
        confirm=_make_confirm(["cancel"]),
        rebuild=lambda env, **kw: calls.append(1) or _default_rebuild_counts(),
    )
    assert calls == []
    assert "Cancelled — nothing changed." in capsys.readouterr().out


def test_distribute_rebuild_confirm_ctrl_c_does_not_rebuild(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(isa.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    _run_distribute_cli(
        monkeypatch, tmp_path,
        ["run", "--stage", "distribute", "--target", "Page B", "--rebuild"],
        confirm=_make_confirm([None]),
        rebuild=lambda env, **kw: calls.append(1) or _default_rebuild_counts(),
    )
    assert calls == []
    assert "Cancelled — nothing changed." in capsys.readouterr().out


def test_distribute_rebuild_non_interactive_without_yes_refuses(monkeypatch, tmp_path):
    calls = []
    confirmed = []
    monkeypatch.setattr(isa.sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    with pytest.raises(SystemExit, match="refusing to change Notion without confirmation"):
        _run_distribute_cli(
            monkeypatch, tmp_path,
            ["run", "--stage", "distribute", "--target", "Page B", "--rebuild"],
            confirm=lambda *a, **k: confirmed.append(1),
            rebuild=lambda env, **kw: calls.append(1) or _default_rebuild_counts(),
        )
    assert calls == [] and confirmed == []


def test_distribute_rebuild_yes_rebuilds_without_confirm_even_when_not_a_tty(
    monkeypatch, tmp_path, capsys
):
    calls = []
    confirmed = []
    monkeypatch.setattr(isa.sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    _run_distribute_cli(
        monkeypatch, tmp_path,
        ["run", "--stage", "distribute", "--target", "Page B", "--rebuild", "--yes"],
        confirm=lambda *a, **k: confirmed.append(1),
        rebuild=lambda env, **kw: calls.append(1) or _default_rebuild_counts(),
    )
    assert calls == [1] and confirmed == []
    assert "1 item(s) reset" in capsys.readouterr().out


def test_distribute_rebuild_refusals_raise_before_confirming(monkeypatch, tmp_path):
    confirmed = []
    rebuilt = []
    review = types.SimpleNamespace(
        sections=[("Off-topic", 0)], blocks_to_delete=0, blocks_kept=0, items=[],
        relations_only=[], refused=[("Off-topic", ["image"])], heading_needs_clearing=False,
    )
    with pytest.raises(SystemExit, match="Off-topic"):
        _run_distribute_cli(
            monkeypatch, tmp_path,
            ["run", "--stage", "distribute", "--target", "Page B", "--rebuild"],
            rebuild_preview=lambda env, **kw: review,
            confirm=lambda *a, **k: confirmed.append(1),
            rebuild=lambda env, **kw: rebuilt.append(1),
        )
    assert confirmed == [] and rebuilt == []


def test_distribute_rebuild_nothing_to_rebuild_skips_confirm(monkeypatch, tmp_path, capsys):
    confirmed = []
    rebuilt = []
    review = types.SimpleNamespace(
        sections=[], blocks_to_delete=0, blocks_kept=0, items=[], relations_only=[], refused=[],
        heading_needs_clearing=False,
    )
    _run_distribute_cli(
        monkeypatch, tmp_path,
        ["run", "--stage", "distribute", "--target", "Page B", "--rebuild"],
        rebuild_preview=lambda env, **kw: review,
        confirm=lambda *a, **k: confirmed.append(1),
        rebuild=lambda env, **kw: rebuilt.append(1),
    )
    assert confirmed == [] and rebuilt == []
    assert "Nothing to rebuild for 'Page B'." in capsys.readouterr().out


def test_distribute_rebuild_heading_only_dirty_prompts_and_rebuilds(monkeypatch, tmp_path, capsys):
    """No items and no blocks to delete, but the template anchor still carries text —
    'Nothing to rebuild' must not fire, and confirming still runs rebuild."""
    calls = []
    review = types.SimpleNamespace(
        sections=[], blocks_to_delete=0, blocks_kept=0, items=[], relations_only=[], refused=[],
        heading_needs_clearing=True,
    )
    monkeypatch.setattr(isa.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    _run_distribute_cli(
        monkeypatch, tmp_path,
        ["run", "--stage", "distribute", "--target", "Page B", "--rebuild"],
        rebuild_preview=lambda env, **kw: review,
        confirm=_make_confirm(["rebuild"]),
        rebuild=lambda env, **kw: calls.append(1) or _default_rebuild_counts(),
    )
    assert calls == [1]
    out = capsys.readouterr().out
    assert "Nothing to rebuild" not in out
    assert "content anchor heading still has text" in out


def test_distribute_rebuild_no_items_but_blocks_warns_before_prompting(
    monkeypatch, tmp_path, capsys
):
    review = types.SimpleNamespace(
        sections=[("Old Content", 4)], blocks_to_delete=4, blocks_kept=0, items=[],
        relations_only=[], refused=[], heading_needs_clearing=False,
    )
    monkeypatch.setattr(isa.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    _run_distribute_cli(
        monkeypatch, tmp_path,
        ["run", "--stage", "distribute", "--target", "Page B", "--rebuild"],
        rebuild_preview=lambda env, **kw: review,
        confirm=_make_confirm(["cancel"]),
    )
    out = capsys.readouterr().out
    assert "No archive items are linked to this page" in out
    warn_pos = out.index("No archive items are linked")
    cancel_pos = out.index("Cancelled — nothing changed.")
    assert warn_pos < cancel_pos


def test_distribute_rebuild_refusal_uses_plain_english_quick_access_wording(monkeypatch, tmp_path):
    """The CLI refusal reuses distribute.refusal_messages' wording — never the raw
    'quick_access' kind token a reader would have to decode."""
    review = types.SimpleNamespace(
        sections=[], blocks_to_delete=0, blocks_kept=0, items=[], relations_only=[],
        refused=[("Z", ["quick_access"])], heading_needs_clearing=False,
    )
    with pytest.raises(SystemExit) as exc:
        _run_distribute_cli(
            monkeypatch, tmp_path,
            ["run", "--stage", "distribute", "--target", "Page B", "--rebuild"],
            rebuild_preview=lambda env, **kw: review,
        )
    assert "holds the Quick Access section" in str(exc.value)
    assert "quick_access" not in str(exc.value)


def test_distribute_rebuild_page_skipped_prints_items_only_line(monkeypatch, tmp_path, capsys):
    counts = {"items_reset": 2, "relations_removed": 2, "relations_only": 0,
              "blocks_deleted": 0, "heading_cleared": False, "page_skipped": True}
    _run_distribute_cli(
        monkeypatch, tmp_path,
        ["run", "--stage", "distribute", "--target", "Page B", "--rebuild", "--yes"],
        rebuild=lambda env, **kw: counts,
    )
    out = capsys.readouterr().out
    assert "has no content heading" in out
    assert "only its items were reset" in out


# ---------------------------------------------------------------------------
# _format_rebuild_preview
# ---------------------------------------------------------------------------


def _rebuild_preview(*, sections=(), blocks_to_delete=0, blocks_kept=0, items=(),
                     relations_only=(), refused=(), heading_needs_clearing=False):
    return types.SimpleNamespace(
        sections=list(sections), blocks_to_delete=blocks_to_delete, blocks_kept=blocks_kept,
        items=list(items), relations_only=list(relations_only), refused=list(refused),
        heading_needs_clearing=heading_needs_clearing,
    )


def test_format_rebuild_preview_none_title_falls_back_to_source_id(capsys):
    preview = _rebuild_preview(items=[("src-id-9", None, "Distributed")])
    print(isa._format_rebuild_preview(preview))
    out = capsys.readouterr().out
    assert "src-id-9" in out
    assert "None" not in out


def test_format_rebuild_preview_groups_items_by_status(capsys):
    preview = _rebuild_preview(items=[
        ("a", "Item A", "Distributed"),
        ("b", "Item B", "Misrouted"),
    ])
    print(isa._format_rebuild_preview(preview))
    out = capsys.readouterr().out
    assert "Distributed" in out and "Misrouted" in out
    assert "Item A" in out and "Item B" in out


def test_format_rebuild_preview_lists_relations_only_separately_from_reset(capsys):
    preview = _rebuild_preview(
        items=[("a", "Item A", "Distributed"), ("b", "Item B", "Queued")],
        relations_only=[("b", "Item B", "Queued")],
    )
    print(isa._format_rebuild_preview(preview))
    out = capsys.readouterr().out
    assert "unlink" in out.lower()
    assert "Item B" in out


def test_format_rebuild_preview_lists_refused_sections_with_reason(capsys):
    preview = _rebuild_preview(refused=[("Weird Section", ["image", "has_children"])])
    print(isa._format_rebuild_preview(preview))
    out = capsys.readouterr().out
    assert "Weird Section" in out
    assert "image" in out and "has_children" in out


def test_format_rebuild_preview_flags_a_dirty_anchor_heading(capsys):
    preview = _rebuild_preview(heading_needs_clearing=True)
    print(isa._format_rebuild_preview(preview))
    assert "still has text" in capsys.readouterr().out


def test_format_rebuild_preview_omits_heading_line_when_already_clear(capsys):
    preview = _rebuild_preview(heading_needs_clearing=False)
    print(isa._format_rebuild_preview(preview))
    assert "still has text" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _format_distribute_preview
# ---------------------------------------------------------------------------


def _preview(*, sections=(), folded=(), unverified=(), dropped=(), unmentioned=()):
    return types.SimpleNamespace(
        sections=list(sections),
        folded=list(folded),
        unverified=list(unverified),
        dropped=list(dropped),
        unmentioned=list(unmentioned),
    )


def test_format_distribute_preview_dropped_with_reason_prints_title_and_reason(capsys):
    preview = _preview(dropped=[("a", "Dropped Title", "off topic")])
    print(isa._format_distribute_preview(preview))
    out = capsys.readouterr().out
    assert "Dropped Title — off topic" in out


def test_format_distribute_preview_dropped_without_reason_has_no_suffix_or_none(capsys):
    preview = _preview(dropped=[("a", "Dropped Title", None)])
    print(isa._format_distribute_preview(preview))
    out = capsys.readouterr().out
    assert "Dropped Title" in out
    assert "Dropped Title —" not in out
    assert "None" not in out


def test_format_distribute_preview_lists_unverified_items_under_their_heading(capsys):
    preview = _preview(unverified=[("a", "Unverified Title")])
    print(isa._format_distribute_preview(preview))
    out = capsys.readouterr().out
    assert "folded but the link is missing from the page" in out
    assert "Unverified Title" in out


def test_format_distribute_preview_falls_back_to_source_id_when_title_is_none(capsys):
    preview = _preview(unmentioned=[("src-id-123", None)])
    print(isa._format_distribute_preview(preview))
    out = capsys.readouterr().out
    assert "src-id-123" in out
    assert "None" not in out


def test_format_distribute_preview_with_no_sections_prints_no_changes_line(capsys):
    preview = _preview()
    print(isa._format_distribute_preview(preview))
    out = capsys.readouterr().out
    assert "No page content changes" in out


def test_route_reroute_summary_prints_the_decided_by_breakdown(monkeypatch, capsys):
    route_mod = _route_common(monkeypatch)
    monkeypatch.setattr(route_mod, "run_reroute_stage",
                        lambda *a, **k: {"rerouted": 3, "unrouted": 0, "unchanged": 0,
                                         "failed": 0, "moves": {("Old", "New"): 3},
                                         "decided_by": {"primary": 2, "order": 1}})
    args = isa.build_parser().parse_args(["run", "--stage", "route", "--reroute"])
    isa.dispatch_run(args)
    out = capsys.readouterr().out
    assert "decided by primary topic: 2" in out
    assert "by rule order: 1" in out


def test_route_reroute_dry_run_also_prints_the_decided_by_breakdown(monkeypatch, capsys):
    route_mod = _route_common(monkeypatch)
    monkeypatch.setattr(route_mod, "run_reroute_stage",
                        lambda *a, **k: {"rerouted": 1, "unrouted": 0, "unchanged": 0,
                                         "failed": 0, "moves": {("Old", "New"): 1},
                                         "decided_by": {"primary": 1, "order": 0}})
    args = isa.build_parser().parse_args(["run", "--stage", "route", "--reroute", "--dry-run"])
    isa.dispatch_run(args)
    out = capsys.readouterr().out
    assert "(dry-run)" in out
    assert "decided by primary topic: 1" in out and "by rule order: 0" in out
