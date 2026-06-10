# tests/stages/test_calibrate.py
import json
import types
from insta_save.stages import calibrate


class _Cols:
    def group_of(self, c):
        return {"hust-a": "Hustling"}.get(c)


def _env(tmp_path):
    return types.SimpleNamespace(tmp_dir=str(tmp_path))


def test_sample_collects_group_items_and_writes_prompt(tmp_path, monkeypatch):
    stubs = {"High": [{"page_id": "p1", "source_id": "s1", "collections": ["hust-a"]},
                      {"page_id": "p2", "source_id": "s2", "collections": ["other"]}]}
    monkeypatch.setattr(calibrate, "query_by_status_and_priority",
                        lambda env, status, pr: stubs.get(pr, []))
    monkeypatch.setattr(calibrate, "get_page_content",
                        lambda env, pid: {"page_id": pid, "source_id": "s1", "caption": "c",
                                          "transcript": "t", "ocr_text": "", "type": "Reel"})

    n = calibrate.sample(_env(tmp_path), group="Hustling", collections_cfg=_Cols(),
                         limit=20, statuses=["Extracted"], prompt_template="CAL {group}")
    assert n == 1   # only the hust-a item
    sample = json.loads((tmp_path / "calibrate" / "sample.json").read_text())
    assert [i["page_id"] for i in sample["items"]] == ["p1"]
    prompt = (tmp_path / "calibrate" / "prompt.txt").read_text()
    assert "Hustling" in prompt and "p1" in prompt


def test_sample_respects_limit(tmp_path, monkeypatch):
    stubs = {"High": [{"page_id": f"p{i}", "source_id": f"s{i}", "collections": ["hust-a"]}
                      for i in range(5)]}
    monkeypatch.setattr(calibrate, "query_by_status_and_priority",
                        lambda env, status, pr: stubs.get(pr, []))
    monkeypatch.setattr(calibrate, "get_page_content",
                        lambda env, pid: {"page_id": pid, "source_id": pid, "caption": "c",
                                          "transcript": "", "ocr_text": "", "type": "Reel"})
    n = calibrate.sample(_env(tmp_path), group="Hustling", collections_cfg=_Cols(),
                         limit=3, statuses=["Extracted"], prompt_template="CAL {group}")
    assert n == 3


def test_sample_reads_multiple_statuses(tmp_path, monkeypatch):
    # Hustling content lives only at Summarized (v1-done) -> sampling must reach it.
    by_status = {
        "Extracted": {},
        "Summarized": {"High": [{"page_id": "p9", "source_id": "s9", "collections": ["hust-a"]}]},
    }
    monkeypatch.setattr(calibrate, "query_by_status_and_priority",
                        lambda env, status, pr: by_status.get(status, {}).get(pr, []))
    monkeypatch.setattr(calibrate, "get_page_content",
                        lambda env, pid: {"page_id": pid, "source_id": pid, "caption": "c",
                                          "transcript": "t", "ocr_text": "", "type": "Reel"})
    n = calibrate.sample(_env(tmp_path), group="Hustling", collections_cfg=_Cols(),
                         limit=20, statuses=["Extracted", "Summarized"],
                         prompt_template="CAL {group}")
    assert n == 1
    sample = json.loads((tmp_path / "calibrate" / "sample.json").read_text())
    assert [i["page_id"] for i in sample["items"]] == ["p9"]
