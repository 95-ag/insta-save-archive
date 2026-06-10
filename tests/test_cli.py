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
            "mode": "incremental", "stage": "enrich", "group": None,
            "limit": None, "reextract": False, "reenrich": False, "retry_failed": False})())
