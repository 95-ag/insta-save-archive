# tests/stages/test_enrich.py
import json
import types
import pytest
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
    stubs = [{"page_id": "p1", "source_id": "s1", "collections": ["hust-a"]},
             {"page_id": "p2", "source_id": "s2", "collections": ["hust-a"]},
             {"page_id": "p3", "source_id": "s3", "collections": ["other"]}]
    monkeypatch.setattr(enrich, "query_by_status",
                        lambda env, status: stubs if status == "Extracted" else [])
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
    stubs = [{"page_id": "p1", "source_id": "s1", "collections": ["hust-a"]}]
    monkeypatch.setattr(enrich, "query_by_status",
                        lambda env, status: stubs if status == "Extracted" else [])
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
    stubs = [{"page_id": f"p{i}", "source_id": f"p{i}", "collections": ["hust-a"]}
            for i in range(3)]
    monkeypatch.setattr(enrich, "query_by_status",
                        lambda env, status: stubs if status == "Extracted" else [])
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
    stubs = [{"page_id": "p1", "source_id": "s1", "collections": ["hust-a"]},
             {"page_id": "p2", "source_id": "s2", "collections": ["other"]},
             {"page_id": "p3", "source_id": "s3", "collections": ["hust-a"]}]
    monkeypatch.setattr(enrich, "query_by_status",
                        lambda env, status: stubs if status == "Extracted" else [])
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
    monkeypatch.setattr(enrich, "query_by_status", lambda env, status: stubs)
    got = [s["page_id"] for s in enrich._ordered_group_stubs(
        None, ["Extracted"], "G", cfg, kinds={"Carousel", "Post"})]
    assert got == ["k", "p"]


def test_prepare_vision_lane_breaks_on_image_budget(monkeypatch, tmp_path):
    from insta_save.config.collections import CollectionsConfig
    cfg = CollectionsConfig(groups=("G",), collections={"c": {"group": "G", "extract": True}})
    stubs = [{"page_id": f"k{i}", "type": "Carousel", "collections": ["c"]} for i in range(3)]
    monkeypatch.setattr(enrich, "query_by_status", lambda env, status: stubs)
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
    monkeypatch.setattr(enrich, "query_by_status", lambda env, status: stubs)
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
    monkeypatch.setattr(enrich, "query_by_status", lambda env, status: stubs)
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
    monkeypatch.setattr(enrich, "query_by_status", lambda env, status: stubs)
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


# ===========================================================================
# drain_enrich_group
# ===========================================================================

import types as _types


def _fake_backend(automated=True, vision_capable=False):
    """Minimal backend stub for drain tests."""
    class _B:
        AUTOMATED = automated
        VISION_CAPABLE = vision_capable
        NAME = "test"

        @staticmethod
        def batch_budgets(run_cfg):
            return _types.SimpleNamespace(
                char_budget=100000, max_items=10,
                image_token_budget=50000,
            )

        @staticmethod
        def fill(env, run_cfg, enrich_dir):
            pass

    return _B()


def _fake_run_cfg():
    return _types.SimpleNamespace(
        output_language="english",
        enrich=_types.SimpleNamespace(model="test-model"),
    )


def test_drain_enrich_group_text_lane_stops_on_drained(tmp_path, monkeypatch):
    """Text lane: prepare returns 0 on first call -> DRAINED immediately, apply never called."""
    calls = {"prepare": 0, "fill": 0, "apply": 0}
    monkeypatch.setattr(enrich, "prepare", lambda *a, **k: (_ for _ in ()).throw(
        # Use a side-effect counter instead
        Exception()) if False else (calls.__setitem__("prepare", calls["prepare"] + 1) or 0))
    monkeypatch.setattr(enrich, "apply",
                        lambda *a, **k: calls.__setitem__("apply", calls["apply"] + 1) or {"written": 0, "failed": 0})

    env = _env(tmp_path)
    backend = _fake_backend(vision_capable=False)
    result = enrich.drain_enrich_group(env, _fake_run_cfg(), _Cols(), _vocab(), backend, "Hustling")

    assert calls["prepare"] == 1
    assert calls["apply"] == 0
    assert result["written"] == 0
    assert result["lanes"]["text"]["stop_reason"] == "drained"
    assert "vision" not in result["lanes"]


