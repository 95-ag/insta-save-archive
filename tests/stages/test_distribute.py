import pytest

from insta_save.stages import distribute as dist

TAGS = {"fitness-workouts", "mobility-posture"}
CTYPES = ["tutorial", "tips-hacks", "explainer"]


def _item(sid, tags, summary="s", title=None):
    return {
        "page_id": f"p-{sid}",
        "source_id": sid,
        "title": title or f"t-{sid}",
        "summary": summary,
        "tags": tags,
        "ig_link": f"https://ig/{sid}",
        "knowledge_base": [],
    }


def test_bucket_items_groups_by_routing_tag_combo():
    items = [
        _item("a", ["fitness-workouts"]),
        _item("b", ["mobility-posture"]),
        _item("c", ["fitness-workouts", "mobility-posture"]),
    ]
    buckets = dist.bucket_items(items, TAGS, CTYPES, max_items=10, char_budget=1000)
    assert {b.name for b in buckets} == {
        "fitness-workouts",
        "mobility-posture",
        "fitness-workouts + mobility-posture",
    }


def test_bucket_items_ignores_non_routing_tags_in_the_key():
    items = [_item("a", ["fitness-workouts", "tutorial"]), _item("b", ["fitness-workouts"])]
    buckets = dist.bucket_items(items, TAGS, CTYPES, max_items=10, char_budget=1000)
    assert len(buckets) == 1 and len(buckets[0].items) == 2


def test_bucket_items_splits_an_oversized_combo_by_content_type():
    items = [
        _item("a", ["fitness-workouts", "tutorial"]),
        _item("b", ["fitness-workouts", "explainer"]),
    ]
    buckets = dist.bucket_items(items, TAGS, CTYPES, max_items=1, char_budget=1000)
    assert sorted(b.name for b in buckets) == [
        "fitness-workouts · explainer",
        "fitness-workouts · tutorial",
    ]


def test_bucket_items_splits_on_char_budget_too():
    items = [
        _item("a", ["fitness-workouts", "tutorial"], summary="x" * 100),
        _item("b", ["fitness-workouts", "explainer"], summary="y" * 100),
    ]
    buckets = dist.bucket_items(items, TAGS, CTYPES, max_items=10, char_budget=50)
    assert len(buckets) == 2


def test_bucket_items_is_stable_across_input_order():
    a, b = _item("a", ["fitness-workouts"]), _item("b", ["mobility-posture"])
    one = [x.name for x in dist.bucket_items([a, b], TAGS, CTYPES, max_items=10, char_budget=1000)]
    two = [x.name for x in dist.bucket_items([b, a], TAGS, CTYPES, max_items=10, char_budget=1000)]
    assert one == two


def test_chunk_buckets_keeps_a_fitting_page_as_one_call():
    buckets = [
        dist.Bucket("x", [_item("a", ["fitness-workouts"])]),
        dist.Bucket("y", [_item("b", ["mobility-posture"])]),
    ]
    assert len(dist.chunk_buckets(buckets, max_items=10, char_budget=1000)) == 1


def test_chunk_buckets_splits_when_over_budget():
    buckets = [
        dist.Bucket("x", [_item("a", ["fitness-workouts"])]),
        dist.Bucket("y", [_item("b", ["mobility-posture"])]),
    ]
    assert len(dist.chunk_buckets(buckets, max_items=1, char_budget=1000)) == 2
