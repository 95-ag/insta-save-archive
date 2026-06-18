import json
from pathlib import Path
import pytest
from insta_save.orchestrator import config_gate


def _seed(path, backend="cowork", model="m", effort="medium"):
    path.write_text(json.dumps({
        "mode": "first-time",
        "enrich": {"backend": backend, "model": model, "effort": effort, "api_mode": "sync"},
        "extract": {"transcript": {"model": "base", "vad": True}, "ocr": {"mode": "rapidocr"}},
        "batch": {"max_items": 15, "max_char_budget": 80000, "max_image_tokens": 120000},
        "guardrails": {"max_items_per_run": None, "max_spend_usd": None},
        "deterministic": {"title_mode": "template"}, "output_language": "english",
    }), encoding="utf-8")


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


def test_inline_sets_backend_model_effort_and_confirms(tmp_path):
    from insta_save.config.run import load_run_config
    p = tmp_path / "run.json"; _seed(p, backend="cowork", model="old", effort="low")
    run_cfg = load_run_config(p)
    answers = iter(["claude-p", "claude-sonnet", "high", "y"])
    out = config_gate.run_config_gate(run_cfg, path=p, select_mode="inline",
                                      prompt_input=lambda _p: next(answers))
    assert out.enrich.backend == "claude-p" and out.enrich.model == "claude-sonnet"
    assert out.enrich.effort == "high"
    assert json.loads(p.read_text(encoding="utf-8"))["deterministic"]["title_mode"] == "template"


def test_inline_blank_keeps_current(tmp_path):
    from insta_save.config.run import load_run_config
    p = tmp_path / "run.json"; _seed(p, backend="api", model="keep", effort="medium")
    run_cfg = load_run_config(p)
    answers = iter(["", "", "", "y"])
    out = config_gate.run_config_gate(run_cfg, path=p, select_mode="inline",
                                      prompt_input=lambda _p: next(answers))
    assert out.enrich.backend == "api" and out.enrich.model == "keep" and out.enrich.effort == "medium"


def test_inline_reprompts_on_invalid_backend(tmp_path):
    from insta_save.config.run import load_run_config
    p = tmp_path / "run.json"; _seed(p)
    run_cfg = load_run_config(p)
    answers = iter(["nope", "claude-p", "m", "medium", "y"])
    out = config_gate.run_config_gate(run_cfg, path=p, select_mode="inline",
                                      prompt_input=lambda _p: next(answers))
    assert out.enrich.backend == "claude-p"


def test_abort_raises(tmp_path):
    from insta_save.config.run import load_run_config
    p = tmp_path / "run.json"; _seed(p)
    run_cfg = load_run_config(p)
    answers = iter(["", "", "", "abort"])
    with pytest.raises(SystemExit):
        config_gate.run_config_gate(run_cfg, path=p, select_mode="inline",
                                    prompt_input=lambda _p: next(answers))


def test_editor_mode_reloads_after_edit(tmp_path, monkeypatch):
    from insta_save.config.run import load_run_config
    p = tmp_path / "run.json"; _seed(p, backend="cowork")
    run_cfg = load_run_config(p)
    def fake_edit(path):
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        d["enrich"]["backend"] = "claude-p"
        Path(path).write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setattr(config_gate, "_editor_edit", fake_edit)
    out = config_gate.run_config_gate(run_cfg, path=p, select_mode="editor",
                                      prompt_input=lambda _p: "y")
    assert out.enrich.backend == "claude-p"
