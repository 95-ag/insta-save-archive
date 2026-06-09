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