def test_drain_enrich_group_text_lane_loops_then_drains(tmp_path, monkeypatch):
    """Text lane: prepare returns 2 once then 0; apply writes 2; loop runs once then drains."""
    counts = iter([2, 0])
    calls = {"prepare": 0, "fill": 0, "apply": 0}
    monkeypatch.setattr(enrich, "prepare",
                        lambda *a, **k: (calls.__setitem__("prepare", calls["prepare"] + 1)
                                         or next(counts)))

    fill_calls = []

    class _B(_fake_backend(vision_capable=False).__class__):
        @staticmethod
        def fill(env, run_cfg, enrich_dir):
            fill_calls.append(1)

    monkeypatch.setattr(enrich, "apply",
                        lambda *a, **k: (calls.__setitem__("apply", calls["apply"] + 1)
                                         or {"written": 2, "failed": 0}))

    env = _env(tmp_path)
    result = enrich.drain_enrich_group(env, _fake_run_cfg(), _Cols(), _vocab(), _B(),
                                       "Hustling")

    assert calls["prepare"] == 2   # first call returns 2, second returns 0
    assert calls["apply"] == 1
    assert len(fill_calls) == 1
    assert result["written"] == 2
    assert result["lanes"]["text"]["stop_reason"] == "drained"


def test_drain_enrich_group_stops_on_no_progress(tmp_path, monkeypatch):
    """When apply writes 0 items, the no-progress guard must break the loop."""
    calls = {"prepare": 0, "apply": 0}
    monkeypatch.setattr(enrich, "prepare",
                        lambda *a, **k: (calls.__setitem__("prepare", calls["prepare"] + 1) or 3))
    monkeypatch.setattr(enrich, "apply",
                        lambda *a, **k: (calls.__setitem__("apply", calls["apply"] + 1)
                                         or {"written": 0, "failed": 3}))

    class _B(_fake_backend(vision_capable=False).__class__):
        @staticmethod
        def fill(env, run_cfg, enrich_dir):
            pass

    env = _env(tmp_path)
    result = enrich.drain_enrich_group(env, _fake_run_cfg(), _Cols(), _vocab(), _B(), "Hustling")

    assert calls["prepare"] == 1
    assert calls["apply"] == 1
    assert result["lanes"]["text"]["stop_reason"] == "no_progress"


def test_drain_enrich_group_skips_vision_lane_when_not_capable(tmp_path, monkeypatch):
    """Vision lane must not run when backend.VISION_CAPABLE is False."""
    seen_kinds = []
    monkeypatch.setattr(enrich, "prepare",
                        lambda *a, **k: (seen_kinds.append(k.get("kinds")) or 0))
    monkeypatch.setattr(enrich, "apply",
                        lambda *a, **k: {"written": 0, "failed": 0})

    env = _env(tmp_path)
    backend = _fake_backend(vision_capable=False)
    result = enrich.drain_enrich_group(env, _fake_run_cfg(), _Cols(), _vocab(), backend, "Hustling")

    # Only text lane ran — only {"Reel", "IGTV"} kinds seen
    assert all(kinds == {"Reel", "IGTV"} for kinds in seen_kinds), seen_kinds
    assert "vision" not in result["lanes"]


def test_drain_enrich_group_runs_vision_lane_when_capable(tmp_path, monkeypatch):
    """When backend.VISION_CAPABLE is True, the vision lane runs after the text lane."""
    seen_kinds = []
    monkeypatch.setattr(enrich, "prepare",
                        lambda *a, **k: (seen_kinds.append(frozenset(k.get("kinds") or [])) or 0))
    monkeypatch.setattr(enrich, "apply",
                        lambda *a, **k: {"written": 0, "failed": 0})

    env = _env(tmp_path)
    backend = _fake_backend(vision_capable=True)
    result = enrich.drain_enrich_group(env, _fake_run_cfg(), _Cols(), _vocab(), backend, "Hustling")

    # Both lanes triggered (both return 0 immediately — DRAINED on first call)
    assert frozenset({"Reel", "IGTV"}) in seen_kinds
    assert frozenset({"Carousel", "Post"}) in seen_kinds
    assert "text" in result["lanes"]
    assert "vision" in result["lanes"]


# ===========================================================================
# Spend guardrail — check_spend_cap wired per batch before backend.fill
# ===========================================================================

def _api_run_cfg(max_spend_usd=None):
    """run_cfg stub with api backend and optional spend cap."""
    return _types.SimpleNamespace(
        output_language="english",
        enrich=_types.SimpleNamespace(model="test-model", backend="api"),
        guardrails_max_spend_usd=max_spend_usd,
    )


