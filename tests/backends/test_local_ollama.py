# tests/backends/test_local_ollama.py
import json
from insta_save.backends import local_ollama as loc
from insta_save.backends.base import FillResult


class _FakeEnv:
    def __init__(self, tmp_path): self.tmp_dir = str(tmp_path)


class _FakeEnrich:
    model = "qwen2.5:7b"


class _FakeRun:
    enrich = _FakeEnrich()


class _FakeOllama:
    def __init__(self, by_pid): self.by_pid = by_pid; self.calls = 0
    def chat(self, model, messages, format):
        self.calls += 1
        pid = messages[-1]["content"].split("page_id:")[1].split()[0].strip()
        return {"message": {"content": json.dumps(self.by_pid[pid])}}


def _enrich_dir(tmp_path):
    d = tmp_path / "enrich"; d.mkdir()
    (d / "batch.json").write_text(json.dumps({"group": "Hustling", "items": [
        {"page_id": "p1", "source_id": "s1", "caption": "c1", "transcript": "t1"},
        {"page_id": "p2", "source_id": "s2", "caption": "c2", "transcript": "t2"},
    ]}), encoding="utf-8")
    return d


def test_fill_writes_results_for_each_item(tmp_path, monkeypatch):
    d = _enrich_dir(tmp_path)
    fake = _FakeOllama({
        "p1": {"page_id": "p1", "source_id": "s1", "title": "T1", "summary": "S1",
               "externals": "", "content_type": "tool", "topics": ["seo"]},
        "p2": {"page_id": "p2", "source_id": "s2", "title": "T2", "summary": "S2",
               "externals": "", "content_type": "tutorial", "topics": []},
    })
    monkeypatch.setattr(loc, "_client", lambda: fake)
    r = loc.fill(env=_FakeEnv(tmp_path), run_cfg=_FakeRun(), enrich_dir=d)
    assert isinstance(r, FillResult) and r.filled == 2 and r.failed == 0
    results = json.loads((d / "results.json").read_text())
    assert {x["page_id"] for x in results} == {"p1", "p2"}
    by = {x["page_id"]: x for x in results}
    assert by["p1"]["summary"] == "S1"


def test_fill_resumes_skipping_done_items(tmp_path, monkeypatch):
    d = _enrich_dir(tmp_path)
    (d / "results.json").write_text(json.dumps([
        {"page_id": "p1", "source_id": "s1", "title": "T1", "summary": "S1",
         "externals": "", "content_type": "tool", "topics": []}]), encoding="utf-8")
    fake = _FakeOllama({"p2": {"page_id": "p2", "source_id": "s2", "title": "T2",
        "summary": "S2", "externals": "", "content_type": "tutorial", "topics": []}})
    monkeypatch.setattr(loc, "_client", lambda: fake)
    r = loc.fill(env=_FakeEnv(tmp_path), run_cfg=_FakeRun(), enrich_dir=d)
    assert fake.calls == 1 and r.filled == 1  # p1 skipped, only p2 called
