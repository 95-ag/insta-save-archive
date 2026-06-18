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
            "confirm_removed": None, "apply": False, "prepare": False,
            "calibrate_limit": 20})())


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
        by_tag={}, by_collection={}, by_group={}))
    monkeypatch.setattr(isa, "setup_logging", lambda name: "logs/run-20260101_000000.log")
    monkeypatch.setattr(isa, "preflight", lambda env, run_cfg, stages: None)
    monkeypatch.setattr(isa, "build_status",
                        lambda env, cols: [{"group": "TOTAL", "remaining": 5}])

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