def test_drain_enrich_group_api_raises_before_fill_when_over_cap(tmp_path, monkeypatch):
    """For the api backend, check_spend_cap must be called and raise SystemExit BEFORE
    backend.fill when the estimated spend exceeds the cap. fill must NOT be invoked."""
    # Write a prompt.txt large enough that estimate_spend_usd exceeds a tiny cap.
    # estimate: chars/4 tokens * $15/Mtok => 8000 chars / 4 = 2000 tok * $15/1e6 = $0.00003
    # Use 10_000 chars so estimate = 10000/4/1e6*15 = $0.0000375; cap = $0.00001 -> exceeds.
    enrich_dir = tmp_path / "enrich"
    enrich_dir.mkdir(parents=True, exist_ok=True)
    (enrich_dir / "prompt.txt").write_text("x" * 10_000, encoding="utf-8")

    fill_called = []

    prepare_calls = iter([1, 0])

    def _fake_prepare(*a, **k):
        n = next(prepare_calls)
        # On the first call (n=1), prompt.txt already exists with the oversized content.
        return n

    class _ApiBacked(_fake_backend().__class__):
        NAME = "api"
        @staticmethod
        def fill(env, run_cfg, enrich_dir):
            fill_called.append(1)

    monkeypatch.setattr(enrich, "prepare", _fake_prepare)
    monkeypatch.setattr(enrich, "apply",
                        lambda *a, **k: {"written": 1, "failed": 0})

    env = _env(tmp_path)
    run_cfg = _api_run_cfg(max_spend_usd=0.00001)  # tiny cap; 10k-char prompt exceeds it

    import pytest
    with pytest.raises(SystemExit):
        enrich.drain_enrich_group(env, run_cfg, _Cols(), _vocab(), _ApiBacked(), "Hustling")

    assert fill_called == [], "fill must not be called when spend cap is exceeded"


def test_drain_enrich_group_api_no_cap_proceeds_to_fill(tmp_path, monkeypatch):
    """For the api backend with no spend cap set, drain proceeds normally to fill."""
    enrich_dir = tmp_path / "enrich"
    enrich_dir.mkdir(parents=True, exist_ok=True)
    (enrich_dir / "prompt.txt").write_text("x" * 10_000, encoding="utf-8")

    fill_called = []
    prepare_calls = iter([1, 0])

    def _fake_prepare(*a, **k):
        n = next(prepare_calls)
        return n

    class _ApiBacked(_fake_backend().__class__):
        NAME = "api"
        @staticmethod
        def fill(env, run_cfg, enrich_dir):
            fill_called.append(1)

    monkeypatch.setattr(enrich, "prepare", _fake_prepare)
    monkeypatch.setattr(enrich, "apply",
                        lambda *a, **k: {"written": 1, "failed": 0})

    env = _env(tmp_path)
    run_cfg = _api_run_cfg(max_spend_usd=None)  # no cap -> no-op

    enrich.drain_enrich_group(env, run_cfg, _Cols(), _vocab(), _ApiBacked(), "Hustling")
    assert fill_called == [1], "fill must be called when no spend cap is set"


def test_drain_enrich_group_non_api_ignores_spend_cap(tmp_path, monkeypatch):
    """For a non-api backend (e.g. claude-code), check_spend_cap is a no-op even when
    the prompt is large and a cap is set — fill proceeds normally."""
    enrich_dir = tmp_path / "enrich"
    enrich_dir.mkdir(parents=True, exist_ok=True)
    (enrich_dir / "prompt.txt").write_text("x" * 10_000, encoding="utf-8")

    fill_called = []
    prepare_calls = iter([1, 0])

    def _fake_prepare(*a, **k):
        n = next(prepare_calls)
        return n

    class _NonApiBacked(_fake_backend().__class__):
        NAME = "claude-code"

        @staticmethod
        def fill(env, run_cfg, enrich_dir):
            fill_called.append(1)

    monkeypatch.setattr(enrich, "prepare", _fake_prepare)
    monkeypatch.setattr(enrich, "apply",
                        lambda *a, **k: {"written": 1, "failed": 0})

    env = _env(tmp_path)
    # run_cfg has a very tight spend cap but non-api backend -> must NOT raise
    run_cfg = _types.SimpleNamespace(
        output_language="english",
        enrich=_types.SimpleNamespace(model="test-model", backend="claude-code"),
        guardrails_max_spend_usd=0.00001,
    )

    enrich.drain_enrich_group(env, run_cfg, _Cols(), _vocab(), _NonApiBacked(), "Hustling")
    assert fill_called == [1], "fill must be called for non-api backend regardless of spend cap"


