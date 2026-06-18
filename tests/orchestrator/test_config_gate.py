import json
import pytest
from insta_save.orchestrator import config_gate
from insta_save.helpers import tui
from insta_save.config.run import load_run_config


def _seed(p, backend="cowork", model="m", effort="medium"):
    p.write_text(json.dumps({
        "mode": "first-time",
        "enrich": {"backend": backend, "model": model, "effort": effort, "api_mode": "sync"},
        "extract": {"transcript": {"model": "base", "vad": True}, "ocr": {"mode": "rapidocr"}},
        "batch": {"max_items": 15, "max_char_budget": 80000, "max_image_tokens": 120000},
        "guardrails": {"max_items_per_run": None, "max_spend_usd": None},
        "deterministic": {"title_mode": "template"}, "output_language": "english",
    }), encoding="utf-8")


def _script(monkeypatch, *, selects, confirms, others=None):
    sel = iter(selects)        # values returned by tui.select (mode + enum fields)
    conf = iter(confirms)      # values returned by tui.confirm_action
    oth = iter(others or [])   # values returned by tui.select_or_other
    monkeypatch.setattr(tui, "select", lambda *a, **k: next(sel))
    monkeypatch.setattr(tui, "confirm_action", lambda *a, **k: next(conf))
    monkeypatch.setattr(tui, "select_or_other", lambda *a, **k: next(oth))


def test_inline_sets_fields_and_confirms(tmp_path, monkeypatch):
    p = tmp_path / "run.json"; _seed(p, backend="cowork", model="old", effort="low")
    # selects: mode=inline, backend=claude-p, effort=high   (model/output_language via select_or_other)
    _script(monkeypatch, selects=["inline", "claude-p", "high"],
            others=["claude-sonnet", "english"], confirms=["proceed"])
    out = config_gate.run_config_gate(load_run_config(p), path=p, select_mode="inline")
    assert out.enrich.backend == "claude-p" and out.enrich.model == "claude-sonnet"
    assert out.enrich.effort == "high"
    assert json.loads(p.read_text())["deterministic"]["title_mode"] == "template"  # untouched


def test_api_backend_prompts_api_mode(tmp_path, monkeypatch):
    p = tmp_path / "run.json"; _seed(p)
    # selects: mode, backend=api, effort, api_mode=batches
    _script(monkeypatch, selects=["inline", "api", "medium", "batches"],
            others=["claude-sonnet", "english"], confirms=["proceed"])
    out = config_gate.run_config_gate(load_run_config(p), path=p, select_mode="inline")
    assert out.enrich.backend == "api" and out.enrich.api_mode == "batches"


def test_go_back_then_proceed(tmp_path, monkeypatch):
    p = tmp_path / "run.json"; _seed(p)
    # first pass picks claude-p, confirm=back → re-pick local, confirm=proceed
    _script(monkeypatch, selects=["inline", "claude-p", "medium", "local", "medium"],
            others=["claude-sonnet", "english", "qwen2.5:7b", "english"],
            confirms=["back", "proceed"])
    out = config_gate.run_config_gate(load_run_config(p), path=p, select_mode="inline")
    assert out.enrich.backend == "local"


def test_abort_raises(tmp_path, monkeypatch):
    p = tmp_path / "run.json"; _seed(p)
    _script(monkeypatch, selects=["inline", "claude-p", "medium"],
            others=["claude-sonnet", "english"], confirms=["abort"])
    with pytest.raises(SystemExit):
        config_gate.run_config_gate(load_run_config(p), path=p, select_mode="inline")


def test_editor_mode(tmp_path, monkeypatch):
    p = tmp_path / "run.json"; _seed(p, backend="cowork")
    def fake_editor(path):
        d = json.loads(p.read_text()); d["enrich"]["backend"] = "claude-p"; p.write_text(json.dumps(d))
    monkeypatch.setattr(config_gate, "_editor_edit", fake_editor)
    _script(monkeypatch, selects=["editor"], others=[], confirms=["proceed"])
    out = config_gate.run_config_gate(load_run_config(p), path=p, select_mode="editor")
    assert out.enrich.backend == "claude-p"


def test_ensure_run_json_seeds_claude_p(tmp_path):
    p = tmp_path / "run.json"; config_gate.ensure_run_json(p)
    assert json.loads(p.read_text())["enrich"]["backend"] == "claude-p"


def test_ensure_run_json_seeds_claude_p_when_absent(tmp_path):
    p = tmp_path / "run.json"
    config_gate.ensure_run_json(p)
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8"))["enrich"]["backend"] == "claude-p"


def test_ensure_run_json_noop_when_present(tmp_path):
    p = tmp_path / "run.json"
    _seed(p, backend="api")
    config_gate.ensure_run_json(p)
    assert json.loads(p.read_text(encoding="utf-8"))["enrich"]["backend"] == "api"
