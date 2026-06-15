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


def test_prepare_prompt_rendered_through_prompt_module(tmp_path, monkeypatch):
    # prompt.txt is assembled by backends.prompt: the rendered vocab block carries
    # the group's locked topic, proving assembly routes through the prompt module.
    stubs = {"High": [{"page_id": "p1", "source_id": "s1", "collections": ["hust-a"]}]}
    monkeypatch.setattr(enrich, "query_by_status_and_priority",
                        lambda env, status, pr: stubs.get(pr, []) if status == "Extracted" else [])
    monkeypatch.setattr(enrich, "get_page_content",
                        lambda env, pid: {"page_id": pid, "source_id": pid, "caption": "c",
                                          "transcript": "", "ocr_text": "", "type": "Reel",
                                          "author": "a", "transcript_language": "en"})
    n = enrich.prepare(_env(tmp_path), group="Hustling", collections_cfg=_Cols(),
                       vocab=_vocab(), char_budget=100000, max_items=10,
                       statuses=["Extracted"], prompt_template="H {vocab_block}")
    assert n == 1
    rendered = (tmp_path / "enrich" / "prompt.txt").read_text()
    assert "seo" in rendered  # group-locked topic from the vocab block


def test_prepare_budgets_on_rendered_prompt(tmp_path, monkeypatch):
    # char_budget bounds the RENDERED prompt (header + scaffolding + content), not
    # just raw content. Three identical items + a budget sized for exactly two.
    from insta_save.backends import claude_code as backend
    stubs = {"High": [{"page_id": f"p{i}", "source_id": f"p{i}", "collections": ["hust-a"]}
                      for i in range(3)]}
    monkeypatch.setattr(enrich, "query_by_status_and_priority",
                        lambda env, status, pr: stubs.get(pr, []) if status == "Extracted" else [])
    base = {"caption": "c" * 40, "transcript": "", "ocr_text": "", "type": "Reel",
            "author": "a", "transcript_language": "en"}
    monkeypatch.setattr(enrich, "get_page_content",
                        lambda env, pid: {**base, "page_id": pid, "source_id": pid})
    vocab, tmpl = _vocab(), "H {vocab_block}"
    one = {**base, "page_id": "p0", "source_id": "p0"}
    budget = backend.header_len("Hustling", vocab, tmpl) + 2 * backend.item_len(one) + 5

    n = enrich.prepare(_env(tmp_path), group="Hustling", collections_cfg=_Cols(), vocab=vocab,
                       char_budget=budget, max_items=10, statuses=["Extracted"], prompt_template=tmpl)
    assert n == 2  # third item would push the rendered prompt over budget
    assert len((tmp_path / "enrich" / "prompt.txt").read_text()) <= budget


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
    # full success -> tmp files cleaned (lets the next --prepare advance)
    assert not (tmp_path / "enrich" / "results.json").exists()
    assert not (tmp_path / "enrich" / "batch.json").exists()


def test_prepare_excludes_other_group(tmp_path, monkeypatch):
    # large cap so the cap never fires — only the group filter decides membership
    stubs = {"High": [{"page_id": "p1", "source_id": "s1", "collections": ["hust-a"]},
                      {"page_id": "p2", "source_id": "s2", "collections": ["other"]},
                      {"page_id": "p3", "source_id": "s3", "collections": ["hust-a"]}]}
    monkeypatch.setattr(enrich, "query_by_status_and_priority",
                        lambda env, status, pr: stubs.get(pr, []) if status == "Extracted" else [])
    monkeypatch.setattr(enrich, "get_page_content",
                        lambda env, pid: {"page_id": pid, "source_id": pid, "caption": "c",
                                          "transcript": "", "ocr_text": "", "type": "Reel",
                                          "author": "a", "transcript_language": "en"})
    n = enrich.prepare(_env(tmp_path), group="Hustling", collections_cfg=_Cols(),
                       vocab=_vocab(), char_budget=100000, max_items=10,
                       statuses=["Extracted"], prompt_template="H {vocab_block}")
    assert n == 2
    batch = json.loads((tmp_path / "enrich" / "batch.json").read_text())
    assert [i["page_id"] for i in batch["items"]] == ["p1", "p3"]   # 'other' excluded


def test_apply_errors_when_results_missing(tmp_path):
    (tmp_path / "enrich").mkdir()
    try:
        enrich.apply(_env(tmp_path), vocab=_vocab(), model="m")
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_ordered_stubs_filters_by_kind(monkeypatch):
    from insta_save.config.collections import CollectionsConfig
    cfg = CollectionsConfig(groups=("G",), collections={"c": {"group": "G", "extract": True}})
    stubs = [
        {"page_id": "r", "type": "Reel", "collections": ["c"]},
        {"page_id": "k", "type": "Carousel", "collections": ["c"]},
        {"page_id": "p", "type": "Post", "collections": ["c"]},
    ]
    monkeypatch.setattr(enrich, "query_by_status_and_priority",
                        lambda env, status, bucket: stubs if bucket is None else [])
    got = [s["page_id"] for s in enrich._ordered_group_stubs(
        None, ["Extracted"], "G", cfg, kinds={"Carousel", "Post"})]
    assert got == ["k", "p"]


def test_prepare_vision_lane_breaks_on_image_budget(monkeypatch, tmp_path):
    from insta_save.config.collections import CollectionsConfig
    cfg = CollectionsConfig(groups=("G",), collections={"c": {"group": "G", "extract": True}})
    stubs = [{"page_id": f"k{i}", "type": "Carousel", "collections": ["c"]} for i in range(3)]
    monkeypatch.setattr(enrich, "query_by_status_and_priority",
                        lambda env, status, bucket: stubs if bucket is None else [])
    monkeypatch.setattr(enrich, "get_page_content", lambda env, pid: {
        "page_id": pid, "source_id": pid, "type": "Carousel", "author": "a", "caption": "c",
        "slide_images": ["a.jpg", "b.jpg"], "transcript": None, "ocr_text": None,
        "transcript_language": "en"})

    class _Env:
        tmp_dir = str(tmp_path)

    # vocab must include group "G" so header_len doesn't blow up on the vocab block
    vocab_g = Vocab(content_types=["tool"], cross_group_topics=["ai"],
                    _group_topics={"G": ["seo"]},
                    definitions={"tool": "x", "ai": "y", "seo": "z"})
    # 2 slides/item * 1600 = 3200 tokens/item; budget 5000 -> first admitted, second exceeds
    n = enrich.prepare(_Env(), group="G", collections_cfg=cfg, vocab=vocab_g,
                       char_budget=10**9, max_items=None, statuses=["Extracted"],
                       prompt_template="H {vocab_block} E", kinds={"Carousel", "Post"},
                       image_token_budget=5000)
    assert n == 1
