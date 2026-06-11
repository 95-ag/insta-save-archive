import json
from insta_save.snapshots import write_snapshot, read_snapshot, is_reusable, snapshot_path


def test_write_then_read(tmp_path):
    write_snapshot(tmp_path, name="Dev", slug="dev", numeric_id="1",
                   posts=[{"shortcode": "abc", "url": "u"}], complete=True,
                   now="2026-06-11T00:00:00Z")
    snap = read_snapshot(tmp_path, "dev")
    assert snap["name"] == "Dev" and snap["complete"] is True
    assert snap["posts"] == [{"shortcode": "abc", "url": "u"}]


def test_read_missing_returns_none(tmp_path):
    assert read_snapshot(tmp_path, "nope") is None


def test_is_reusable_requires_complete_and_fresh():
    fresh_complete = {"complete": True, "crawled_at": "2026-06-11T00:00:00Z"}
    assert is_reusable(fresh_complete, max_age_min=360, now="2026-06-11T01:00:00Z") is True
    assert is_reusable({"complete": False, "crawled_at": "2026-06-11T00:00:00Z"},
                       max_age_min=360, now="2026-06-11T00:10:00Z") is False
    assert is_reusable(fresh_complete, max_age_min=30, now="2026-06-11T02:00:00Z") is False
    assert is_reusable(None, max_age_min=360, now="2026-06-11T00:00:00Z") is False
