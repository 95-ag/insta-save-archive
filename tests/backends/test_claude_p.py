# tests/backends/test_claude_p.py
import json
from pathlib import Path
from insta_save.backends import claude_p


class _Enrich:
    backend = "claude-p"; model = "claude-sonnet"; effort = "medium"; api_mode = "sync"


class _Run:
    enrich = _Enrich(); char_budget = 80000; max_items = 15; image_token_budget = 120000


class _Env:
    def __init__(self, tmp): self.tmp_dir = str(tmp)


def test_fill_parses_claude_p_envelope_and_writes_results(tmp_path, monkeypatch):
    d = tmp_path / "enrich"; d.mkdir()
    # Items shaped like test_api_anthropic.py — normalize_results keys on page_id,
    # overwrites source_id from the batch item.
    items = [{"page_id": "p1", "source_id": "s1", "caption": "c"}]
    (d / "batch.json").write_text(json.dumps({"group": "G", "items": items}), encoding="utf-8")
    (d / "prompt.txt").write_text("ENRICH PROMPT", encoding="utf-8")

    # stub the CLI helper (signature: prompt, model, add_dirs). fill passes run_cfg.enrich.model RAW.
    def fake_run(prompt, model, add_dirs=None):
        assert model == "claude-sonnet"
        return json.dumps([{"page_id": "p1", "source_id": "WRONG", "content_type": "tutorial",
                            "topics": ["x"], "title": "t", "summary": "s", "externals": None}])
    monkeypatch.setattr(claude_p, "_run_claude_p", fake_run)

    res = claude_p.fill(_Env(tmp_path), _Run(), d)
    out = json.loads((d / "results.json").read_text(encoding="utf-8"))
    assert res.filled == 1 and res.failed == 0 and res.external is False
    # identity came from the BATCH item — page_id preserved, source_id overwritten from batch
    assert out[0]["page_id"] == "p1"
    assert out[0]["source_id"] == "s1"  # overwritten from batch, not "WRONG" from model
    assert out[0]["content_type"] == "tutorial"


def test_constants():
    assert claude_p.NAME == "claude-p" and claude_p.AUTOMATED is True
    assert claude_p.VISION_CAPABLE is True


def test_cli_model_strips_claude_prefix():
    assert claude_p._cli_model("claude-sonnet") == "sonnet"
    assert claude_p._cli_model("claude-opus") == "opus"
    assert claude_p._cli_model("haiku") == "haiku"


def test_propose_vocab_parses_claude_p_json(monkeypatch):
    from insta_save.backends import claude_p
    payload = ('{"content_type":{"tool":"an app"},'
               '"groups":{"G":{"web-dev":"sites"}},"cross_group":{"ai":"ai"}}')
    def fake_run(prompt, model):
        assert "PROMPT" in prompt and model == "claude-sonnet"
        return payload
    monkeypatch.setattr(claude_p, "_run_claude_p", fake_run)
    out = claude_p.propose_vocab("CALIBRATE PROMPT body", "claude-sonnet")
    assert out["groups"]["G"]["web-dev"] == "sites" and "tool" in out["content_type"]


def test_run_claude_p_appends_inline_output_override(monkeypatch):
    from insta_save.backends import claude_p
    sent = {}
    class _Proc:
        returncode = 0
        stdout = '{"result": "[]", "is_error": false}'
        stderr = ""
    def _fake_run(cmd, input=None, capture_output=None, text=None, timeout=None, cwd=None):
        sent["input"] = input
        return _Proc()
    monkeypatch.setattr(claude_p.subprocess, "run", _fake_run)
    claude_p._run_claude_p("MY ENRICH PROMPT", "claude-sonnet")
    assert "MY ENRICH PROMPT" in sent["input"]
    low = sent["input"].lower()
    assert "do not write" in low and ("return only" in low or "as your reply" in low)


def test_propose_vocab_recovers_prose_wrapped_draft(monkeypatch):
    # real claude -p (diagnostic-confirmed) returns the JSON wrapped in prose + a fence,
    # not bare JSON — propose_vocab must recover the object, not crash on json.loads.
    from insta_save.backends import claude_p
    wrapped = ("I don't have write permission to tmp/calibrate/. Here's the proposal — "
               "you can paste it in manually:\n\n"
               '```json\n{"content_type":{"tool":"an app"},'
               '"groups":{"G":{"web-dev":"sites"}},"cross_group":{"ai":"ai"}}\n```\n\n'
               "**Notes on choices:** rationale prose after the object.")
    monkeypatch.setattr(claude_p, "_run_claude_p", lambda p, m: wrapped)
    out = claude_p.propose_vocab("CALIBRATE PROMPT body", "claude-sonnet")
    assert out["groups"]["G"]["web-dev"] == "sites" and "tool" in out["content_type"]


