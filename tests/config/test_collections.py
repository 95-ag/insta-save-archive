import json
import pytest
from insta_save.config import collections as colcfg

FIXTURE = {
    "groups": ["Hustling", "Biz", "Lifestyle", "uncategorized"],
    "collections": {
        "Coding": {"group": "Hustling", "extract": True},
        "Hustle Ideas": {"group": "Biz", "extract": True},
        "Makeup": {"group": "Lifestyle", "extract": False},
    },
}


def _load(tmp_path):
    p = tmp_path / "collections.json"
    p.write_text(json.dumps(FIXTURE), encoding="utf-8")
    return colcfg.load_collections(p)


def test_group_of_known_and_unknown(tmp_path):
    c = _load(tmp_path)
    assert c.group_of("Coding") == "Hustling"
    assert c.group_of("Nonexistent") == "uncategorized"


def test_collections_in_group(tmp_path):
    c = _load(tmp_path)
    assert c.collections_in_group("Hustling") == {"Coding"}


def test_extract_collections_in_group(tmp_path):
    c = _load(tmp_path)
    assert c.extract_collections_in_group("Hustling") == {"Coding"}   # extract=yes
    assert c.extract_collections_in_group("Lifestyle") == set()       # Makeup is extract=no


def test_group_order_index(tmp_path):
    c = _load(tmp_path)
    assert c.group_order_index("Hustling") < c.group_order_index("Biz")
    assert c.group_order_index("anything-unknown") == len(FIXTURE["groups"])  # last


def test_is_extract_path(tmp_path):
    c = _load(tmp_path)
    assert c.is_extract_path(["Coding"]) is True
    assert c.is_extract_path(["Makeup"]) is False
    assert c.is_extract_path(["Makeup", "Coding"]) is True  # any extract=yes


def test_enrich_group_is_last_extract_group(tmp_path):
    c = _load(tmp_path)
    # item spanning Hustling (extract) + Biz (extract): Biz is later in `groups`
    assert c.enrich_group(["Coding", "Hustle Ideas"]) == "Biz"
    # single-group item
    assert c.enrich_group(["Coding"]) == "Hustling"
    # all extract=no -> None (deterministic branch)
    assert c.enrich_group(["Makeup"]) is None


def test_extract_groups_of(tmp_path):
    c = _load(tmp_path)
    # cross-group item spanning Hustling (extract) + Biz (extract) -> both, in groups order
    assert c.extract_groups_of(["Coding", "Hustle Ideas"]) == ["Hustling", "Biz"]
    # single-group item
    assert c.extract_groups_of(["Coding"]) == ["Hustling"]
    # non-extract-only item (Lifestyle extract=False) -> []
    assert c.extract_groups_of(["Makeup"]) == []
    # enrich_group is still the last element (backward-compat)
    assert c.enrich_group(["Coding", "Hustle Ideas"]) == "Biz"


def test_legacy_flat_shape_raises(tmp_path):
    # v1 flat shape (top-level name -> {slug, group, ...}, no "collections" key)
    # must fail loudly, not silently yield an empty collections map.
    p = tmp_path / "collections.json"
    p.write_text(json.dumps({
        "Job Hunt": {"slug": "job-hunt", "group": "Hustling", "extract": True},
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="flat"):
        colcfg.load_collections(p)


from insta_save.config.collections import (
    load_collections, merge_discovered, write_collections, CollectionsConfig,
)


def _write(tmp_path, data):
    import json
    p = tmp_path / "collections.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_loader_surfaces_slug_and_numeric_id(tmp_path):
    p = _write(tmp_path, {
        "groups": ["Hustling", "uncategorized"],
        "collections": {"Dev": {"group": "Hustling", "extract": True,
                                 "slug": "dev", "numeric_id": "123"}},
    })
    cfg = load_collections(p)
    assert cfg.collections["Dev"]["slug"] == "dev"
    assert cfg.collections["Dev"]["numeric_id"] == "123"
    assert cfg.is_extract_path(["Dev"]) is True
    assert cfg.group_of("Dev") == "Hustling"


def test_loader_tolerates_missing_ids(tmp_path):
    p = _write(tmp_path, {"groups": ["uncategorized"],
                          "collections": {"X": {"group": "uncategorized", "extract": False}}})
    cfg = load_collections(p)
    assert cfg.collections["X"]["slug"] is None
    assert cfg.collections["X"]["numeric_id"] is None


def test_merge_discovered_adds_new_and_preserves_annotations():
    existing = {"groups": ["Hustling", "uncategorized"],
                "collections": {"Dev": {"group": "Hustling", "extract": True,
                                        "slug": "dev", "numeric_id": "1"}}}
    discovered = {"Dev": {"slug": "dev", "numeric_id": "1"},
                  "New": {"slug": "new", "numeric_id": "2"}}
    merged, new_names, missing = merge_discovered(existing, discovered)
    assert new_names == ["New"]
    assert missing == []
    assert merged["collections"]["Dev"] == {"group": "Hustling", "extract": True,
                                            "slug": "dev", "numeric_id": "1"}
    assert merged["collections"]["New"] == {"group": "uncategorized", "extract": False,
                                            "slug": "new", "numeric_id": "2"}


def test_merge_discovered_does_not_clobber_ids_with_none():
    existing = {"groups": ["uncategorized"],
                "collections": {"Dev": {"group": "uncategorized", "extract": True,
                                        "slug": "dev", "numeric_id": "1"}}}
    merged, _, _ = merge_discovered(existing, {"Dev": {"slug": None, "numeric_id": None}})
    assert merged["collections"]["Dev"]["slug"] == "dev"
    assert merged["collections"]["Dev"]["numeric_id"] == "1"


def test_merge_discovered_reports_missing():
    existing = {"groups": ["uncategorized"],
                "collections": {"Gone": {"group": "uncategorized", "extract": False,
                                         "slug": "gone", "numeric_id": "9"}}}
    merged, new_names, missing = merge_discovered(existing, {})
    assert missing == ["Gone"]
    assert "Gone" in merged["collections"]


def test_write_then_load_roundtrip(tmp_path):
    data = {"groups": ["Hustling", "uncategorized"],
            "collections": {"Dev": {"group": "Hustling", "extract": True,
                                    "slug": "dev", "numeric_id": "1"}}}
    p = tmp_path / "collections.json"
    write_collections(data, p)
    cfg = load_collections(p)
    assert cfg.collections["Dev"]["slug"] == "dev"
