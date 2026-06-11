import json

from insta_save.config.collections import CollectionsConfig
from insta_save.stages import deterministic as det


def test_slugify_collection_kebabs_and_strips():
    assert det.slugify_collection("Plants & Pets") == "plants-pets"
    assert det.slugify_collection("Clothing hacks") == "clothing-hacks"
    assert det.slugify_collection("BLR") == "blr"
    assert det.slugify_collection("boi saves") == "boi-saves"
    assert det.slugify_collection("  Interesting   buys!! ") == "interesting-buys"


def test_deterministic_tags_union_sorted_deduped():
    assert det.deterministic_tags(["Makeup", "Hair hacks", "Makeup"]) == ["hair-hacks", "makeup"]
    assert det.deterministic_tags([]) == []
    # a name that slugifies to empty is dropped
    assert det.deterministic_tags(["!!!", "Travel"]) == ["travel"]


def test_template_title_uses_alpha_first_collection_and_author():
    item = {"collections": ["Travel", "Makeup"], "author": "dinarakasko"}
    assert det.template_title(item) == "Makeup — dinarakasko"


def test_template_title_fallbacks():
    assert det.template_title({"collections": ["Makeup"], "author": None}) == "Makeup"
    assert det.template_title({"collections": [], "author": "x", "title": "T — abc"}) == "T — abc"
    assert det.template_title({"collections": [], "author": None, "source_id": "abc"}) == "abc"


def _cfg():
    return CollectionsConfig(groups=("Lifestyle", "uncategorized"), collections={
        "Makeup": {"group": "Lifestyle", "extract": False, "slug": "m", "numeric_id": "1"},
        "Dev": {"group": "Hustling", "extract": True, "slug": "d", "numeric_id": "2"}})


def test_tag_item_skips_extract_path():
    item = {"page_id": "p", "collections": ["Dev"], "author": "a"}
    assert det._tag_item(None, item, _cfg()) == "skipped_extract_path"


def test_tag_item_writes_tagged(monkeypatch):
    writes = []
    monkeypatch.setattr(det, "write_deterministic",
                        lambda env, pid, title, tags, ver: writes.append((pid, title, tags, ver)))
    item = {"page_id": "p1", "collections": ["Makeup"], "author": "dinarakasko"}
    assert det._tag_item("ENV", item, _cfg()) == "tagged"
    assert writes == [("p1", "Makeup — dinarakasko", ["makeup"], det.DETERMINISTIC_VERSION)]


def test_build_title_prompt_includes_language_and_caption():
    items = [{"page_id": "p1", "source_id": "s1", "author": "a",
              "collections": ["Makeup"], "caption": "glowy look"}]
    out = det.build_title_prompt(items, "Write in {language}.", "english")
    assert "Write in english." in out
    assert "s1" in out and "glowy look" in out and "Makeup" in out


def test_apply_falls_back_to_template_when_result_missing(tmp_path, monkeypatch):
    d = tmp_path / "deterministic"
    d.mkdir()
    (d / "batch.json").write_text(json.dumps({"group": "Lifestyle", "language": "english", "items": [
        {"page_id": "p1", "source_id": "s1", "author": "dinarakasko",
         "collections": ["Makeup"], "caption": "x", "tags": ["makeup"]},
        {"page_id": "p2", "source_id": "s2", "author": "x",
         "collections": ["Travel"], "caption": "y", "tags": ["travel"]}]}), encoding="utf-8")
    # results only cover p1 → p2 must fall back to its template title
    (d / "results.json").write_text(json.dumps([
        {"page_id": "p1", "source_id": "s1", "title": "Glowy summer makeup"}]), encoding="utf-8")

    env = type("E", (), {"tmp_dir": str(tmp_path), "notion_write_delay": 0})()
    writes = []
    monkeypatch.setattr(det, "write_deterministic",
                        lambda e, pid, title, tags, ver: writes.append((pid, title, tags)))
    counts = det.apply(env, progress=None)

    assert counts == {"written": 2, "failed": 0}
    assert ("p1", "Glowy summer makeup", ["makeup"]) in writes
    assert ("p2", "Travel — x", ["travel"]) in writes  # template fallback
    assert not (d / "results.json").exists()  # cleaned on full success


def test_prepare_caption_split(tmp_path, monkeypatch):
    # one captioned item (→ batch) + one caption-less item (→ finalized immediately)
    contents = {
        "p1": {"page_id": "p1", "source_id": "s1", "author": "a",
               "collections": ["Makeup"], "caption": "glowy look"},
        "p2": {"page_id": "p2", "source_id": "s2", "author": "b",
               "collections": ["Travel"], "caption": None},
    }
    monkeypatch.setattr(det, "_deterministic_stubs",
                        lambda env, group, cfg: [{"page_id": "p1"}, {"page_id": "p2"}])
    monkeypatch.setattr(det, "get_page_content", lambda env, pid: contents[pid])
    finalized = []
    monkeypatch.setattr(det, "write_deterministic",
                        lambda e, pid, title, tags, ver: finalized.append((pid, title, tags)))

    env = type("E", (), {"tmp_dir": str(tmp_path)})()
    out = det.prepare(env, group="Lifestyle", collections_cfg=None,
                      language="english", prompt_template="Lang={language}", max_items=None)

    assert out == {"batched": 1, "finalized_template": 1}
    # caption-less p2 finalized immediately with its template title + slug tag
    assert finalized == [("p2", "Travel — b", ["travel"])]
    # captioned p1 written to batch.json with precomputed tags, NOT finalized
    import json as _json
    batch = _json.loads((tmp_path / "deterministic" / "batch.json").read_text(encoding="utf-8"))
    assert [i["page_id"] for i in batch["items"]] == ["p1"]
    assert batch["items"][0]["tags"] == ["makeup"]
    assert batch["language"] == "english"


def test_apply_raises_when_batch_missing(tmp_path):
    import pytest
    d = tmp_path / "deterministic"
    d.mkdir()
    # results.json present but batch.json absent
    (d / "results.json").write_text("[]", encoding="utf-8")
    env = type("E", (), {"tmp_dir": str(tmp_path), "notion_write_delay": 0})()
    with pytest.raises(FileNotFoundError):
        det.apply(env, progress=None)
