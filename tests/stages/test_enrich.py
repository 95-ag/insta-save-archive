# tests/stages/test_enrich.py
import json
import types
from insta_save.stages import enrich
from insta_save.config.tags import Vocab


class _Cols:
    def group_of(self, c):
        return {"hust-a": "Hustling", "other": "Other"}.get(c)


def _vocab():
    return Vocab(content_types=["tool"], cross_group_topics=["ai"],
                 _group_topics={"Hustling": ["seo"]},
                 definitions={"tool": "x", "ai": "y", "seo": "z"})


def _env(tmp_path):
    return types.SimpleNamespace(tmp_dir=str(tmp_path), enrich_version="v2.0-enrich",
                                 notion_write_delay=0)


def test_prepare_filters_group_and_caps_batch(tmp_path, monkeypatch):
    # two Hustling stubs + one Other; max_items=1 -> batch has exactly the first Hustling item
    stubs = {
        "High": [{"page_id": "p1", "source_id": "s1", "collections": ["hust-a"]},
                 {"page_id": "p2", "source_id": "s2", "collections": ["hust-a"]}],
        "Medium": [{"page_id": "p3", "source_id": "s3", "collections": ["other"]}],
    }
    monkeypatch.setattr(enrich, "query_by_status_and_priority",
                        lambda env, status, pr: stubs.get(pr, []) if status == "Extracted" else [])
    monkeypatch.setattr(enrich, "get_page_content",
                        lambda env, pid: {"page_id": pid, "source_id": pid.replace("p", "s"),
                                          "caption": "c", "transcript": "", "ocr_text": "",
                                          "type": "Reel", "author": "a", "transcript_language": "en"})

    n = enrich.prepare(_env(tmp_path), group="Hustling", collections_cfg=_Cols(),
                       vocab=_vocab(), char_budget=100000, max_items=1,
                       statuses=["Extracted"], prompt_template="H {vocab_block}")
    assert n == 1
    batch = json.loads((tmp_path / "enrich" / "batch.json").read_text())
    assert batch["group"] == "Hustling"
    assert [i["page_id"] for i in batch["items"]] == ["p1"]
    assert (tmp_path / "enrich" / "prompt.txt").exists()


def test_apply_validates_tags_and_writes(tmp_path, monkeypatch):
    (tmp_path / "enrich").mkdir()
    (tmp_path / "enrich" / "batch.json").write_text(json.dumps(
        {"group": "Hustling", "items": [{"page_id": "p1", "source_id": "s1"}]}))
    (tmp_path / "enrich" / "results.json").write_text(json.dumps([
        {"page_id": "p1", "source_id": "s1", "title": "T", "summary": "S", "externals": "",
         "content_type": "tool", "topics": ["seo", "bogus", "ai"]}]))
    written = {}
    monkeypatch.setattr(enrich, "write_enrichment",
                        lambda env, pid, fields, version: written.update({pid: (fields, version)}))

    counts = enrich.apply(_env(tmp_path), vocab=_vocab(), model="claude-sonnet")
    fields, version = written["p1"]
    assert fields["tags"] == ["tool", "seo", "ai"]   # bogus dropped, content_type first
    assert version == "claude-sonnet/v2.0-enrich/Hustling"
    assert counts["written"] == 1


def test_apply_errors_when_results_missing(tmp_path):
    (tmp_path / "enrich").mkdir()
    try:
        enrich.apply(_env(tmp_path), vocab=_vocab(), model="m")
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass
