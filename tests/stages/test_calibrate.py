# tests/stages/test_calibrate.py
import json
import types
from insta_save.stages import calibrate


class _Cols:
    def group_of(self, c):
        return {"hust-a": "Hustling"}.get(c)

    def collections_in_group(self, g):
        return {"hust-a", "Side Projects"} if g == "Hustling" else set()


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


class _Cols2:
    def group_of(self, c):
        return "G" if c in ("big", "small") else None

    def collections_in_group(self, g):
        return {"big", "small"} if g == "G" else set()


def test_sample_round_robins_across_collections(tmp_path, monkeypatch):
    stubs = {"High": [{"page_id": f"b{i}", "source_id": f"b{i}", "collections": ["big"]} for i in range(5)]
                     + [{"page_id": "s0", "source_id": "s0", "collections": ["small"]}]}
    monkeypatch.setattr(calibrate, "query_by_status_and_priority", lambda env, s, pr: stubs.get(pr, []))
    monkeypatch.setattr(calibrate, "get_page_content",
                        lambda env, pid: {"page_id": pid, "source_id": pid, "type": "Reel"})
    calibrate.sample(_env(tmp_path), group="G", collections_cfg=_Cols2(),
                     limit=3, statuses=["Extracted"], prompt_template="{group}")
    pids = [i["page_id"] for i in json.loads((tmp_path / "calibrate" / "sample.json").read_text())["items"]]
    assert "s0" in pids and len(pids) == 3      # small collection represented despite big's 5


def test_sample_size_adapts_to_collection_size(tmp_path, monkeypatch):
    # big=40 -> ceil(40*0.25)=10 (capped at _MAX_PER_COLL=10); small=2 -> min(2, max(0.5→3))=2 ; total 12
    big = [{"page_id": f"b{i}", "source_id": f"b{i}", "collections": ["big"]} for i in range(40)]
    small = [{"page_id": f"s{i}", "source_id": f"s{i}", "collections": ["small"]} for i in range(2)]
    monkeypatch.setattr(calibrate, "query_by_status_and_priority", lambda env, s, pr: {"High": big + small}.get(pr, []))
    monkeypatch.setattr(calibrate, "get_page_content",
                        lambda env, pid: {"page_id": pid, "source_id": pid, "type": "Reel"})
    n = calibrate.sample(_env(tmp_path), group="G", collections_cfg=_Cols2(),
                         limit=None, statuses=["Extracted"], prompt_template="{group}")
    pids = [i["page_id"] for i in json.loads((tmp_path / "calibrate" / "sample.json").read_text())["items"]]
    assert n == 12
    assert len([p for p in pids if p.startswith("b")]) == 10
    assert len([p for p in pids if p.startswith("s")]) == 2


def test_prompt_includes_collection_names(tmp_path, monkeypatch):
    stubs = {"High": [{"page_id": "p1", "source_id": "s1", "collections": ["hust-a"]}]}
    monkeypatch.setattr(calibrate, "query_by_status_and_priority", lambda env, s, pr: stubs.get(pr, []))
    monkeypatch.setattr(calibrate, "get_page_content",
                        lambda env, pid: {"page_id": pid, "source_id": "s1", "caption": "c", "type": "Reel"})
    calibrate.sample(_env(tmp_path), group="Hustling", collections_cfg=_Cols(),
                     limit=20, statuses=["Extracted"], prompt_template="CAL {group} COLS {collections}")
    prompt = (tmp_path / "calibrate" / "prompt.txt").read_text()
    assert "Side Projects" in prompt and "hust-a" in prompt
