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


class _FakeBatch:
    id = "batch-1"
    processing_status = "ended"


class _FakeResult:
    def __init__(self, message): self.result = type("R", (), {"message": message})


class _FakeBatches:
    """Mirrors the messages.batches SDK shape used by _fill_batches."""
    def __init__(self, payload_text): self.payload_text = payload_text; self.created = None
    def create(self, requests): self.created = requests; return _FakeBatch()
    def retrieve(self, batch_id): return _FakeBatch()
    def results(self, batch_id): return [_FakeResult(_FakeResp(self.payload_text))]


def test_batches_fill_writes_results(tmp_path, monkeypatch):
    items = [{"page_id": "p1", "source_id": "s1", "caption": "c"}]
    d = _enrich_dir(tmp_path, items)
    payload = json.dumps([{"page_id": "p1", "source_id": "WRONG", "title": "T", "summary": "S",
                           "externals": "", "content_type": "tool", "topics": ["seo"]}])
    fake = _FakeBatches(payload)
    monkeypatch.setattr(api, "_batches", lambda env, run_cfg: fake)
    r = api.fill(env=_FakeEnv(), run_cfg=_FakeRun("batches"), enrich_dir=d)
    assert isinstance(r, FillResult) and r.filled == 1 and r.failed == 0
    out = json.loads((d / "results.json").read_text())
    assert out[0]["page_id"] == "p1" and out[0]["summary"] == "S"
    assert out[0]["source_id"] == "s1"  # overwritten from the batch, not the model
    assert fake.created is not None  # batches.create was actually called


def test_fill_normalizes_identity_from_batch(tmp_path, monkeypatch):
    items = [{"page_id": "p1", "source_id": "s1", "caption": "c"}]
    d = _enrich_dir(tmp_path, items)
    payload = json.dumps([
        {"page_id": "p1", "source_id": "bogus", "title": "T", "summary": "S",
         "externals": "", "content_type": "tool", "topics": []},
        {"page_id": "p1", "source_id": "dup", "title": "DUP", "summary": "D",
         "externals": "", "content_type": "tool", "topics": []},
        {"page_id": "ghost", "source_id": "x", "title": "HALLUCINATED", "summary": "H",
         "externals": "", "content_type": "tool", "topics": []},
    ])
    monkeypatch.setattr(api, "_messages", lambda env, run_cfg: _FakeMessages(payload))
    r = api.fill(env=_FakeEnv(), run_cfg=_FakeRun("sync"), enrich_dir=d)
    assert r.filled == 1 and r.failed == 0
    out = json.loads((d / "results.json").read_text())
    assert len(out) == 1
    assert out[0]["page_id"] == "p1" and out[0]["title"] == "T"  # first occurrence kept
    assert out[0]["source_id"] == "s1"  # authoritative source_id from the batch


def test_batch_budgets_forwards_run_cfg():
    b = api.batch_budgets(_FakeRun())
    assert b.char_budget == 80000 and b.max_items == 15 and b.image_token_budget == 120000


# --- max_tokens truncation guard tests ---

class _FakeRespWithStopReason:
    """Like _FakeResp but carries a stop_reason (mimics a real Messages response)."""
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_FakeTextBlock(text)]
        self.stop_reason = stop_reason


class _FakeMessagesWithStopReason:
    """Like _FakeMessages but uses _FakeRespWithStopReason so stop_reason is present."""
    def __init__(self, text, stop_reason="end_turn"):
        self.payload_text = text; self.stop_reason = stop_reason
        self.calls = 0
    def create(self, **kwargs):
        self.calls += 1
        return _FakeRespWithStopReason(self.payload_text, self.stop_reason)


def test_sync_truncated_raises_runtime_error(tmp_path, monkeypatch):
    """When the Messages API returns stop_reason='max_tokens', fill must raise RuntimeError."""
    items = [{"page_id": "p1", "source_id": "s1", "caption": "c"}]
    d = _enrich_dir(tmp_path, items)
    # Partial text that was cut off — doesn't matter what it contains
    fake = _FakeMessagesWithStopReason('{"page_id": "p1"', stop_reason="max_tokens")
    monkeypatch.setattr(api, "_messages", lambda env, run_cfg: fake)
    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="max_tokens"):
        api.fill(env=_FakeEnv(), run_cfg=_FakeRun("sync"), enrich_dir=d)


def test_sync_end_turn_returns_normally(tmp_path, monkeypatch):
    """When stop_reason='end_turn', fill continues normally and writes results."""
    items = [{"page_id": "p1", "source_id": "s1", "caption": "c"}]
    d = _enrich_dir(tmp_path, items)
    payload = json.dumps([{"page_id": "p1", "source_id": "s1", "title": "T", "summary": "S",
                           "externals": "", "content_type": "tool", "topics": []}])
    fake = _FakeMessagesWithStopReason(payload, stop_reason="end_turn")
    monkeypatch.setattr(api, "_messages", lambda env, run_cfg: fake)
    r = api.fill(env=_FakeEnv(), run_cfg=_FakeRun("sync"), enrich_dir=d)
    assert r.filled == 1 and r.failed == 0


class _FakeResultWithStopReason:
    """Batch result whose inner message carries a stop_reason."""
    def __init__(self, message):
        self.result = type("R", (), {"message": message})


class _FakeBatchesWithStopReason:
    """Like _FakeBatches but supports configuring stop_reason on the message."""
    def __init__(self, payload_text, stop_reason="end_turn"):
        self.payload_text = payload_text; self.stop_reason = stop_reason
    def create(self, requests): return _FakeBatch()
    def retrieve(self, batch_id): return _FakeBatch()
    def results(self, batch_id):
        msg = _FakeRespWithStopReason(self.payload_text, self.stop_reason)
        return [_FakeResultWithStopReason(msg)]


def test_batches_truncated_raises_runtime_error(tmp_path, monkeypatch):
    """When a batch result message has stop_reason='max_tokens', fill must raise RuntimeError."""
    items = [{"page_id": "p1", "source_id": "s1", "caption": "c"}]
    d = _enrich_dir(tmp_path, items)
    fake = _FakeBatchesWithStopReason('{"page_id": "p1"', stop_reason="max_tokens")
    monkeypatch.setattr(api, "_batches", lambda env, run_cfg: fake)
    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="max_tokens"):
        api.fill(env=_FakeEnv(), run_cfg=_FakeRun("batches"), enrich_dir=d)


def test_batches_end_turn_returns_normally(tmp_path, monkeypatch):
    """When batch result stop_reason='end_turn', fill completes normally."""
    items = [{"page_id": "p1", "source_id": "s1", "caption": "c"}]
    d = _enrich_dir(tmp_path, items)
    payload = json.dumps([{"page_id": "p1", "source_id": "s1", "title": "T", "summary": "S",
                           "externals": "", "content_type": "tool", "topics": []}])
    fake = _FakeBatchesWithStopReason(payload, stop_reason="end_turn")
    monkeypatch.setattr(api, "_batches", lambda env, run_cfg: fake)
    r = api.fill(env=_FakeEnv(), run_cfg=_FakeRun("batches"), enrich_dir=d)
    assert r.filled == 1 and r.failed == 0
