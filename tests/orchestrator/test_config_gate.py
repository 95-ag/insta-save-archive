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


def test_run_config_gate_framing(tmp_path, monkeypatch, capsys):
    """Gate prints a stage_section header/footer rule and a ✔ outcome line."""
    p = tmp_path / "run.json"
    _seed(p, backend="cowork", model="m", effort="low")
    _script(monkeypatch, selects=["inline", "claude-p", "high"],
            others=["claude-sonnet", "english"], confirms=["proceed"])
    config_gate.run_config_gate(load_run_config(p), path=p, select_mode="inline")
    out = capsys.readouterr().out
    assert "run config" in out          # stage_section header rule
    assert "done · run config" in out   # stage_section footer rule
    assert "✔" in out                   # outcome line printed


def test_keep_current_skips_field_prompts_and_editor(tmp_path, monkeypatch):
    """Mode prompt returns the keep-current sentinel -> run_cfg returned unchanged,
    no inline field-prompts and no $EDITOR invoked."""
    p = tmp_path / "run.json"
    _seed(p, backend="cowork", model="old-model", effort="low")
    run_cfg = load_run_config(p)

    called = {"editor": False, "select_count": 0}

    def fake_editor(path):
        called["editor"] = True

    monkeypatch.setattr(config_gate, "_editor_edit", fake_editor)

    # tui.select: first call is the mode prompt — return the keep-current sentinel.
    # Any subsequent call (field prompts) increments the counter so we can detect them.
    sel_iter = iter([config_gate._KEEP_CURRENT])

    def fake_select(*args, **kwargs):
        called["select_count"] += 1
        return next(sel_iter)

    monkeypatch.setattr(tui, "select", fake_select)
    # select_or_other and confirm_action must not be called either
    monkeypatch.setattr(tui, "select_or_other", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("select_or_other must not be called in keep-current path")))
    monkeypatch.setattr(tui, "confirm_action", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("confirm_action must not be called in keep-current path")))

    out = config_gate.run_config_gate(run_cfg, path=p, select_mode="inline")

    assert out is run_cfg                        # same object, unchanged
    assert called["editor"] is False             # $EDITOR not opened
    assert called["select_count"] == 1           # only the mode prompt was called


def test_run_config_divider_prints_before_mode_prompt(tmp_path, monkeypatch, capsys):
    """The 'run config' header rule must print on the keep-current path.

    Before this fix, keep-current returned BEFORE the stage_section opened, so no
    divider printed at all on that path. After the fix, the section wraps the mode
    prompt, so both the header rule and 'done · run config' footer appear even when
    the user keeps current settings."""
    p = tmp_path / "run.json"
    _seed(p, backend="cowork", model="old-model", effort="low")
    run_cfg = load_run_config(p)

    monkeypatch.setattr(config_gate.tui, "select",
                        lambda *a, **k: config_gate._KEEP_CURRENT)
    monkeypatch.setattr(config_gate.tui, "select_or_other",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("select_or_other must not be called")))
    monkeypatch.setattr(config_gate.tui, "confirm_action",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("confirm_action must not be called")))

    result = config_gate.run_config_gate(run_cfg, path=p, select_mode="inline")
    out = capsys.readouterr().out

    # Both the open rule and the done-rule must appear — proving the section now wraps
    # the mode prompt and the keep-current early return is inside the with-block.
    assert "run config" in out, "header rule not printed on keep-current path"
    assert "done" in out, "'done · run config' footer not printed on keep-current path"
    assert result is run_cfg
