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

    # stub the CLI helper (signature: prompt, model). fill passes run_cfg.enrich.model RAW.
    def fake_run(prompt, model):
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