def test_drain_enrich_group_stops_between_batches(tmp_path, monkeypatch):
    """checkpoint() at the top of the while-True fires between batches: after apply writes,
    before the next prepare/fill.  RunStopped propagates out and only one batch runs."""
    from insta_save.orchestrator.run_control import RunControl, RunStopped

    rc = RunControl(mode="first-time")
    calls = {"prepare": 0, "apply": 0}

    def _prepare(*a, **k):
        calls["prepare"] += 1
        return 1  # one item batched — never 0, so drain-check cannot fire

    def _apply(*a, **k):
        calls["apply"] += 1
        rc.request_stop()  # simulate 'q' pressed during batch 1
        return {"written": 1}  # written>0 so no-progress guard does NOT fire

    monkeypatch.setattr(enrich, "prepare", _prepare)
    monkeypatch.setattr(enrich, "apply", _apply)

    backend = _fake_backend(vision_capable=False)

    with rc:
        with pytest.raises(RunStopped):
            enrich.drain_enrich_group(
                _env(tmp_path), _fake_run_cfg(), _Cols(), _vocab(), backend, "Hustling"
            )

    # checkpoint fires at the TOP of iteration 2 → stopped before a 2nd prepare
    assert calls["prepare"] == 1
    assert calls["apply"] == 1


def test_drain_retries_transient_fill_then_succeeds(tmp_path, monkeypatch):
    """A transient fill error is retried; a later attempt succeeds and the batch applies."""
    prep = iter([2, 0])  # one batch, then drained
    monkeypatch.setattr(enrich, "prepare", lambda *a, **k: next(prep))
    monkeypatch.setattr(enrich, "apply", lambda *a, **k: {"written": 2, "failed": 0})

    attempts = {"n": 0}

    class _B(_fake_backend(vision_capable=False).__class__):
        @staticmethod
        def fill(env, run_cfg, enrich_dir):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ValueError("Expecting ',' delimiter: line 24 column 156")  # transient

    result = enrich.drain_enrich_group(
        _env(tmp_path), _fake_run_cfg(), _Cols(), _vocab(), _B(), "Hustling",
        sleep=lambda *_: None)
    assert attempts["n"] == 2                      # failed once, retried, succeeded
    assert result["written"] == 2
    assert result["lanes"]["text"]["stop_reason"] == "drained"


def test_drain_marks_batch_failed_and_advances_when_retries_exhausted(tmp_path, monkeypatch):
    """A persistently-malformed batch is marked Failed and the lane advances (not abandoned)."""
    prep = iter([2, 0])  # one bad batch, then nothing left (Failed items leave the pool)
    monkeypatch.setattr(enrich, "prepare", lambda *a, **k: next(prep))
    monkeypatch.setattr(enrich, "apply", lambda *a, **k: {"written": 0, "failed": 0})

    # batch.json must exist for _mark_batch_failed to read it
    (tmp_path / "enrich").mkdir()
    (tmp_path / "enrich" / "batch.json").write_text(
        '{"group": "Hustling", "items": [{"page_id": "p1"}, {"page_id": "p2"}]}')

    failed = []
    monkeypatch.setattr(enrich, "mark_failed", lambda env, pid, notes: failed.append(pid))

    class _B(_fake_backend(vision_capable=False).__class__):
        @staticmethod
        def fill(env, run_cfg, enrich_dir):
            raise ValueError("always malformed")

    result = enrich.drain_enrich_group(
        _env(tmp_path), _fake_run_cfg(), _Cols(), _vocab(), _B(), "Hustling",
        sleep=lambda *_: None)
    assert failed == ["p1", "p2"]                  # whole batch marked Failed
    assert result["failed"] == 2
    assert result["lanes"]["text"]["stop_reason"] == "drained"


def test_drain_marks_batch_failed_prints_group_progress(tmp_path, monkeypatch, capsys):
    """On the exhausted-retries path, _print_group_progress fires with the failed count.

    group_total=5, batch of 2 items all fail: written=0, failed=2, remaining=3.
    The printed line must contain 'enriched 0/5', '~3 left', and 'failed 2'.
    """
    prep = iter([2, 0])
    monkeypatch.setattr(enrich, "prepare", lambda *a, **k: next(prep))
    monkeypatch.setattr(enrich, "apply", lambda *a, **k: {"written": 0, "failed": 0})

    (tmp_path / "enrich").mkdir()
    (tmp_path / "enrich" / "batch.json").write_text(
        '{"group": "Hustling", "items": [{"page_id": "p1"}, {"page_id": "p2"}]}')

    failed = []
    monkeypatch.setattr(enrich, "mark_failed", lambda env, pid, notes: failed.append(pid))

    class _B(_fake_backend(vision_capable=False).__class__):
        @staticmethod
        def fill(env, run_cfg, enrich_dir):
            raise ValueError("always malformed")

    result = enrich.drain_enrich_group(
        _env(tmp_path), _fake_run_cfg(), _Cols(), _vocab(), _B(), "Hustling",
        group_total=5, sleep=lambda *_: None)

    assert failed == ["p1", "p2"]
    assert result["failed"] == 2

    out = capsys.readouterr().out
    assert "enriched 0/5" in out
    assert "~3 left" in out
    assert "failed 2" in out


