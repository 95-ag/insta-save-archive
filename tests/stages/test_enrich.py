# tests/stages/test_enrich.py
import json
import types
from insta_save.stages import enrich
from insta_save.config.collections import CollectionsConfig
from insta_save.config.tags import Vocab


class _Cols:
    groups = ("Hustling", "Other")

    def group_of(self, c):
        return {"hust-a": "Hustling", "other": "Other"}.get(c)

    def enrich_group(self, names):
        # single-group: Hustling only; cross-group unsupported in this stub
        groups = {self.group_of(c) for c in names if self.group_of(c) is not None}
        order = ["Hustling", "Other"]
        extract = [g for g in order if g in groups]
        return extract[-1] if extract else None

    def extract_groups_of(self, names):
        order = ["Hustling", "Other"]
        groups = {self.group_of(c) for c in names if self.group_of(c) is not None}
        return [g for g in order if g in groups]


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
        {"group": "Hustling",
         "items": [{"page_id": "p1", "source_id": "s1", "collections": ["hust-a"]}]}))
    (tmp_path / "enrich" / "results.json").write_text(json.dumps([
        {"page_id": "p1", "source_id": "s1", "title": "T", "summary": "S", "externals": "",
         "content_type": "tool", "topics": ["seo", "bogus", "ai"]}]))
    written = {}
    monkeypatch.setattr(enrich, "write_enrichment",
                        lambda env, pid, fields, version: written.update({pid: (fields, version)}))

    counts = enrich.apply(_env(tmp_path), vocab=_vocab(), model="claude-sonnet",
                          collections_cfg=_Cols())
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
        enrich.apply(_env(tmp_path), vocab=_vocab(), model="m", collections_cfg=_Cols())
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


# ---------------------------------------------------------------------------
# Cross-group tests (Part 1: selection at last extract group; Part 2: union vocab)
# ---------------------------------------------------------------------------

def _cross_cfg():
    """Two extract groups F (first) and G (second/last), plus a non-extract group N."""
    return CollectionsConfig(
        groups=("F", "G", "N"),
        collections={
            "col-f": {"group": "F", "extract": True},
            "col-g": {"group": "G", "extract": True},
            "col-n": {"group": "N", "extract": False},
        },
    )


def _cross_vocab():
    return Vocab(
        content_types=["tool"],
        cross_group_topics=["ai"],
        _group_topics={"F": ["f-topic"], "G": ["g-topic"]},
        definitions={"tool": "x", "ai": "y", "f-topic": "ff", "g-topic": "gg"},
    )


def test_ordered_stubs_cross_group_excluded_from_first_group(monkeypatch):
    """A cross-group stub (F+G) must NOT be yielded when draining group F — it will
    be enriched at G (its last extract group)."""
    cfg = _cross_cfg()
    # cross_stub is in both F and G collections
    stubs = [
        {"page_id": "cross", "type": "Reel", "collections": ["col-f", "col-g"]},
        {"page_id": "pure-f", "type": "Reel", "collections": ["col-f"]},
    ]
    monkeypatch.setattr(enrich, "query_by_status_and_priority",
                        lambda env, status, bucket: stubs if bucket is None else [])
    got = [s["page_id"] for s in enrich._ordered_group_stubs(
        None, ["Extracted"], "F", cfg)]
    # cross item's last extract group is G, not F -> excluded from F's drain
    assert got == ["pure-f"]


def test_ordered_stubs_cross_group_included_at_last_group(monkeypatch):
    """The same cross-group stub IS yielded when draining its last extract group G."""
    cfg = _cross_cfg()
    stubs = [
        {"page_id": "cross", "type": "Reel", "collections": ["col-f", "col-g"]},
        {"page_id": "pure-g", "type": "Reel", "collections": ["col-g"]},
    ]
    monkeypatch.setattr(enrich, "query_by_status_and_priority",
                        lambda env, status, bucket: stubs if bucket is None else [])
    got = [s["page_id"] for s in enrich._ordered_group_stubs(
        None, ["Extracted"], "G", cfg)]
    assert set(got) == {"cross", "pure-g"}


def test_apply_cross_group_union_vocab_survives(tmp_path, monkeypatch):
    """A cross-group result item's F-granular AND G-granular topics must both survive
    validation — the old single-group allowed_topics(G) would strip F's granular tags."""
    cfg = _cross_cfg()
    vocab = _cross_vocab()
    (tmp_path / "enrich").mkdir()
    # batch.json: cross-group item that lives in both F and G collections
    (tmp_path / "enrich" / "batch.json").write_text(json.dumps({
        "group": "G",
        "items": [{"page_id": "p1", "source_id": "s1", "collections": ["col-f", "col-g"]}],
    }))
    # result claims both f-topic (F-granular) and g-topic (G-granular) + cross-group ai
    (tmp_path / "enrich" / "results.json").write_text(json.dumps([{
        "page_id": "p1", "source_id": "s1",
        "title": "T", "summary": "S", "externals": "",
        "content_type": "tool", "topics": ["f-topic", "g-topic", "ai"],
    }]))
    written = {}
    monkeypatch.setattr(enrich, "write_enrichment",
                        lambda env, pid, fields, version: written.update({pid: (fields, version)}))
    env = _env(tmp_path)
    counts = enrich.apply(env, vocab=vocab, model="m", collections_cfg=cfg)
    assert counts["written"] == 1
    tags = written["p1"][0]["tags"]
    assert "f-topic" in tags, "F-granular tag stripped — union_topics not applied"
    assert "g-topic" in tags, "G-granular tag stripped"
    assert "ai" in tags, "cross-group tag stripped"


def test_prepare_cross_group_prompt_contains_union_vocab(tmp_path, monkeypatch):
    """prepare() for group G with a cross-group item must render BOTH F and G granular
    topics in the prompt vocab block."""
    cfg = _cross_cfg()
    vocab = _cross_vocab()
    stubs = [{"page_id": "p1", "type": "Reel", "collections": ["col-f", "col-g"]}]
    monkeypatch.setattr(enrich, "query_by_status_and_priority",
                        lambda env, status, pr: stubs if pr is None else [])
    monkeypatch.setattr(enrich, "get_page_content",
                        lambda env, pid: {"page_id": pid, "source_id": pid, "caption": "c",
                                          "transcript": "", "ocr_text": "", "type": "Reel",
                                          "author": "a", "transcript_language": "en",
                                          "collections": ["col-f", "col-g"]})

    class _Env:
        tmp_dir = str(tmp_path)

    n = enrich.prepare(_Env(), group="G", collections_cfg=cfg, vocab=vocab,
                       char_budget=10**9, max_items=10, statuses=["Extracted"],
                       prompt_template="H {vocab_block} E")
    assert n == 1
    rendered = (tmp_path / "enrich" / "prompt.txt").read_text()
    assert "f-topic" in rendered, "F-granular topic missing from union vocab block"
    assert "g-topic" in rendered, "G-granular topic missing from union vocab block"
