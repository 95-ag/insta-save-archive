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
        "limit": 5, "reextract": False, "reenrich": False, "retry_failed": False})())

    assert calls["schema"] == "ENV"
    assert calls["stage"][0] == "ENV" and calls["stage"][1] == "EX"
    assert calls["stage"][2]["group"] == "Hustling" and calls["stage"][2]["limit"] == 5


def test_run_unimplemented_stage_raises():
    with __import__("pytest").raises(SystemExit):
        isa.dispatch_run(type("A", (), {
            "mode": "incremental", "stage": "route", "group": None,
            "limit": None, "reextract": False, "reenrich": False, "retry_failed": False,
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
    monkeypatch.setattr(isa, "setup_logging", lambda name: "log")
    monkeypatch.setattr(isa, "StageProgress", lambda title: _FakeProgress())
    def _fake_apply(env, *, vocab, model, progress=None):
        calls["apply"] = (vocab, model, progress is not None)
        return {"written": 1, "failed": 0}
    monkeypatch.setattr(isa.enrich, "apply", _fake_apply)
    args = isa.build_parser().parse_args(["run", "--stage", "enrich", "--apply"])
    isa.dispatch_run(args)
    assert calls["apply"] == ("VOCAB", "claude-sonnet", True)  # progress passed through


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
    monkeypatch.setattr(isa, "_load_env", lambda: "ENV")
    monkeypatch.setattr(isa, "_load_run", lambda: run_obj)
    monkeypatch.setattr(isa, "_load_collections", lambda: "COLS")
    monkeypatch.setattr(isa, "ensure_schema", lambda env: None)
    monkeypatch.setattr(isa, "setup_logging", lambda name: "logpath")
    _stub_progress(monkeypatch)


def _det_args(**kw):
    base = {"mode": "incremental", "stage": "deterministic", "group": None, "limit": None,
            "prepare": False, "apply": False, "reextract": False, "reenrich": False,
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
    assert seen["kw"] == {"limit": 5, "group": "Lifestyle"}


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
