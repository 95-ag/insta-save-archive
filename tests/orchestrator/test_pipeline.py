"""Tests for run_pipeline: discover->ingest->select front-fold, then per-group loop."""
from types import SimpleNamespace
import insta_save.orchestrator.pipeline as pl


def _env():
    return SimpleNamespace(notion_write_delay=0, tmp_dir="/tmp", ig_username="u")


def _done_plan():
    return SimpleNamespace(done=True, steps=[], next_action=None)


def test_pipeline_front_folds_in_order(monkeypatch):
    calls = []
    monkeypatch.setattr(pl, "run_discover", lambda *a, **k: calls.append("discover") or {})
    monkeypatch.setattr(pl, "run_ingest", lambda *a, **k: calls.append("ingest") or {})
    monkeypatch.setattr(pl, "run_select_stage", lambda *a, **k: calls.append("select") or {})
    monkeypatch.setattr(pl, "run_first_time", lambda *a, **k: calls.append("loop") or _done_plan())
    monkeypatch.setattr(pl, "_reload_collections", lambda: object())
    pl.run_pipeline(_env(), object(), object(), object(), object(), object(),
                    mode="first-time", dry_run=False)
    assert calls == ["discover", "ingest", "select", "loop"]


def test_pipeline_dry_run_skips_population(monkeypatch):
    calls = []
    for fn in ("run_discover", "run_ingest", "run_select_stage"):
        monkeypatch.setattr(pl, fn, lambda *a, fn=fn, **k: calls.append(fn))
    monkeypatch.setattr(pl, "run_first_time", lambda *a, **k: _done_plan())
    pl.run_pipeline(_env(), object(), object(), object(), object(), object(),
                    mode="first-time", dry_run=True)
    assert calls == []   # dry-run previews the plan only


def test_pipeline_incremental_calls_run_incremental(monkeypatch):
    calls = []
    monkeypatch.setattr(pl, "run_discover", lambda *a, **k: {})
    monkeypatch.setattr(pl, "run_ingest", lambda *a, **k: {})
    monkeypatch.setattr(pl, "run_select_stage", lambda *a, **k: {})
    monkeypatch.setattr(pl, "_reload_collections", lambda: object())
    monkeypatch.setattr(pl, "run_incremental",
                        lambda *a, **k: calls.append("incremental") or _done_plan())
    pl.run_pipeline(_env(), object(), object(), object(), object(), object(),
                    mode="incremental", dry_run=False)
    assert "incremental" in calls


def test_pipeline_incremental_dry_run_skips_population(monkeypatch):
    calls = []
    for fn in ("run_discover", "run_ingest", "run_select_stage"):
        monkeypatch.setattr(pl, fn, lambda *a, fn=fn, **k: calls.append(fn))
    monkeypatch.setattr(pl, "run_incremental", lambda *a, **k: _done_plan())
    pl.run_pipeline(_env(), object(), object(), object(), object(), object(),
                    mode="incremental", dry_run=True)
    assert calls == []


def test_pipeline_first_time_uses_fresh_discover(monkeypatch):
    """first-time mode passes fresh=True to run_discover."""
    seen = {}
    monkeypatch.setattr(pl, "run_discover", lambda *a, **k: seen.update(k) or {})
    monkeypatch.setattr(pl, "run_ingest", lambda *a, **k: {})
    monkeypatch.setattr(pl, "run_select_stage", lambda *a, **k: {})
    monkeypatch.setattr(pl, "_reload_collections", lambda: object())
    monkeypatch.setattr(pl, "run_first_time", lambda *a, **k: _done_plan())
    pl.run_pipeline(_env(), object(), object(), object(), object(), object(),
                    mode="first-time", dry_run=False)
    assert seen.get("fresh") is True


def test_pipeline_incremental_uses_non_fresh_discover(monkeypatch):
    """incremental mode passes fresh=False to run_discover."""
    seen = {}
    monkeypatch.setattr(pl, "run_discover", lambda *a, **k: seen.update(k) or {})
    monkeypatch.setattr(pl, "run_ingest", lambda *a, **k: {})
    monkeypatch.setattr(pl, "run_select_stage", lambda *a, **k: {})
    monkeypatch.setattr(pl, "_reload_collections", lambda: object())
    monkeypatch.setattr(pl, "run_incremental", lambda *a, **k: _done_plan())
    pl.run_pipeline(_env(), object(), object(), object(), object(), object(),
                    mode="incremental", dry_run=False)
    assert seen.get("fresh") is False


def test_pipeline_passes_select_mode(monkeypatch):
    """select_mode kwarg is forwarded to run_discover."""
    seen = {}
    monkeypatch.setattr(pl, "run_discover", lambda *a, **k: seen.update(k) or {})
    monkeypatch.setattr(pl, "run_ingest", lambda *a, **k: {})
    monkeypatch.setattr(pl, "run_select_stage", lambda *a, **k: {})
    monkeypatch.setattr(pl, "_reload_collections", lambda: object())
    monkeypatch.setattr(pl, "run_first_time", lambda *a, **k: _done_plan())
    pl.run_pipeline(_env(), object(), object(), object(), object(), object(),
                    mode="first-time", dry_run=False, select_mode="editor")
    assert seen.get("select_mode") == "editor"
