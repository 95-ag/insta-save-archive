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


def test_bucket_items_orders_items_within_a_bucket_stably():
    a, b, c = (_item(s, ["fitness-workouts"]) for s in ("a", "b", "c"))
    one = dist.bucket_items([a, b, c], TAGS, CTYPES, max_items=10, char_budget=1000)
    two = dist.bucket_items([c, a, b], TAGS, CTYPES, max_items=10, char_budget=1000)
    assert [i["source_id"] for i in one[0].items] == ["a", "b", "c"]
    assert [i["source_id"] for i in two[0].items] == ["a", "b", "c"]


def test_bucket_items_slices_a_still_oversized_content_type_bucket():
    items = [_item(s, ["fitness-workouts", "tutorial"]) for s in ("a", "b", "c", "d", "e")]
    buckets = dist.bucket_items(items, TAGS, CTYPES, max_items=2, char_budget=1000)
    assert [b.name for b in buckets] == [
        "fitness-workouts · tutorial · part 1",
        "fitness-workouts · tutorial · part 2",
        "fitness-workouts · tutorial · part 3",
    ]
    assert [len(b.items) for b in buckets] == [2, 2, 1]


def test_bucket_items_gives_an_item_over_the_char_budget_its_own_part():
    items = [
        _item("a", ["fitness-workouts", "tutorial"], summary="x" * 500),
        _item("b", ["fitness-workouts", "tutorial"], summary="y" * 10),
    ]
    buckets = dist.bucket_items(items, TAGS, CTYPES, max_items=None, char_budget=100)
    assert [[i["source_id"] for i in b.items] for b in buckets] == [["a"], ["b"]]


PROPOSAL = """# Workouts
- cue one

```json
{"folded": ["abc"], "dropped": [{"source_id": "xyz", "reason": "meal prep"}]}
```
"""


def test_parse_proposal_splits_body_from_the_json_trailer():
    body, folded, dropped = dist.parse_proposal(PROPOSAL)
    assert body == "# Workouts\n- cue one"
    assert folded == ["abc"]
    assert dropped == [{"source_id": "xyz", "reason": "meal prep"}]


def test_parse_proposal_uses_the_last_fenced_block():
    text = '```python\ncode\n```\n# Sec\n\n```json\n{"folded": ["a"]}\n```'
    body, folded, _ = dist.parse_proposal(text)
    assert "# Sec" in body and folded == ["a"]


def test_parse_proposal_tolerates_raw_control_chars():
    text = '# Sec\n\n```json\n{"folded": ["a"], "dropped": [{"source_id": "b", "reason": "x\ty"}]}\n```'
    _, _, dropped = dist.parse_proposal(text)
    assert dropped[0]["reason"] == "x\ty"


def test_parse_proposal_defaults_missing_keys_to_empty():
    body, folded, dropped = dist.parse_proposal("# Sec\n\n```json\n{}\n```")
    assert folded == [] and dropped == []


def test_parse_proposal_raises_without_a_trailer():
    with pytest.raises(ValueError, match="no fenced JSON trailer"):
        dist.parse_proposal("# Sec\n- a")


def test_parse_proposal_raises_on_malformed_json():
    with pytest.raises(ValueError, match="malformed JSON trailer"):
        dist.parse_proposal('# Sec\n\n```json\n{"folded": [\n```')


def _trailer(payload):
    return "# Sec\n- a\n\n```json\n" + payload + "\n```"


@pytest.mark.parametrize(
    "payload",
    [
        '["not", "an", "object"]',
        '{"folded": "abc"}',
        '{"folded": [1, 2]}',
        '{"folded": [""]}',
        '{"dropped": "xyz"}',
        '{"dropped": ["not-a-dict"]}',
        '{"dropped": [{"reason": "missing id"}]}',
        '{"dropped": [{"source_id": 7, "reason": "x"}]}',
        '{"dropped": [{"source_id": "b", "reason": 5}]}',
    ],
)
def test_parse_proposal_rejects_a_wrong_shaped_trailer(payload):
    with pytest.raises(ValueError, match="distribute proposal: "):
        dist.parse_proposal(_trailer(payload))


def test_parse_proposal_treats_null_lists_as_empty():
    _, folded, dropped = dist.parse_proposal(_trailer('{"folded": null, "dropped": null}'))
    assert folded == [] and dropped == []


def test_parse_proposal_defaults_a_missing_reason_to_empty():
    _, _, dropped = dist.parse_proposal(_trailer('{"dropped": [{"source_id": "b"}]}'))
    assert dropped == [{"source_id": "b", "reason": ""}]


def test_plan_sections_replaces_an_existing_heading():
    ops = dist.plan_sections("# One\n- old", "# One\n- new")
    assert [(o.heading, o.action) for o in ops] == [("One", "replace")]
    assert ops[0].body_markdown == "- new"


def test_plan_sections_appends_a_new_heading():
    ops = dist.plan_sections("# One\n- a", "# Two\n- b")
    assert [(o.heading, o.action) for o in ops] == [("Two", "append")]


def test_plan_sections_omits_untouched_sections():
    ops = dist.plan_sections("# One\n- a\n# Two\n- b", "# One\n- new")
    assert [o.heading for o in ops] == ["One"]


def test_plan_sections_omits_a_section_echoed_back_unchanged():
    # fill() proposes the FULL merged body, so unchanged sections DO come back. A section
    # whose body is byte-identical must never be rewritten.
    ops = dist.plan_sections("# One\n- a\n# Two\n- b", "# One\n- new\n# Two\n- b")
    assert [o.heading for o in ops] == ["One"]


def test_plan_sections_rejects_a_duplicate_proposed_heading():
    with pytest.raises(ValueError, match="duplicate section heading"):
        dist.plan_sections("# One\n- a", "# One\n- x\n# One\n- y")


def test_render_diff_shows_the_change_and_names_the_section():
    out = dist.render_diff("# One\n- old", "# One\n- new")
    assert "One (current)" in out and "One (proposed)" in out
    assert "-- old" in out and "+- new" in out


def test_render_diff_reports_no_change_when_the_proposal_is_empty():
    assert "no changes" in dist.render_diff("# One\n- a", "").lower()