def test_drain_raises_terminal_backend_error(tmp_path, monkeypatch):
    """A terminal fill error (usage limit / auth) stops the run by propagating."""
    from insta_save.backends.base import TerminalBackendError
    monkeypatch.setattr(enrich, "prepare", lambda *a, **k: 2)
    monkeypatch.setattr(enrich, "apply", lambda *a, **k: {"written": 0, "failed": 0})

    class _B(_fake_backend(vision_capable=False).__class__):
        @staticmethod
        def fill(env, run_cfg, enrich_dir):
            raise RuntimeError("Claude usage limit reached. Your limit will reset at 5pm")

    with pytest.raises(TerminalBackendError):
        enrich.drain_enrich_group(
            _env(tmp_path), _fake_run_cfg(), _Cols(), _vocab(), _B(), "Hustling",
            sleep=lambda *_: None)


def test_drain_prints_group_progress_counter(tmp_path, monkeypatch, capsys):
    """When group_total is provided, drain prints a group-level enriched/total line after each batch."""
    prep = iter([3, 0])
    monkeypatch.setattr(enrich, "prepare", lambda *a, **k: next(prep))
    monkeypatch.setattr(enrich, "apply", lambda *a, **k: {"written": 3, "failed": 0})

    class _B(_fake_backend(vision_capable=False).__class__):
        @staticmethod
        def fill(env, run_cfg, enrich_dir):
            pass

    enrich.drain_enrich_group(_env(tmp_path), _fake_run_cfg(), _Cols(), _vocab(), _B(),
                              "Hustling", group_total=10, sleep=lambda *_: None)
    out = capsys.readouterr().out
    assert "enriched 3/10" in out
    assert "left" in out


def test_drain_no_group_progress_when_total_absent(tmp_path, monkeypatch, capsys):
    """When group_total is not passed (back-compat / --stage enrich), no progress line is printed."""
    prep = iter([3, 0])
    monkeypatch.setattr(enrich, "prepare", lambda *a, **k: next(prep))
    monkeypatch.setattr(enrich, "apply", lambda *a, **k: {"written": 3, "failed": 0})

    class _B(_fake_backend(vision_capable=False).__class__):
        @staticmethod
        def fill(env, run_cfg, enrich_dir):
            pass

    enrich.drain_enrich_group(_env(tmp_path), _fake_run_cfg(), _Cols(), _vocab(), _B(),
                              "Hustling", sleep=lambda *_: None)
    out = capsys.readouterr().out
    assert "enriched" not in out


def test_apply_scrubs_fabricated_url_from_summary_and_externals(tmp_path, monkeypatch):
    """apply() must scrub source-absent URLs from summary + externals before writing."""
    (tmp_path / "enrich").mkdir()
    (tmp_path / "enrich" / "batch.json").write_text(json.dumps(
        {"group": "Hustling",
         "items": [{"page_id": "p1", "source_id": "s1", "collections": ["hust-a"],
                    "caption": "a skill shared on GitHub", "transcript": "", "ocr_text": ""}]}))
    (tmp_path / "enrich" / "results.json").write_text(json.dumps([
        {"page_id": "p1", "source_id": "s1", "title": "T",
         "summary": "Xiaohei (github.com/helloianneo/x) is great.",
         "externals": "[Tools]\n  Xiaohei — github.com/helloianneo/x",
         "content_type": "tool", "topics": ["seo"]}]))
    written = {}
    monkeypatch.setattr(enrich, "write_enrichment",
                        lambda env, pid, fields, version: written.update({pid: fields}))
    enrich.apply(_env(tmp_path), vocab=_vocab(), model="m", collections_cfg=_Cols())
    f = written["p1"]
    assert "github.com/helloianneo/x" not in f["summary"]
    assert "github.com/helloianneo/x" not in f["externals"]
    assert "Xiaohei" in f["summary"]
