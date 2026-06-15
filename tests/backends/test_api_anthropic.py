# tests/backends/test_api_anthropic.py
import json
from insta_save.backends import api_anthropic as api
from insta_save.backends.base import FillResult


class _FakeEnv:
    anthropic_api_key = "sk-test"


class _FakeEnrich:
    def __init__(self, api_mode="sync"):
        self.model = "claude-opus-4-8"; self.effort = "high"; self.api_mode = api_mode


class _FakeRun:
    def __init__(self, api_mode="sync"):
        self.enrich = _FakeEnrich(api_mode)
        self.char_budget = 80000; self.max_items = 15; self.image_token_budget = 120000


class _FakeTextBlock:
    type = "text"
    def __init__(self, text): self.text = text


class _FakeResp:
    def __init__(self, text): self.content = [_FakeTextBlock(text)]


class _FakeMessages:
    def __init__(self, payload_text): self.payload_text = payload_text; self.calls = 0; self.last_kwargs = None
    def create(self, **kwargs):
        self.calls += 1; self.last_kwargs = kwargs
        return _FakeResp(self.payload_text)


def _enrich_dir(tmp_path, items):
    d = tmp_path / "enrich"; d.mkdir()
    (d / "batch.json").write_text(json.dumps({"group": "Hustling", "items": items}),
                                  encoding="utf-8")
    (d / "prompt.txt").write_text("INSTRUCTIONS + VOCAB + CONTENT", encoding="utf-8")
    return d


def test_sync_fill_writes_results(tmp_path, monkeypatch):
    items = [{"page_id": "p1", "source_id": "s1", "caption": "c"}]
    d = _enrich_dir(tmp_path, items)
    payload = json.dumps([{"page_id": "p1", "source_id": "s1", "title": "T", "summary": "S",
                           "externals": "", "content_type": "tool", "topics": ["seo"]}])
    fake = _FakeMessages(payload)
    monkeypatch.setattr(api, "_messages", lambda env, run_cfg: fake)
    r = api.fill(env=_FakeEnv(), run_cfg=_FakeRun("sync"), enrich_dir=d)
    assert isinstance(r, FillResult) and r.filled == 1 and r.failed == 0
    out = json.loads((d / "results.json").read_text())
    assert out[0]["page_id"] == "p1" and out[0]["summary"] == "S"
    assert fake.calls == 1


def test_sync_fill_strips_json_fences(tmp_path, monkeypatch):
    items = [{"page_id": "p1", "source_id": "s1", "caption": "c"}]
    d = _enrich_dir(tmp_path, items)
    fenced = "```json\n" + json.dumps([{"page_id": "p1", "source_id": "s1", "title": "T",
        "summary": "S", "externals": "", "content_type": "tool", "topics": []}]) + "\n```"
    monkeypatch.setattr(api, "_messages", lambda env, run_cfg: _FakeMessages(fenced))
    r = api.fill(env=_FakeEnv(), run_cfg=_FakeRun("sync"), enrich_dir=d)
    assert r.filled == 1
    assert json.loads((d / "results.json").read_text())[0]["title"] == "T"


def test_batch_budgets_forwards_run_cfg():
    b = api.batch_budgets(_FakeRun())
    assert b.char_budget == 80000 and b.max_items == 15 and b.image_token_budget == 120000