def test_run_claude_p_passes_add_dir_and_keeps_clean_cwd(monkeypatch):
    sent = {}
    class _Proc:
        returncode = 0; stdout = '{"result": "[]", "is_error": false}'; stderr = ""
    def _fake_run(cmd, input=None, capture_output=None, text=None, timeout=None, cwd=None):
        sent["cmd"] = cmd; sent["cwd"] = cwd
        return _Proc()
    monkeypatch.setattr(claude_p.subprocess, "run", _fake_run)
    claude_p._run_claude_p("P", "claude-sonnet", add_dirs=["/t/slides/a", "/t/slides/b"])
    assert sent["cwd"].endswith("isa-claude-cwd")          # ALWAYS clean cwd
    assert "--add-dir" in sent["cmd"]
    assert "/t/slides/a" in sent["cmd"] and "/t/slides/b" in sent["cmd"]


def test_run_claude_p_no_add_dir_flag_when_none(monkeypatch):
    sent = {}
    class _Proc:
        returncode = 0; stdout = '{"result": "[]", "is_error": false}'; stderr = ""
    def _fake_run(cmd, input=None, capture_output=None, text=None, timeout=None, cwd=None):
        sent["cmd"] = cmd
        return _Proc()
    monkeypatch.setattr(claude_p.subprocess, "run", _fake_run)
    claude_p._run_claude_p("P", "claude-sonnet")
    assert "--add-dir" not in sent["cmd"]


def test_fill_vision_batch_passes_slide_dirs_as_add_dir(tmp_path, monkeypatch):
    import json
    d = tmp_path / "enrich"; d.mkdir()
    items = [{"page_id": "p1", "source_id": "s1",
              "slide_images": ["/abs/slides/p1/a.jpg", "/abs/slides/p1/b.jpg"]}]
    (d / "batch.json").write_text(json.dumps({"group": "G", "items": items}), encoding="utf-8")
    (d / "prompt.txt").write_text("VISION", encoding="utf-8")
    seen = {}
    def fake_run(prompt, model, add_dirs=None):
        seen["add_dirs"] = add_dirs
        return "[]"
    monkeypatch.setattr(claude_p, "_run_claude_p", fake_run)
    claude_p.fill(_Env(tmp_path), _Run(), d)
    assert seen["add_dirs"] == ["/abs/slides/p1"]


def test_fill_text_batch_passes_no_add_dir(tmp_path, monkeypatch):
    import json
    d = tmp_path / "enrich"; d.mkdir()
    items = [{"page_id": "p1", "source_id": "s1", "caption": "c"}]
    (d / "batch.json").write_text(json.dumps({"group": "G", "items": items}), encoding="utf-8")
    (d / "prompt.txt").write_text("TEXT", encoding="utf-8")
    seen = {}
    def fake_run(prompt, model, add_dirs=None):
        seen["add_dirs"] = add_dirs
        return "[]"
    monkeypatch.setattr(claude_p, "_run_claude_p", fake_run)
    claude_p.fill(_Env(tmp_path), _Run(), d)
    assert seen["add_dirs"] is None


def test_run_claude_p_runs_from_clean_cwd_outside_repo(monkeypatch):
    """claude -p must run with cwd set to an empty dir OUTSIDE the repo so CLAUDE.md
    auto-discovery finds nothing (~30k tokens of project context dropped per call)."""
    import os
    sent = {}

    class _Proc:
        returncode = 0
        stdout = '{"result": "[]", "is_error": false}'
        stderr = ""

    def _fake_run(cmd, input=None, capture_output=None, text=None, timeout=None, cwd=None):
        sent["cwd"] = cwd
        assert os.path.isdir(cwd)          # exists DURING the call
        return _Proc()

    monkeypatch.setattr(claude_p.subprocess, "run", _fake_run)
    claude_p._run_claude_p("PROMPT", "claude-sonnet")
    assert sent["cwd"] and sent["cwd"].endswith("isa-claude-cwd")
    # the clean cwd is NOT the repo (no CLAUDE.md to discover there)
    assert "insta-save-archive" not in sent["cwd"]


def test_run_claude_p_removes_clean_cwd_after_use(monkeypatch):
    import os
    sent = {}

    class _Proc:
        returncode = 0
        stdout = '{"result": "[]", "is_error": false}'
        stderr = ""

    def _fake_run(cmd, input=None, capture_output=None, text=None, timeout=None, cwd=None):
        sent["cwd"] = cwd
        assert os.path.isdir(cwd)          # exists DURING the call
        return _Proc()

    monkeypatch.setattr(claude_p.subprocess, "run", _fake_run)
    claude_p._run_claude_p("P", "claude-sonnet")
    assert not os.path.isdir(sent["cwd"])  # removed AFTER the call


def test_run_claude_p_removes_clean_cwd_on_error(monkeypatch):
    import os
    import pytest
    sent = {}

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "boom"

    def _fake_run(cmd, input=None, capture_output=None, text=None, timeout=None, cwd=None):
        sent["cwd"] = cwd
        return _Proc()

    monkeypatch.setattr(claude_p.subprocess, "run", _fake_run)
    with pytest.raises(RuntimeError):
        claude_p._run_claude_p("P", "claude-sonnet")
    assert not os.path.isdir(sent["cwd"])  # cleaned even though the call raised
