import copy
import json
import re
import types

import pytest

from insta_save.backends.base import TerminalBackendError
from insta_save.config.routes import Routes
from insta_save.markdown_blocks import blocks_to_markdown
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


def test_parse_proposal_leaves_a_missing_reason_none():
    _, _, dropped = dist.parse_proposal(_trailer('{"dropped": [{"source_id": "b"}]}'))
    assert dropped == [{"source_id": "b", "reason": None}]


def test_parse_proposal_leaves_a_null_reason_none():
    _, _, dropped = dist.parse_proposal(
        _trailer('{"dropped": [{"source_id": "b", "reason": null}]}')
    )
    assert dropped == [{"source_id": "b", "reason": None}]


def test_parse_proposal_keeps_an_empty_string_reason_as_given():
    _, _, dropped = dist.parse_proposal(_trailer('{"dropped": [{"source_id": "b", "reason": ""}]}'))
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


class _Env:
    tmp_dir = None  # set per test
    notion_token = "t"
    notion_database_id = "db"
    notion_knowledge_database_id = "kdb"


class _Vocab:
    content_types = CTYPES


class _Budgets:
    max_items = 10
    char_budget = 10000


def _env(tmp_path):
    env = _Env()
    env.tmp_dir = str(tmp_path)
    return env


def _h1(text, bid="h1"):
    return {
        "id": bid,
        "type": "heading_1",
        "heading_1": {"rich_text": [{"plain_text": text, "annotations": {"bold": False}}]},
    }


def _bullet(text, bid):
    return {
        "id": bid,
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"plain_text": text, "annotations": {"bold": False}}]},
    }


def _patch_prepare(monkeypatch, *, items, blocks=(), distributed=None):
    monkeypatch.setattr(dist, "get_knowledge_page", lambda env, name: "kp")
    monkeypatch.setattr(dist, "query_routed_for_target", lambda env, target: list(items))
    monkeypatch.setattr(dist, "get_page_blocks", lambda env, pid: list(blocks))

    def _write_distributed(env, pid, version, notes=None):
        # `distributed or []` breaks when the caller passes an empty list (falsy) as the
        # sink to record into — it silently appends to a throwaway list instead. Guard on
        # identity with the default instead.
        if distributed is not None:
            distributed.append(pid)

    monkeypatch.setattr(dist, "write_distributed", _write_distributed)


ROUTES = Routes(
    by_tag=[("fitness-workouts", "Page A"), ("mobility-posture", "Page A"), ("web-dev", "Page C")]
)


def test_prepare_stages_pending_items(tmp_path, monkeypatch):
    env = _env(tmp_path)
    _patch_prepare(
        monkeypatch,
        items=[_item("a", ["fitness-workouts"])],
        blocks=[_h1("Workouts"), _bullet("old", "b1")],
    )
    n = dist.prepare(
        env, target="Page A", routes=ROUTES, vocab=_Vocab(), budgets=_Budgets(), version="v"
    )
    d = dist.staging_dir(env, "Page A")
    assert n == 1
    assert json.loads((d / "batch.json").read_text())["page_id"] == "kp"
    assert (d / "current.md").read_text() == "# Workouts\n- old"


def test_prepare_reconciles_already_related_items(tmp_path, monkeypatch):
    env = _env(tmp_path)
    done = _item("a", ["fitness-workouts"])
    done["knowledge_base"] = ["kp"]
    distributed = []
    _patch_prepare(monkeypatch, items=[done], distributed=distributed)
    assert (
        dist.prepare(
            env, target="Page A", routes=ROUTES, vocab=_Vocab(), budgets=_Budgets(), version="v"
        )
        == 0
    )
    assert distributed == ["p-a"]


def test_prepare_returns_zero_when_nothing_is_pending(tmp_path, monkeypatch):
    env = _env(tmp_path)
    _patch_prepare(monkeypatch, items=[])
    assert (
        dist.prepare(
            env, target="Page B", routes=ROUTES, vocab=_Vocab(), budgets=_Budgets(), version="v"
        )
        == 0
    )


def test_prepare_clears_a_stale_proposal_from_a_previous_cycle(tmp_path, monkeypatch):
    env = _env(tmp_path)
    d = dist.staging_dir(env, "Page A")
    for name in ("proposed.md", "proposal.json", "diff.txt", "prompt.txt"):
        (d / name).write_text("stale", encoding="utf-8")
    _patch_prepare(
        monkeypatch,
        items=[_item("a", ["fitness-workouts"])],
        blocks=[_h1("Workouts"), _bullet("old", "b1")],
    )
    dist.prepare(
        env, target="Page A", routes=ROUTES, vocab=_Vocab(), budgets=_Budgets(), version="v"
    )
    for name in ("proposed.md", "proposal.json", "diff.txt", "prompt.txt"):
        assert not (d / name).exists()
    assert (d / "batch.json").exists() and (d / "current.md").exists()


def test_prepare_clears_staging_even_when_nothing_is_pending(tmp_path, monkeypatch):
    env = _env(tmp_path)
    d = dist.staging_dir(env, "Page A")
    (d / "batch.json").write_text("stale", encoding="utf-8")
    (d / "proposal.json").write_text("stale", encoding="utf-8")
    _patch_prepare(monkeypatch, items=[])
    assert (
        dist.prepare(
            env, target="Page A", routes=ROUTES, vocab=_Vocab(), budgets=_Budgets(), version="v"
        )
        == 0
    )
    assert not (d / "batch.json").exists() and not (d / "proposal.json").exists()


def test_prepare_leaves_unrelated_files_in_the_staging_dir(tmp_path, monkeypatch):
    env = _env(tmp_path)
    d = dist.staging_dir(env, "Page A")
    (d / "notes.txt").write_text("keep me", encoding="utf-8")
    _patch_prepare(monkeypatch, items=[])
    dist.prepare(
        env, target="Page A", routes=ROUTES, vocab=_Vocab(), budgets=_Budgets(), version="v"
    )
    assert (d / "notes.txt").read_text(encoding="utf-8") == "keep me"


def test_content_blocks_starts_at_the_first_h1():
    blocks = [
        {"id": "toc", "type": "table_of_contents", "table_of_contents": {}},
        {"id": "d", "type": "divider", "divider": {}},
        _h1("Workouts"),
        _bullet("a", "b1"),
    ]
    assert [b["id"] for b in dist._content_blocks(blocks)] == ["h1", "b1"]


def test_content_blocks_refuses_when_quick_access_is_an_h1():
    with pytest.raises(RuntimeError, match="Quick Access"):
        dist._content_blocks([_h1("🔗 Quick Access")])


TEMPLATE = (
    "PAGE: {page_name}\nNUMBERING: {numbering}\nSECTIONS: {sections}\n"
    "CURRENT:\n{current}\nITEMS:\n{buckets}\n"
)


def _fill_env(tmp_path, monkeypatch, items, current_md):
    env = _env(tmp_path)
    d = dist.staging_dir(env, "Page A")
    (d / "batch.json").write_text(
        json.dumps({"target": "Page A", "page_id": "kp", "items": items}), encoding="utf-8"
    )
    (d / "current.md").write_text(current_md, encoding="utf-8")
    return env, d


def test_fill_stages_the_proposal_and_diff(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, d = _fill_env(tmp_path, monkeypatch, items, "# Workouts\n- old")
    reply = '# Workouts\n- new\n\n```json\n{"folded": ["a"]}\n```'
    out = dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=lambda prompt, model: reply,
    )
    assert out == {"calls": 1, "folded": 1, "dropped": 0}
    assert (d / "proposed.md").read_text() == "# Workouts\n- new"
    assert json.loads((d / "proposal.json").read_text())["folded"] == ["a"]
    assert "+- new" in (d / "diff.txt").read_text()


def test_fill_chains_over_budget_calls_on_the_merged_body(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"]), _item("b", ["mobility-posture"])]
    env, d = _fill_env(tmp_path, monkeypatch, items, "")
    seen = []
    replies = [
        '# Workouts\n- a\n\n```json\n{"folded": ["a"]}\n```',
        '# Mobility\n- b\n\n```json\n{"folded": ["b"]}\n```',
    ]

    def _call(prompt, model):
        seen.append(prompt)
        return replies[len(seen) - 1]

    budgets = types.SimpleNamespace(max_items=1, char_budget=10000)
    out = dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=budgets,
        model="m",
        template=TEMPLATE,
        call=_call,
    )
    assert out["calls"] == 2
    assert "- a" in seen[1]  # the second call sees the first call's merged body
    assert (d / "proposed.md").read_text() == "# Workouts\n- a\n# Mobility\n- b"


def test_fill_retries_a_malformed_proposal(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _fill_env(tmp_path, monkeypatch, items, "")
    replies = iter(["no trailer here", '# W\n- a\n\n```json\n{"folded": ["a"]}\n```'])
    out = dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=lambda p, m: next(replies),
        sleep=lambda s: None,
    )
    assert out["calls"] == 1 and out["folded"] == 1


def test_fill_stops_immediately_on_a_terminal_backend_error(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _fill_env(tmp_path, monkeypatch, items, "")

    def _boom(prompt, model):
        raise RuntimeError("OAuth token has expired")

    with pytest.raises(TerminalBackendError):
        dist.fill(
            env,
            target="Page A",
            routes=ROUTES,
            vocab=_Vocab(),
            budgets=_Budgets(),
            model="m",
            template=TEMPLATE,
            call=_boom,
            sleep=lambda s: None,
        )


def test_fill_scrubs_a_fabricated_url(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, d = _fill_env(tmp_path, monkeypatch, items, "")
    reply = '# W\n- see https://invented.example.com now\n\n```json\n{"folded": ["a"]}\n```'
    dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=lambda p, m: reply,
    )
    assert "invented.example.com" not in (d / "proposed.md").read_text()


def test_fill_keeps_a_link_that_came_from_an_item(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, d = _fill_env(tmp_path, monkeypatch, items, "")
    reply = '# W\n- [t-a](https://ig/a)\n\n```json\n{"folded": ["a"]}\n```'
    dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=lambda p, m: reply,
    )
    assert "https://ig/a" in (d / "proposed.md").read_text()


def test_render_prompt_fills_every_placeholder():
    buckets = [dist.Bucket("fitness-workouts", [_item("a", ["fitness-workouts"])])]
    out = dist.render_prompt(
        TEMPLATE, target="Page A", current_md="# 1 Workouts\n- a", buckets=buckets
    )
    for placeholder in ("{page_name}", "{numbering}", "{sections}", "{current}", "{buckets}"):
        assert placeholder not in out
    assert "Page A" in out and "[a]" in out and "1 Workouts" in out


def test_render_prompt_detects_numbered_headings():
    out = dist.render_prompt(TEMPLATE, target="X", current_md="# 1 Foundations", buckets=[])
    assert "mirror the same scheme" in out


def test_render_prompt_detects_unnumbered_headings():
    out = dist.render_prompt(TEMPLATE, target="X", current_md="# Workouts", buckets=[])
    assert "do not add numbers" in out


def test_render_prompt_treats_the_first_time_anchor_as_empty():
    out = dist.render_prompt(TEMPLATE, target="Page A", current_md="# ", buckets=[])
    assert "(empty — this page has no content yet)" in out
    assert "(none — this page has no sections yet)" in out


def test_render_buckets_omits_none_fields():
    item = {
        "page_id": "p-a",
        "source_id": "a",
        "title": None,
        "summary": None,
        "tags": ["fitness-workouts"],
        "ig_link": None,
        "knowledge_base": [],
    }
    bucket = dist.Bucket("fitness-workouts", [item])
    out = dist.render_prompt(TEMPLATE, target="Page A", current_md="", buckets=[bucket])
    assert "[a]" in out
    assert "None" not in out


def test_prompt_rule_7_covers_only_added_or_changed_entries():
    from pathlib import Path

    text = Path("prompts/distribute_v2.1.txt").read_text(encoding="utf-8")
    assert "every entry you add or change" in text


def test_prompt_rule_7_explains_a_missing_title_or_url():
    from pathlib import Path

    text = Path("prompts/distribute_v2.1.txt").read_text(encoding="utf-8")
    assert "use its source_id as the link text" in text
    assert "cite it without a link" in text


def test_prompt_v2_1_guides_toward_few_broad_sections():
    from pathlib import Path

    text = Path("prompts/distribute_v2.1.txt").read_text(encoding="utf-8")
    assert "few and broad" in text
    assert "single tip" in text


def test_fill_leaves_an_untouched_section_byte_identical(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    current_md = "# Workouts\n- old\n# Other\n- call useEffect() on mount\n- ratio 3 : 1"
    env, d = _fill_env(tmp_path, monkeypatch, items, current_md)
    reply = '# Workouts\n- new\n\n```json\n{"folded": ["a"]}\n```'
    dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=lambda p, m: reply,
    )
    proposed = (d / "proposed.md").read_text()
    assert "# Other\n- call useEffect() on mount\n- ratio 3 : 1" in proposed
    assert "Other" not in (d / "diff.txt").read_text()


def test_fill_leaves_an_echoed_untouched_section_byte_identical(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    current_md = "# Workouts\n- old\n# Other\n- call useEffect() on mount\n- ratio 3 : 1"
    env, d = _fill_env(tmp_path, monkeypatch, items, current_md)
    reply = (
        "# Workouts\n- new\n# Other\n- call useEffect() on mount\n- ratio 3 : 1"
        '\n\n```json\n{"folded": ["a"]}\n```'
    )
    dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=lambda p, m: reply,
    )
    proposed = (d / "proposed.md").read_text()
    assert "# Other\n- call useEffect() on mount\n- ratio 3 : 1" in proposed
    assert "Other" not in (d / "diff.txt").read_text()


def test_fill_scrub_leaves_unrelated_lines_in_the_same_section_byte_identical(
    tmp_path, monkeypatch
):
    items = [_item("a", ["fitness-workouts"])]
    current_md = "# Main\n- call useEffect() on mount\n- ratio 3 : 1"
    env, d = _fill_env(tmp_path, monkeypatch, items, current_md)
    reply = (
        "# Main\n- call useEffect() on mount\n- ratio 3 : 1"
        "\n- see https://invented.example.com now"
        '\n\n```json\n{"folded": ["a"]}\n```'
    )
    dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=lambda p, m: reply,
    )
    proposed = (d / "proposed.md").read_text()
    assert "- call useEffect() on mount" in proposed
    assert "- ratio 3 : 1" in proposed
    assert "invented.example.com" not in proposed


def test_fill_scrub_leaves_an_untouched_section_byte_identical_despite_a_scrub_elsewhere(
    tmp_path, monkeypatch
):
    items = [_item("a", ["fitness-workouts"])]
    current_md = "# Main\n- a\n# Other\n- keep() this"
    env, d = _fill_env(tmp_path, monkeypatch, items, current_md)
    reply = (
        "# Main\n- a\n- see https://invented.example.com now"
        "\n# Other\n- keep() this"
        '\n\n```json\n{"folded": ["a"]}\n```'
    )
    dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=lambda p, m: reply,
    )
    proposed = (d / "proposed.md").read_text()
    assert "- keep() this" in proposed
    assert "Other" not in (d / "diff.txt").read_text()


def test_fill_retries_a_reply_with_an_unknown_folded_id(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _fill_env(tmp_path, monkeypatch, items, "")
    replies = iter(
        [
            '# W\n- a\n\n```json\n{"folded": ["zzz"]}\n```',
            '# W\n- a\n\n```json\n{"folded": ["a"]}\n```',
        ]
    )
    calls = []

    def _call(p, m):
        calls.append(1)
        return next(replies)

    out = dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=_call,
        sleep=lambda s: None,
    )
    assert out["calls"] == 1 and out["folded"] == 1
    assert len(calls) == 2


def test_fill_retries_a_reply_with_a_duplicate_folded_id(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _fill_env(tmp_path, monkeypatch, items, "")
    replies = iter(
        [
            '# W\n- a\n\n```json\n{"folded": ["a", "a"]}\n```',
            '# W\n- a\n\n```json\n{"folded": ["a"]}\n```',
        ]
    )
    calls = []

    def _call(p, m):
        calls.append(1)
        return next(replies)

    out = dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=_call,
        sleep=lambda s: None,
    )
    assert out["calls"] == 1 and out["folded"] == 1
    assert len(calls) == 2


def test_fill_retries_a_reply_with_an_unknown_dropped_id(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _fill_env(tmp_path, monkeypatch, items, "")
    replies = iter(
        [
            '# W\n- a\n\n```json\n{"dropped": [{"source_id": "zzz", "reason": "x"}]}\n```',
            '# W\n- a\n\n```json\n{"folded": ["a"]}\n```',
        ]
    )
    calls = []

    def _call(p, m):
        calls.append(1)
        return next(replies)

    out = dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=_call,
        sleep=lambda s: None,
    )
    assert out["calls"] == 1 and out["folded"] == 1
    assert len(calls) == 2


def test_fill_retries_a_reply_with_a_duplicate_dropped_id(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _fill_env(tmp_path, monkeypatch, items, "")
    replies = iter(
        [
            '# W\n- a\n\n```json\n{"dropped": ['
            '{"source_id": "a", "reason": "x"}, {"source_id": "a", "reason": "y"}]}\n```',
            '# W\n- a\n\n```json\n{"folded": ["a"]}\n```',
        ]
    )
    calls = []

    def _call(p, m):
        calls.append(1)
        return next(replies)

    out = dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=_call,
        sleep=lambda s: None,
    )
    assert out["calls"] == 1 and out["folded"] == 1
    assert len(calls) == 2


def test_fill_retries_a_reply_with_an_id_both_folded_and_dropped(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _fill_env(tmp_path, monkeypatch, items, "")
    replies = iter(
        [
            '# W\n- a\n\n```json\n{"folded": ["a"], '
            '"dropped": [{"source_id": "a", "reason": "x"}]}\n```',
            '# W\n- a\n\n```json\n{"folded": ["a"]}\n```',
        ]
    )
    calls = []

    def _call(p, m):
        calls.append(1)
        return next(replies)

    out = dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=_call,
        sleep=lambda s: None,
    )
    assert out["calls"] == 1 and out["folded"] == 1
    assert len(calls) == 2


def test_fill_retries_a_reply_with_folded_items_but_no_section(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _fill_env(tmp_path, monkeypatch, items, "")
    replies = iter(
        [
            'no heading here\n\n```json\n{"folded": ["a"]}\n```',
            '# W\n- a\n\n```json\n{"folded": ["a"]}\n```',
        ]
    )
    calls = []

    def _call(p, m):
        calls.append(1)
        return next(replies)

    out = dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=_call,
        sleep=lambda s: None,
    )
    assert out["calls"] == 1 and out["folded"] == 1
    assert len(calls) == 2


def test_fill_retries_a_reply_with_a_repeated_heading(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _fill_env(tmp_path, monkeypatch, items, "")
    replies = iter(
        [
            '# W\n- a\n# W\n- b\n\n```json\n{"folded": ["a"]}\n```',
            '# W\n- a\n\n```json\n{"folded": ["a"]}\n```',
        ]
    )
    calls = []

    def _call(p, m):
        calls.append(1)
        return next(replies)

    out = dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=_call,
        sleep=lambda s: None,
    )
    assert out["calls"] == 1 and out["folded"] == 1
    assert len(calls) == 2


def test_fill_retries_a_reply_with_content_lost_before_the_first_heading(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _fill_env(tmp_path, monkeypatch, items, "")
    replies = iter(
        [
            '## topic\n- stuff\n# X\n- y\n\n```json\n{"folded": ["a"]}\n```',
            '# X\n- y\n\n```json\n{"folded": ["a"]}\n```',
        ]
    )
    calls = []

    def _call(p, m):
        calls.append(1)
        return next(replies)

    out = dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=_call,
        sleep=lambda s: None,
    )
    assert out["calls"] == 1 and out["folded"] == 1
    assert len(calls) == 2


def test_fill_retries_a_reply_with_an_empty_heading(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _fill_env(tmp_path, monkeypatch, items, "")
    replies = iter(
        [
            '# \n- stray\n\n```json\n{"folded": ["a"]}\n```',
            '# W\n- a\n\n```json\n{"folded": ["a"]}\n```',
        ]
    )
    calls = []

    def _call(p, m):
        calls.append(1)
        return next(replies)

    out = dist.fill(
        env,
        target="Page A",
        routes=ROUTES,
        vocab=_Vocab(),
        budgets=_Budgets(),
        model="m",
        template=TEMPLATE,
        call=_call,
        sleep=lambda s: None,
    )
    assert out["calls"] == 1 and out["folded"] == 1
    assert len(calls) == 2


def test_fill_raises_after_three_consecutive_bad_replies(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _fill_env(tmp_path, monkeypatch, items, "")
    calls = []

    def _call(p, m):
        calls.append(1)
        return '# W\n- a\n\n```json\n{"folded": ["zzz"]}\n```'

    with pytest.raises(RuntimeError):
        dist.fill(
            env,
            target="Page A",
            routes=ROUTES,
            vocab=_Vocab(),
            budgets=_Budgets(),
            model="m",
            template=TEMPLATE,
            call=_call,
            sleep=lambda s: None,
        )
    assert len(calls) == 3


def _staged(tmp_path, *, items, current_md, proposed_md, proposal):
    env = _env(tmp_path)
    d = dist.staging_dir(env, "Page A")
    (d / "batch.json").write_text(
        json.dumps({"target": "Page A", "page_id": "kp", "items": items}), encoding="utf-8"
    )
    (d / "current.md").write_text(current_md, encoding="utf-8")
    (d / "proposed.md").write_text(proposed_md, encoding="utf-8")
    (d / "proposal.json").write_text(json.dumps(proposal), encoding="utf-8")
    return env, d


def _patch_apply(monkeypatch, blocks, *, calls):
    monkeypatch.setattr(dist, "get_page_blocks", lambda env, pid: list(blocks))
    monkeypatch.setattr(
        dist,
        "replace_section_blocks",
        lambda env, pid, **kw: calls.setdefault("sections", []).append(kw) or "tail",
    )
    monkeypatch.setattr(
        dist,
        "set_heading_text",
        lambda env, bid, text: calls.setdefault("headings", []).append((bid, text)),
    )
    monkeypatch.setattr(
        dist,
        "set_knowledge_relation",
        lambda env, pid, kp: calls.setdefault("relations", []).append(pid) or True,
    )
    monkeypatch.setattr(
        dist,
        "write_distributed",
        lambda env, pid, version, notes=None: calls.setdefault("distributed", []).append(
            (pid, notes)
        ),
    )
    monkeypatch.setattr(
        dist,
        "write_misrouted",
        lambda env, pid, reason, version: calls.setdefault("misrouted", []).append((pid, reason)),
    )


def test_apply_replaces_a_changed_section_and_links_folded_items(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _staged(
        tmp_path,
        items=items,
        current_md="# Workouts\n- old",
        proposed_md="# Workouts\n- new\n🔗 [t-a](https://ig/a)",
        proposal={"folded": ["a"], "dropped": []},
    )
    calls = {}
    _patch_apply(monkeypatch, [_h1("Workouts"), _bullet("old", "b1")], calls=calls)
    counts = dist.apply(env, target="Page A", version="v")
    assert counts["folded"] == 1
    assert calls["sections"][0]["delete_block_ids"] == ["b1"]
    assert calls["sections"][0]["after_block_id"] == "h1"
    assert calls["relations"] == ["p-a"]
    assert calls["distributed"] == [("p-a", None)]


def test_apply_fills_the_empty_anchor_on_a_first_time_build(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _staged(
        tmp_path,
        items=items,
        current_md="# ",
        proposed_md="# Workouts\n- new\n🔗 [t-a](https://ig/a)",
        proposal={"folded": ["a"], "dropped": []},
    )
    calls = {}
    _patch_apply(monkeypatch, [_h1("", "anchor")], calls=calls)
    dist.apply(env, target="Page A", version="v")
    assert calls["headings"] == [("anchor", "Workouts")]
    assert calls["sections"][0]["after_block_id"] == "anchor"


def test_apply_marks_a_dropped_item_misrouted_without_a_relation(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _staged(
        tmp_path,
        items=items,
        current_md="# Workouts\n- old",
        proposed_md="# Workouts\n- new",
        proposal={"folded": [], "dropped": [{"source_id": "a", "reason": "meal prep"}]},
    )
    calls = {}
    _patch_apply(monkeypatch, [_h1("Workouts"), _bullet("old", "b1")], calls=calls)
    counts = dist.apply(env, target="Page A", version="v")
    assert counts["misrouted"] == 1
    assert calls["misrouted"] == [("p-a", "meal prep")]
    assert "relations" not in calls


def test_apply_passes_a_missing_drop_reason_through_as_none(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _staged(
        tmp_path,
        items=items,
        current_md="# Workouts\n- old",
        proposed_md="# Workouts\n- new",
        proposal={"folded": [], "dropped": [{"source_id": "a", "reason": None}]},
    )
    calls = {}
    _patch_apply(monkeypatch, [_h1("Workouts"), _bullet("old", "b1")], calls=calls)
    counts = dist.apply(env, target="Page A", version="v")
    assert counts["misrouted"] == 1
    assert calls["misrouted"] == [("p-a", None)]


def test_apply_marks_an_unmentioned_item_distributed_with_a_skip_note(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"]), _item("b", ["fitness-workouts"])]
    env, _ = _staged(
        tmp_path,
        items=items,
        current_md="# Workouts\n- old",
        proposed_md="# Workouts\n- new\n🔗 [t-a](https://ig/a)",
        proposal={"folded": ["a"], "dropped": []},
    )
    calls = {}
    _patch_apply(monkeypatch, [_h1("Workouts"), _bullet("old", "b1")], calls=calls)
    counts = dist.apply(env, target="Page A", version="v")
    assert counts["skipped"] == 1
    assert ("p-b", "no new information — already covered on the page") in calls["distributed"]
    assert calls["relations"] == ["p-a"]


def test_apply_writes_no_item_state_when_a_section_write_fails(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _staged(
        tmp_path,
        items=items,
        current_md="# Workouts\n- old",
        proposed_md="# Workouts\n- new\n🔗 [t-a](https://ig/a)",
        proposal={"folded": ["a"], "dropped": []},
    )
    calls = {}
    _patch_apply(monkeypatch, [_h1("Workouts"), _bullet("old", "b1")], calls=calls)
    monkeypatch.setattr(
        dist,
        "replace_section_blocks",
        lambda env, pid, **kw: (_ for _ in ()).throw(RuntimeError("notion down")),
    )
    with pytest.raises(RuntimeError, match="notion down"):
        dist.apply(env, target="Page A", version="v")
    assert "relations" not in calls and "distributed" not in calls


def test_apply_aborts_when_the_page_changed_since_prepare(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _staged(
        tmp_path,
        items=items,
        current_md="# Workouts\n- old",
        proposed_md="# Workouts\n- new\n🔗 [t-a](https://ig/a)",
        proposal={"folded": ["a"], "dropped": []},
    )
    calls = {}
    _patch_apply(monkeypatch, [_h1("Workouts"), _bullet("someone edited this", "b1")], calls=calls)
    with pytest.raises(RuntimeError, match="changed since prepare"):
        dist.apply(env, target="Page A", version="v")
    assert calls == {}


def test_apply_ignores_an_invented_source_id(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, _ = _staged(
        tmp_path,
        items=items,
        current_md="# Workouts\n- old",
        proposed_md="# Workouts\n- new\n🔗 [t-a](https://ig/a)",
        proposal={"folded": ["a", "ghost"], "dropped": []},
    )
    calls = {}
    _patch_apply(monkeypatch, [_h1("Workouts"), _bullet("old", "b1")], calls=calls)
    counts = dist.apply(env, target="Page A", version="v")
    assert counts["unknown"] == 1 and counts["folded"] == 1


def _read_block(bid, btype, text="", *, has_children=False):
    body = {}
    if btype != "divider":
        spans = [{"plain_text": text, "annotations": {"bold": False}, "href": None}] if text else []
        body = {"rich_text": spans}
    return {"id": bid, "type": btype, "has_children": has_children, btype: body}


def _to_read_shape(block, bid):
    btype = block["type"]
    spans = []
    for span in block[btype]["rich_text"]:
        text = span["text"]
        spans.append(
            {
                "plain_text": text["content"],
                "annotations": {"bold": bool((span.get("annotations") or {}).get("bold"))},
                "href": (text.get("link") or {}).get("url"),
            }
        )
    return {"id": bid, "type": btype, "has_children": False, btype: {"rich_text": spans}}


class _FakePage:
    """A live page's ordered top-level blocks, mutated the way the Notion adapter would."""

    WRITES = {
        "replace_section_blocks",
        "set_heading_text",
        "set_knowledge_relation",
        "write_distributed",
        "write_misrouted",
    }

    def __init__(self, blocks, *, fail_on_replace=False):
        self.blocks = copy.deepcopy(blocks)
        self.log = []
        self.fail_on_replace = fail_on_replace
        self._ids = 0

    def patch(self, monkeypatch):
        for name in ("get_page_blocks", *self.WRITES):
            monkeypatch.setattr(dist, name, getattr(self, name))
        return self

    def writes(self):
        return [entry for entry in self.log if entry[0] in self.WRITES]

    def ids(self):
        return [b["id"] for b in self.blocks]

    def render(self):
        return blocks_to_markdown(dist._content_blocks(self.blocks))

    def get_page_blocks(self, env, pid):
        self.log.append(("get_page_blocks", pid))
        return copy.deepcopy(self.blocks)

    def replace_section_blocks(self, env, pid, *, after_block_id, delete_block_ids, blocks):
        self.log.append(("replace_section_blocks", after_block_id, list(delete_block_ids)))
        if self.fail_on_replace:
            raise RuntimeError("notion down")
        self.blocks = [b for b in self.blocks if b["id"] not in delete_block_ids]
        position = self.ids().index(after_block_id) + 1
        last = after_block_id
        for block in blocks:
            self._ids += 1
            last = f"new{self._ids}"
            self.blocks.insert(position, _to_read_shape(block, last))
            position += 1
        return last

    def set_heading_text(self, env, bid, text):
        self.log.append(("set_heading_text", bid, text))
        block = next(b for b in self.blocks if b["id"] == bid)
        block["heading_1"]["rich_text"] = (
            [{"plain_text": text, "annotations": {"bold": False}, "href": None}] if text else []
        )

    def set_knowledge_relation(self, env, pid, kp):
        self.log.append(("set_knowledge_relation", pid, kp))
        return True

    def write_distributed(self, env, pid, version, notes=None):
        self.log.append(("write_distributed", pid, notes))

    def write_misrouted(self, env, pid, reason, version):
        self.log.append(("write_misrouted", pid, reason))


def _stage_for(tmp_path, page, *, items, proposed_md, proposal):
    return _staged(
        tmp_path,
        items=items,
        current_md=page.render(),
        proposed_md=proposed_md,
        proposal=proposal,
    )


def test_apply_replaces_one_section_keeps_the_other_and_appends_last(tmp_path, monkeypatch):
    page = _FakePage(
        [
            {"id": "toc", "type": "table_of_contents", "has_children": False},
            _read_block("qa", "heading_2", "🔗 Quick Access"),
            _read_block("hx", "heading_1", "X"),
            _read_block("bx", "bulleted_list_item", "x old"),
            _read_block("hy", "heading_1", "Y"),
            _read_block("by", "bulleted_list_item", "y keep"),
        ]
    ).patch(monkeypatch)
    proposed = "# X\n- x new\n🔗 [t-a](https://ig/a)\n# Y\n- y keep\n# Z\n- z one"
    env, _ = _stage_for(
        tmp_path,
        page,
        items=[_item("a", ["fitness-workouts"])],
        proposed_md=proposed,
        proposal={"folded": ["a"], "dropped": []},
    )
    dist.apply(env, target="Page A", version="v")
    assert page.render() == proposed
    assert {"toc", "qa", "hx", "hy", "by"} <= set(page.ids())
    assert "bx" not in page.ids()


def test_apply_first_time_build_fills_the_anchor_and_keeps_template_furniture(
    tmp_path, monkeypatch
):
    page = _FakePage(
        [
            _read_block("anchor", "heading_1"),
            _read_block("dv", "divider"),
            _read_block("ep", "paragraph"),
        ]
    ).patch(monkeypatch)
    proposed = "# S1\n- one\n🔗 [t-a](https://ig/a)\n# S2\n- two"
    env, _ = _stage_for(
        tmp_path,
        page,
        items=[_item("a", ["fitness-workouts"])],
        proposed_md=proposed,
        proposal={"folded": ["a"], "dropped": []},
    )
    dist.apply(env, target="Page A", version="v")
    assert page.blocks[0]["id"] == "anchor"
    assert page.render().startswith("# S1\n- one\n🔗 [t-a](https://ig/a)\n# S2\n- two")
    assert "dv" in page.ids() and "ep" in page.ids()
    assert ("set_heading_text", "anchor", "S1") in page.log


def test_apply_appends_after_the_page_tail_despite_a_stray_empty_heading(tmp_path, monkeypatch):
    page = _FakePage(
        [
            _read_block("hx", "heading_1", "X"),
            _read_block("bx", "bulleted_list_item", "x"),
            _read_block("stray", "heading_1"),
            _read_block("hy", "heading_1", "Y"),
            _read_block("by", "bulleted_list_item", "y"),
        ]
    ).patch(monkeypatch)
    proposed = "# X\n- x\n# \n# Y\n- y\n# Z\n- z\n🔗 [t-a](https://ig/a)"
    env, _ = _stage_for(
        tmp_path,
        page,
        items=[_item("a", ["fitness-workouts"])],
        proposed_md=proposed,
        proposal={"folded": ["a"], "dropped": []},
    )
    dist.apply(env, target="Page A", version="v")
    assert page.render() == proposed
    assert not any(entry[0] == "set_heading_text" for entry in page.log)


@pytest.mark.parametrize(
    "bad_block, rendered, kind",
    [
        (_read_block("ny", "numbered_list_item", "hidden"), "", "numbered_list_item"),
        (
            _read_block("cy", "bulleted_list_item", "parent", has_children=True),
            "- parent",
            "has_children",
        ),
    ],
)
def test_apply_refuses_to_replace_a_section_holding_unshowable_blocks(
    tmp_path, monkeypatch, bad_block, rendered, kind
):
    page = _FakePage(
        [
            _read_block("hx", "heading_1", "X"),
            _read_block("bx", "bulleted_list_item", "x"),
            _read_block("hy", "heading_1", "Y"),
            bad_block,
        ]
    ).patch(monkeypatch)
    assert page.render() == ("# X\n- x\n# Y\n" + rendered if rendered else "# X\n- x\n# Y")
    env, d = _stage_for(
        tmp_path,
        page,
        items=[_item("a", ["fitness-workouts"])],
        proposed_md="# X\n- x2\n🔗 [t-a](https://ig/a)\n# Y\n- y2",
        proposal={"folded": ["a"], "dropped": []},
    )
    with pytest.raises(RuntimeError, match=rf"section 'Y' holds blocks .*{kind}") as exc:
        dist.apply(env, target="Page A", version="v")
    assert "'X'" not in str(exc.value)
    assert page.writes() == []
    assert (d / "batch.json").exists()


def test_apply_replace_keeps_dividers_and_empty_paragraphs(tmp_path, monkeypatch):
    page = _FakePage(
        [
            _read_block("hx", "heading_1", "X"),
            _read_block("bx", "bulleted_list_item", "x old"),
            _read_block("px", "paragraph", "prose old"),
            _read_block("dv", "divider"),
            _read_block("ep", "paragraph"),
        ]
    ).patch(monkeypatch)
    env, _ = _stage_for(
        tmp_path,
        page,
        items=[_item("a", ["fitness-workouts"])],
        proposed_md="# X\n- x new\n🔗 [t-a](https://ig/a)",
        proposal={"folded": ["a"], "dropped": []},
    )
    dist.apply(env, target="Page A", version="v")
    assert ("replace_section_blocks", "hx", ["bx", "px"]) in page.log
    assert "dv" in page.ids() and "ep" in page.ids()


def test_apply_links_only_folded_items_whose_link_landed(tmp_path, monkeypatch):
    page = _FakePage(
        [_read_block("hx", "heading_1", "X"), _read_block("bx", "bulleted_list_item", "old")]
    ).patch(monkeypatch)
    no_link = _item("c", ["fitness-workouts"])
    no_link["ig_link"] = None
    items = [_item("a", ["fitness-workouts"]), _item("b", ["fitness-workouts"]), no_link]
    env, _ = _stage_for(
        tmp_path,
        page,
        items=items,
        proposed_md="# X\n- new\n🔗 [t-a](https://ig/a)",
        proposal={"folded": ["a", "b", "c"], "dropped": []},
    )
    counts = dist.apply(env, target="Page A", version="v")
    assert counts == {"folded": 1, "skipped": 0, "misrouted": 0, "unknown": 0, "unverified": 2}
    item_writes = [e for e in page.writes() if e[0] != "replace_section_blocks"]
    assert item_writes == [
        ("set_knowledge_relation", "p-a", "kp"),
        ("write_distributed", "p-a", None),
    ]


def test_apply_resumes_item_writes_when_the_proposal_is_already_on_the_page(tmp_path, monkeypatch):
    proposed = "# X\n- new\n🔗 [t-a](https://ig/a)\n# Z\n- z"
    page = _FakePage(
        [
            _read_block("hx", "heading_1", "X"),
            _read_block("bx", "bulleted_list_item", "new"),
            _read_block("dv", "divider"),
            _read_block("ep", "paragraph"),
            _read_block("nx", "numbered_list_item", "invisible"),
            _read_block("hz", "heading_1", "Z"),
            _read_block("bz", "bulleted_list_item", "z"),
        ]
    ).patch(monkeypatch)
    page.blocks.insert(
        2, _to_read_shape(dist.markdown_to_blocks("🔗 [t-a](https://ig/a)")[0], "lk")
    )
    env, d = _staged(
        tmp_path,
        items=[_item("a", ["fitness-workouts"])],
        current_md="# X\n- old",
        proposed_md=proposed,
        proposal={"folded": ["a"], "dropped": []},
    )
    assert page.render() != "# X\n- old"
    counts = dist.apply(env, target="Page A", version="v")
    assert counts["folded"] == 1
    assert page.writes() == [
        ("set_knowledge_relation", "p-a", "kp"),
        ("write_distributed", "p-a", None),
    ]
    for name in ("batch.json", "current.md", "proposed.md", "proposal.json"):
        assert not (d / name).exists()


def test_apply_removes_staged_files_only_after_success(tmp_path, monkeypatch):
    blocks = [_read_block("hx", "heading_1", "X"), _read_block("bx", "bulleted_list_item", "old")]
    staged = ("batch.json", "current.md", "proposed.md", "proposal.json")
    kwargs = dict(
        items=[_item("a", ["fitness-workouts"])],
        proposed_md="# X\n- new\n🔗 [t-a](https://ig/a)",
        proposal={"folded": ["a"], "dropped": []},
    )

    failing = _FakePage(blocks, fail_on_replace=True).patch(monkeypatch)
    env, d = _stage_for(tmp_path, failing, **kwargs)
    with pytest.raises(RuntimeError, match="notion down"):
        dist.apply(env, target="Page A", version="v")
    assert all((d / name).exists() for name in staged)

    _FakePage(blocks).patch(monkeypatch)
    dist.apply(env, target="Page A", version="v")
    assert not any((d / name).exists() for name in staged)


def test_apply_refuses_when_nothing_is_staged(tmp_path, monkeypatch):
    page = _FakePage([_read_block("hx", "heading_1", "X")]).patch(monkeypatch)
    with pytest.raises(
        RuntimeError, match=re.escape("distribute.apply: nothing staged for 'Page A'")
    ):
        dist.apply(_env(tmp_path), target="Page A", version="v")
    assert page.writes() == []


def test_apply_refuses_text_before_the_first_heading(tmp_path, monkeypatch):
    page = _FakePage(
        [_read_block("hx", "heading_1", "X"), _read_block("bx", "bulleted_list_item", "old")]
    ).patch(monkeypatch)
    env, _ = _stage_for(
        tmp_path,
        page,
        items=[_item("a", ["fitness-workouts"])],
        proposed_md="an orphan intro line\n# X\n- new\n🔗 [t-a](https://ig/a)",
        proposal={"folded": ["a"], "dropped": []},
    )
    with pytest.raises(RuntimeError, match="before its first heading"):
        dist.apply(env, target="Page A", version="v")
    assert page.writes() == []


def test_apply_resumes_after_a_partial_failure_despite_indentation_and_trailing_space(
    tmp_path, monkeypatch
):
    page = _FakePage(
        [_read_block("hx", "heading_1", "X"), _read_block("bx", "bulleted_list_item", "old")]
    ).patch(monkeypatch)
    # A hand-editable indented sub-bullet and a trailing space: markdown_to_blocks flattens
    # and strips both on the real write, so the raw text never reappears on the live page.
    proposed = "# X\n- parent\n  - child\n- item \n🔗 [t-a](https://ig/a)\n🔗 [t-b](https://ig/b)"
    items = [_item("a", ["fitness-workouts"]), _item("b", ["fitness-workouts"])]
    env, d = _stage_for(
        tmp_path,
        page,
        items=items,
        proposed_md=proposed,
        proposal={"folded": ["a", "b"], "dropped": []},
    )

    real_relation = page.set_knowledge_relation

    def _fail_on_b(env, pid, kp):
        if pid == "p-b":
            raise RuntimeError("notion down")
        return real_relation(env, pid, kp)

    monkeypatch.setattr(dist, "set_knowledge_relation", _fail_on_b)
    with pytest.raises(RuntimeError, match="notion down"):
        dist.apply(env, target="Page A", version="v")
    assert (d / "batch.json").exists()
    assert any(entry[0] == "replace_section_blocks" for entry in page.log)

    monkeypatch.setattr(dist, "set_knowledge_relation", real_relation)
    before = len(page.log)
    counts = dist.apply(env, target="Page A", version="v")
    resumed = page.log[before:]
    assert not any(entry[0] in ("replace_section_blocks", "set_heading_text") for entry in resumed)
    assert counts["folded"] == 2
    relation_pids = {e[1] for e in page.log if e[0] == "set_knowledge_relation"}
    distributed_pids = {e[1] for e in page.log if e[0] == "write_distributed"}
    assert {"p-a", "p-b"} <= relation_pids
    assert {"p-a", "p-b"} <= distributed_pids
    for name in ("batch.json", "current.md", "proposed.md", "proposal.json"):
        assert not (d / name).exists()


def test_apply_resumes_after_a_partial_failure_when_an_untouched_section_was_deleted(
    tmp_path, monkeypatch
):
    page = _FakePage(
        [
            _read_block("hx", "heading_1", "X"),
            _read_block("bx", "bulleted_list_item", "old"),
            _read_block("hy", "heading_1", "Y"),
            _read_block("by", "bulleted_list_item", "y keep"),
        ]
    ).patch(monkeypatch)
    # The reviewer hand-edited proposed.md and dropped the untouched "Y" section entirely;
    # plan_sections leaves a section the proposal omits alone, so Y stays on the page.
    proposed = "# X\n- new\n🔗 [t-a](https://ig/a)"
    env, d = _stage_for(
        tmp_path,
        page,
        items=[_item("a", ["fitness-workouts"])],
        proposed_md=proposed,
        proposal={"folded": ["a"], "dropped": []},
    )

    def _always_fail(env, pid, kp):
        raise RuntimeError("notion down")

    monkeypatch.setattr(dist, "set_knowledge_relation", _always_fail)
    with pytest.raises(RuntimeError, match="notion down"):
        dist.apply(env, target="Page A", version="v")
    assert (d / "batch.json").exists()
    assert page.render() == "# X\n- new\n🔗 [t-a](https://ig/a)\n# Y\n- y keep"

    monkeypatch.setattr(dist, "set_knowledge_relation", page.set_knowledge_relation)
    before = len(page.log)
    counts = dist.apply(env, target="Page A", version="v")
    resumed = page.log[before:]
    assert not any(entry[0] in ("replace_section_blocks", "set_heading_text") for entry in resumed)
    assert counts["folded"] == 1
    for name in ("batch.json", "current.md", "proposed.md", "proposal.json"):
        assert not (d / name).exists()


def test_apply_refuses_when_prepared_but_not_filled(tmp_path, monkeypatch):
    page = _FakePage([_read_block("hx", "heading_1", "X")]).patch(monkeypatch)
    env = _env(tmp_path)
    d = dist.staging_dir(env, "Page A")
    (d / "batch.json").write_text(
        json.dumps({"target": "Page A", "page_id": "kp", "items": []}), encoding="utf-8"
    )
    (d / "current.md").write_text("# X", encoding="utf-8")
    with pytest.raises(
        RuntimeError, match=re.escape("distribute.apply: 'Page A' is prepared but not filled")
    ):
        dist.apply(env, target="Page A", version="v")
    assert page.writes() == []


def test_apply_does_not_verify_a_folded_item_whose_link_is_only_a_url_prefix(tmp_path, monkeypatch):
    page = _FakePage(
        [_read_block("hx", "heading_1", "X"), _read_block("bx", "bulleted_list_item", "old")]
    ).patch(monkeypatch)
    env, _ = _stage_for(
        tmp_path,
        page,
        items=[_item("a", ["fitness-workouts"])],
        proposed_md="# X\n- new\n🔗 [x](https://ig/ab)",
        proposal={"folded": ["a"], "dropped": []},
    )
    counts = dist.apply(env, target="Page A", version="v")
    assert counts == {"folded": 0, "skipped": 0, "misrouted": 0, "unknown": 0, "unverified": 1}
    writes = page.writes()
    assert len(writes) == 1 and writes[0][0] == "replace_section_blocks"


def test_apply_resumes_with_untouched_section_having_trailing_space(tmp_path, monkeypatch):
    # An untouched section Y with trailing whitespace on its bullet ("y keep ") is stored
    # in the fake page's read shape. The proposal replaces X only and folds one item.
    # Resume detection must normalize both live and expected through the write/read path
    # so trailing space (stripped by markdown_to_blocks) doesn't cause a false stale-page error.
    page = _FakePage(
        [
            _read_block("hx", "heading_1", "X"),
            _read_block("bx", "bulleted_list_item", "x old"),
            _read_block("hy", "heading_1", "Y"),
            _read_block("by", "bulleted_list_item", "y keep "),  # trailing space
        ]
    ).patch(monkeypatch)
    proposed = "# X\n- x new\n🔗 [t-a](https://ig/a)\n# Y\n- y keep "
    items = [_item("a", ["fitness-workouts"])]
    env, d = _stage_for(
        tmp_path,
        page,
        items=items,
        proposed_md=proposed,
        proposal={"folded": ["a"], "dropped": []},
    )

    real_relation = page.set_knowledge_relation

    def _fail_on_a(env, pid, kp):
        if pid == "p-a":
            raise RuntimeError("relation write failed")
        return real_relation(env, pid, kp)

    monkeypatch.setattr(dist, "set_knowledge_relation", _fail_on_a)
    with pytest.raises(RuntimeError, match="relation write failed"):
        dist.apply(env, target="Page A", version="v")
    assert (d / "batch.json").exists()
    assert any(entry[0] == "replace_section_blocks" for entry in page.log)

    monkeypatch.setattr(dist, "set_knowledge_relation", real_relation)
    before = len(page.log)
    counts = dist.apply(env, target="Page A", version="v")
    resumed = page.log[before:]
    assert not any(entry[0] in ("replace_section_blocks", "set_heading_text") for entry in resumed)
    assert counts["folded"] == 1
    relation_pids = {e[1] for e in page.log if e[0] == "set_knowledge_relation"}
    distributed_pids = {e[1] for e in page.log if e[0] == "write_distributed"}
    assert "p-a" in relation_pids
    assert "p-a" in distributed_pids
    for name in ("batch.json", "current.md", "proposed.md", "proposal.json"):
        assert not (d / name).exists()


def test_apply_logs_the_unverified_warning_even_when_a_linked_write_raises(
    tmp_path, monkeypatch, caplog
):
    page = _FakePage(
        [_read_block("hx", "heading_1", "X"), _read_block("bx", "bulleted_list_item", "old")]
    ).patch(monkeypatch)
    # "a" is folded and linked (its link lands on the applied body); "b" is folded but
    # unverified (no link on the page). set_knowledge_relation fails for every linked item,
    # so apply raises during the item-write phase — the unverified warning, logged right
    # after classification, must already be on record by then.
    items = [_item("a", ["fitness-workouts"]), _item("b", ["fitness-workouts"])]
    env, _ = _stage_for(
        tmp_path,
        page,
        items=items,
        proposed_md="# X\n- new\n🔗 [t-a](https://ig/a)",
        proposal={"folded": ["a", "b"], "dropped": []},
    )

    def _always_fail(env, pid, kp):
        raise RuntimeError("notion down")

    monkeypatch.setattr(dist, "set_knowledge_relation", _always_fail)
    with pytest.raises(RuntimeError, match="notion down"):
        dist.apply(env, target="Page A", version="v")
    assert any(
        "folded b but its link is not on the page" in record.message for record in caplog.records
    )


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


def test_preview_classifies_sections_and_items(tmp_path, monkeypatch):
    items = [
        _item("a", ["fitness-workouts"], title="Folded linked"),
        _item("b", ["fitness-workouts"], title="Folded no link"),
        _item("c", ["fitness-workouts"], title="Dropped with reason"),
        _item("d", ["fitness-workouts"], title="Dropped no reason"),
        _item("e", ["fitness-workouts"], title="Unmentioned"),
    ]
    current_md = "# Workouts\n- old\n# Mobility\n- keep"
    proposed_md = "# Workouts\n- new\n🔗 [Folded linked](https://ig/a)\n# New Topic\n- fresh"
    proposal = {
        "folded": ["a", "b"],
        "dropped": [
            {"source_id": "c", "reason": "off topic"},
            {"source_id": "d", "reason": None},
        ],
    }
    env, d = _staged(
        tmp_path, items=items, current_md=current_md, proposed_md=proposed_md, proposal=proposal
    )
    result = dist.preview(env, target="Page A")

    assert [(s.heading, s.action) for s in result.sections] == [
        ("Workouts", "replace"),
        ("New Topic", "append"),
    ]
    assert [(s.added, s.removed) for s in result.sections] == [(2, 1), (1, 0)]
    assert result.folded == [("a", "Folded linked")]
    assert result.unverified == [("b", "Folded no link")]
    assert result.dropped == [
        ("c", "Dropped with reason", "off topic"),
        ("d", "Dropped no reason", None),
    ]
    assert result.unmentioned == [("e", "Unmentioned")]
    assert "Workouts" in result.diff_text


def test_preview_no_section_changes_yields_empty_sections(tmp_path, monkeypatch):
    items = [_item("a", ["fitness-workouts"])]
    env, d = _staged(
        tmp_path,
        items=items,
        current_md="# Workouts\n- old",
        proposed_md="# Workouts\n- old",
        proposal={"folded": [], "dropped": []},
    )
    result = dist.preview(env, target="Page A")
    assert result.sections == []


def test_preview_raises_when_nothing_staged(tmp_path):
    with pytest.raises(
        RuntimeError, match=re.escape("distribute.apply: nothing staged for 'Page A'")
    ):
        dist.preview(_env(tmp_path), target="Page A")


def test_preview_raises_when_prepared_but_not_filled(tmp_path):
    env = _env(tmp_path)
    d = dist.staging_dir(env, "Page A")
    (d / "batch.json").write_text(
        json.dumps({"target": "Page A", "page_id": "kp", "items": []}), encoding="utf-8"
    )
    (d / "current.md").write_text("# X", encoding="utf-8")
    with pytest.raises(
        RuntimeError, match=re.escape("distribute.apply: 'Page A' is prepared but not filled")
    ):
        dist.preview(env, target="Page A")


def test_preview_makes_no_adapter_calls(tmp_path, monkeypatch):
    for name in (
        "get_knowledge_page",
        "get_page_blocks",
        "query_routed_for_target",
        "replace_section_blocks",
        "set_heading_text",
        "set_knowledge_relation",
        "write_distributed",
        "write_misrouted",
    ):
        monkeypatch.setattr(
            dist,
            name,
            lambda *a, _name=name, **k: (_ for _ in ()).throw(
                AssertionError(f"{_name} must not be called by preview")
            ),
        )
    items = [_item("a", ["fitness-workouts"])]
    env, d = _staged(
        tmp_path,
        items=items,
        current_md="# Workouts\n- old",
        proposed_md="# Workouts\n- new\n🔗 [t-a](https://ig/a)",
        proposal={"folded": ["a"], "dropped": []},
    )
    dist.preview(env, target="Page A")


class _RebuildPage(_FakePage):
    """A live page plus the archive items pointing at it, sharing ONE ordered call log so a
    test can assert that every item write precedes every page write."""

    ITEM_WRITES = {"remove_knowledge_relation", "reset_item_to_tagged"}
    PAGE_WRITES = {"replace_section_blocks", "set_heading_text"}
    WRITES = _FakePage.WRITES | ITEM_WRITES

    def __init__(self, blocks, items=(), *, fail_on_reset=None):
        super().__init__(blocks)
        self.items = {item["page_id"]: copy.deepcopy(item) for item in items}
        self.fail_on_reset = fail_on_reset

    def patch(self, monkeypatch):
        super().patch(monkeypatch)
        for name in ("get_knowledge_page", "query_items_for_rebuild"):
            monkeypatch.setattr(dist, name, getattr(self, name))
        return self

    def get_knowledge_page(self, env, name):
        self.log.append(("get_knowledge_page", name))
        return "kp"

    def query_items_for_rebuild(self, env, target, page_id):
        self.log.append(("query_items_for_rebuild", target, page_id))
        return [
            {k: item[k] for k in ("page_id", "source_id", "title", "status", "knowledge_base")}
            for item in copy.deepcopy(list(self.items.values()))
            if page_id in item["knowledge_base"]
            or (item["route_target"] == target and item["status"] in {"Distributed", "Misrouted"})
        ]

    def remove_knowledge_relation(self, env, pid, kp):
        self.log.append(("remove_knowledge_relation", pid, kp))
        item = self.items[pid]
        if kp not in item["knowledge_base"]:
            return False
        item["knowledge_base"] = [k for k in item["knowledge_base"] if k != kp]
        return True

    def reset_item_to_tagged(self, env, pid):
        self.log.append(("reset_item_to_tagged", pid))
        if pid == self.fail_on_reset:
            raise RuntimeError("notion down")
        self.items[pid].update(
            status="Tagged", route_target=None, distribute_notes=None, distribute_version=None
        )

    def page_writes(self):
        return [entry for entry in self.log if entry[0] in self.PAGE_WRITES]


def _archive_item(pid, status, *, knowledge_base=(), route_target="Page A", title=None):
    return {
        "page_id": pid,
        "source_id": f"s-{pid}",
        "title": title,
        "status": status,
        "route_target": route_target,
        "knowledge_base": list(knowledge_base),
        "distribute_notes": "note",
        "distribute_version": "v",
    }


def _distributed_page_blocks():
    return [
        {"id": "toc", "type": "table_of_contents", "has_children": False},
        _read_block("qa", "heading_2", "🔗 Quick Access"),
        _read_block("qdv", "divider"),
        _read_block("qp", "paragraph", "links"),
        _read_block("hx", "heading_1", "X"),
        _read_block("x2", "heading_2", "X topic"),
        _read_block("xb", "bulleted_list_item", "x tip"),
        _read_block("xp", "paragraph", "x prose"),
        _read_block("dv", "divider"),
        _read_block("ep", "paragraph"),
        _read_block("hy", "heading_1", "Y"),
        _read_block("y3", "heading_3", "Y detail"),
        _read_block("yb", "bulleted_list_item", "y tip"),
    ]


def _rebuild_items():
    return [
        _archive_item("folded", "Distributed", knowledge_base=["other-kp", "kp"], title="F"),
        _archive_item("skipped", "Distributed"),
        _archive_item("dropped", "Misrouted", title="D"),
        _archive_item("moved", "Routed", knowledge_base=["kp"], route_target="Page B"),
        _archive_item("pending", "Routed"),
    ]


_TEMPLATE_ABOVE = ["toc", "qa", "qdv", "qp"]


def test_rebuild_preview_reports_sections_counts_and_items_without_writing(tmp_path, monkeypatch):
    page = _RebuildPage(_distributed_page_blocks(), _rebuild_items()).patch(monkeypatch)
    result = dist.rebuild_preview(_env(tmp_path), target="Page A")
    assert result == dist.RebuildPreview(
        sections=[("X", 3), ("Y", 3)],
        blocks_to_delete=6,
        blocks_kept=2,
        items=[
            ("s-folded", "F", "Distributed"),
            ("s-skipped", None, "Distributed"),
            ("s-dropped", "D", "Misrouted"),
            ("s-moved", None, "Routed"),
        ],
        relations_only=[],
        refused=[],
    )
    assert page.writes() == []


def test_rebuild_preview_reports_refusals(tmp_path, monkeypatch):
    blocks = _distributed_page_blocks() + [
        _read_block("hz", "heading_1", "Z", has_children=True),
        _read_block("zn", "numbered_list_item", "hidden"),
        _read_block("zc", "bulleted_list_item", "parent", has_children=True),
    ]
    page = _RebuildPage(blocks).patch(monkeypatch)
    result = dist.rebuild_preview(_env(tmp_path), target="Page A")
    assert result.refused == [("Z", ["has_children", "numbered_list_item"])]
    assert page.writes() == []


@pytest.mark.parametrize(
    "bad_blocks, kind",
    [
        ([_read_block("zn", "numbered_list_item", "hidden")], "numbered_list_item"),
        ([_read_block("zc", "bulleted_list_item", "parent", has_children=True)], "has_children"),
        ([{"id": "zt", "type": "toggle", "has_children": False, "toggle": {}}], "toggle"),
    ],
)
def test_rebuild_refuses_unshowable_content_before_any_write(
    tmp_path, monkeypatch, bad_blocks, kind
):
    blocks = _distributed_page_blocks() + [_read_block("hz", "heading_1", "Z")] + bad_blocks
    page = _RebuildPage(blocks, _rebuild_items()).patch(monkeypatch)
    before_items = copy.deepcopy(page.items)
    with pytest.raises(RuntimeError, match=rf"section 'Z' holds blocks .*{kind}") as exc:
        dist.rebuild(_env(tmp_path), target="Page A")
    assert "'X'" not in str(exc.value) and "'Y'" not in str(exc.value)
    assert page.writes() == []
    assert page.items == before_items


def test_rebuild_refuses_a_heading_1_with_children(tmp_path, monkeypatch):
    blocks = _distributed_page_blocks()
    blocks[10] = _read_block("hy", "heading_1", "Y", has_children=True)
    page = _RebuildPage(blocks, _rebuild_items()).patch(monkeypatch)
    with pytest.raises(RuntimeError, match=r"section 'Y' holds blocks .*has_children"):
        dist.rebuild(_env(tmp_path), target="Page A")
    assert page.writes() == []


def test_rebuild_writes_every_item_before_touching_the_page(tmp_path, monkeypatch):
    page = _RebuildPage(_distributed_page_blocks(), _rebuild_items()).patch(monkeypatch)
    dist.rebuild(_env(tmp_path), target="Page A")
    kinds = [entry[0] for entry in page.writes()]
    last_item_write = max(i for i, k in enumerate(kinds) if k in _RebuildPage.ITEM_WRITES)
    first_page_write = min(i for i, k in enumerate(kinds) if k in _RebuildPage.PAGE_WRITES)
    assert last_item_write < first_page_write


def test_rebuild_resets_scoped_items_and_preserves_other_relations(tmp_path, monkeypatch):
    page = _RebuildPage(_distributed_page_blocks(), _rebuild_items()).patch(monkeypatch)
    counts = dist.rebuild(_env(tmp_path), target="Page A")
    assert page.items["folded"]["knowledge_base"] == ["other-kp"]
    for pid in ("folded", "skipped", "dropped", "moved"):
        item = page.items[pid]
        assert item["status"] == "Tagged" and item["route_target"] is None
        assert item["distribute_notes"] is None and item["distribute_version"] is None
        assert "kp" not in item["knowledge_base"]
    assert page.items["pending"]["status"] == "Routed"
    assert page.items["pending"]["route_target"] == "Page A"
    assert counts == {
        "items_reset": 4,
        "relations_removed": 2,
        "relations_only": 0,
        "blocks_deleted": 6,
        "heading_cleared": True,
        "page_skipped": False,
    }


def test_rebuild_restores_the_page_to_its_template(tmp_path, monkeypatch):
    page = _RebuildPage(_distributed_page_blocks(), _rebuild_items()).patch(monkeypatch)
    dist.rebuild(_env(tmp_path), target="Page A")
    assert page.ids() == _TEMPLATE_ABOVE + ["hx", "dv", "ep"]
    assert page.blocks[: len(_TEMPLATE_ABOVE)] == _distributed_page_blocks()[: len(_TEMPLATE_ABOVE)]
    assert dist._heading_text(page.blocks[4]) == ""
    assert ("set_heading_text", "hx", "") in page.log


def test_rebuild_stops_before_the_page_when_an_item_write_fails(tmp_path, monkeypatch):
    page = _RebuildPage(
        _distributed_page_blocks(), _rebuild_items(), fail_on_reset="skipped"
    ).patch(monkeypatch)
    with pytest.raises(RuntimeError, match="notion down"):
        dist.rebuild(_env(tmp_path), target="Page A")
    assert page.page_writes() == []
    assert page.ids() == [b["id"] for b in _distributed_page_blocks()]


def test_rebuild_clears_the_staging_files(tmp_path, monkeypatch):
    page = _RebuildPage(_distributed_page_blocks(), _rebuild_items()).patch(monkeypatch)
    env = _env(tmp_path)
    d = dist.staging_dir(env, "Page A")
    for name in dist._STAGED_FILES:
        (d / name).write_text("stale", encoding="utf-8")
    dist.rebuild(env, target="Page A")
    assert not any((d / name).exists() for name in dist._STAGED_FILES)


def test_rebuild_twice_makes_no_writes_the_second_time(tmp_path, monkeypatch):
    page = _RebuildPage(_distributed_page_blocks(), _rebuild_items()).patch(monkeypatch)
    env = _env(tmp_path)
    dist.rebuild(env, target="Page A")
    after_first = len(page.log)
    counts = dist.rebuild(env, target="Page A")
    assert [e for e in page.log[after_first:] if e[0] in page.WRITES] == []
    assert counts == {
        "items_reset": 0,
        "relations_removed": 0,
        "relations_only": 0,
        "blocks_deleted": 0,
        "heading_cleared": False,
        "page_skipped": False,
    }


def test_rebuild_resets_items_but_skips_a_page_with_no_heading_1(tmp_path, monkeypatch):
    blocks = [
        {"id": "toc", "type": "table_of_contents", "has_children": False},
        _read_block("qa", "heading_2", "🔗 Quick Access"),
        _read_block("qp", "paragraph", "links"),
    ]
    page = _RebuildPage(blocks, _rebuild_items()).patch(monkeypatch)
    counts = dist.rebuild(_env(tmp_path), target="Page A")
    assert page.page_writes() == []
    assert page.ids() == ["toc", "qa", "qp"]
    assert page.items["folded"]["status"] == "Tagged"
    assert counts == {
        "items_reset": 4,
        "relations_removed": 2,
        "relations_only": 0,
        "blocks_deleted": 0,
        "heading_cleared": False,
        "page_skipped": True,
    }


# ---------------------------------------------------------------------------
# rebuild — review fix round 1
# ---------------------------------------------------------------------------


def test_rebuild_refuses_a_quick_access_block_inside_a_section(tmp_path, monkeypatch):
    blocks = _distributed_page_blocks() + [
        _read_block("hz", "heading_1", "Z"),
        _read_block("zq", "heading_2", "🔗 Quick Access"),
    ]
    page = _RebuildPage(blocks, _rebuild_items()).patch(monkeypatch)
    before_items = copy.deepcopy(page.items)
    with pytest.raises(RuntimeError, match=r"section 'Z' holds the Quick Access section") as exc:
        dist.rebuild(_env(tmp_path), target="Page A")
    assert "'X'" not in str(exc.value) and "'Y'" not in str(exc.value)
    assert page.writes() == []
    assert page.items == before_items


def test_rebuild_preview_reports_a_quick_access_refusal(tmp_path, monkeypatch):
    blocks = _distributed_page_blocks() + [
        _read_block("hz", "heading_1", "Z"),
        _read_block("zq", "heading_2", "🔗 Quick Access"),
    ]
    page = _RebuildPage(blocks).patch(monkeypatch)
    result = dist.rebuild_preview(_env(tmp_path), target="Page A")
    assert result.refused == [("Z", ["quick_access"])]
    assert page.writes() == []


def test_rebuild_resets_before_removing_the_relation_so_a_failed_removal_can_resume(
    tmp_path, monkeypatch
):
    page = _RebuildPage(_distributed_page_blocks(), _rebuild_items()).patch(monkeypatch)
    real_remove = page.remove_knowledge_relation

    def _fail_on_folded(env, pid, kp):
        if pid == "folded":
            raise RuntimeError("notion down")
        return real_remove(env, pid, kp)

    monkeypatch.setattr(dist, "remove_knowledge_relation", _fail_on_folded)
    with pytest.raises(RuntimeError, match="notion down"):
        dist.rebuild(_env(tmp_path), target="Page A")
    assert page.page_writes() == []
    # reset already ran before the relation removal that then failed
    assert page.items["folded"]["status"] == "Tagged"
    assert "kp" in page.items["folded"]["knowledge_base"]

    monkeypatch.setattr(dist, "remove_knowledge_relation", real_remove)
    counts = dist.rebuild(_env(tmp_path), target="Page A")
    assert "kp" not in page.items["folded"]["knowledge_base"]
    assert counts["relations_removed"] >= 1
    assert page.page_writes() != []


def test_rebuild_only_removes_the_relation_for_an_item_distribute_does_not_own(
    tmp_path, monkeypatch
):
    stale = _archive_item("stale", "Extracted", knowledge_base=["kp"], route_target=None, title="S")
    page = _RebuildPage(_distributed_page_blocks(), [*_rebuild_items(), stale]).patch(monkeypatch)
    counts = dist.rebuild(_env(tmp_path), target="Page A")
    assert page.items["stale"]["status"] == "Extracted"
    assert page.items["stale"]["route_target"] is None
    assert "kp" not in page.items["stale"]["knowledge_base"]
    assert counts["relations_only"] == 1
    # the owned-status items are unaffected by the addition
    assert page.items["folded"]["status"] == "Tagged"


def test_rebuild_preview_marks_relation_only_items(tmp_path, monkeypatch):
    stale = _archive_item("stale", "Extracted", knowledge_base=["kp"], route_target=None, title="S")
    page = _RebuildPage(_distributed_page_blocks(), [*_rebuild_items(), stale]).patch(monkeypatch)
    result = dist.rebuild_preview(_env(tmp_path), target="Page A")
    assert ("s-stale", "S", "Extracted") in result.relations_only
    assert ("s-stale", "S", "Extracted") in result.items
    assert page.writes() == []


def test_rebuild_refuses_when_the_page_changes_during_the_item_loop(tmp_path, monkeypatch):
    page = _RebuildPage(_distributed_page_blocks(), _rebuild_items()).patch(monkeypatch)
    real_get = page.get_page_blocks
    calls = {"n": 0}

    def _get_page_blocks(env, pid):
        calls["n"] += 1
        if calls["n"] == 2:
            page.blocks.append(_read_block("zc", "bulleted_list_item", "parent", has_children=True))
        return real_get(env, pid)

    monkeypatch.setattr(dist, "get_page_blocks", _get_page_blocks)
    with pytest.raises(RuntimeError, match="changed during rebuild"):
        dist.rebuild(_env(tmp_path), target="Page A")
    assert page.page_writes() == []
    # items were already reset before the fresh check fired
    assert page.items["folded"]["status"] == "Tagged"


def test_rebuild_page_step_failure_leaves_items_reset_and_a_rerun_completes_the_page(
    tmp_path, monkeypatch
):
    page = _RebuildPage(_distributed_page_blocks(), _rebuild_items()).patch(monkeypatch)
    real_replace = page.replace_section_blocks

    def _fail_once(env, pid, **kw):
        monkeypatch.setattr(dist, "replace_section_blocks", real_replace)
        raise RuntimeError("notion down")

    monkeypatch.setattr(dist, "replace_section_blocks", _fail_once)
    with pytest.raises(RuntimeError, match="failed while clearing"):
        dist.rebuild(_env(tmp_path), target="Page A")
    for pid in ("folded", "skipped", "dropped", "moved"):
        assert page.items[pid]["status"] == "Tagged"
    assert page.page_writes() == []

    counts = dist.rebuild(_env(tmp_path), target="Page A")
    assert counts["items_reset"] == 0  # already reset on the failed attempt
    assert page.ids() == _TEMPLATE_ABOVE + ["hx", "dv", "ep"]


# ---------------------------------------------------------------------------
# rebuild — review fix round 2
# ---------------------------------------------------------------------------


def test_rebuild_does_not_refuse_a_bullet_that_merely_mentions_quick_access(tmp_path, monkeypatch):
    blocks = _distributed_page_blocks() + [
        _read_block("hz", "heading_1", "Z"),
        _read_block("zb", "bulleted_list_item", "Pin it to the Quick Access toolbar"),
    ]
    page = _RebuildPage(blocks, _rebuild_items()).patch(monkeypatch)
    dist.rebuild(_env(tmp_path), target="Page A")
    # Z was an ordinary (non-first) section, so it — bullet included — was deleted normally,
    # not refused.
    assert page.ids() == _TEMPLATE_ABOVE + ["hx", "dv", "ep"]


def test_rebuild_preview_does_not_flag_a_bullet_that_merely_mentions_quick_access(
    tmp_path, monkeypatch
):
    blocks = _distributed_page_blocks() + [
        _read_block("hz", "heading_1", "Z"),
        _read_block("zb", "bulleted_list_item", "Pin it to the Quick Access toolbar"),
    ]
    page = _RebuildPage(blocks).patch(monkeypatch)
    result = dist.rebuild_preview(_env(tmp_path), target="Page A")
    assert result.refused == []


def test_rebuild_heading_clear_failure_uses_the_rebuild_wording(tmp_path, monkeypatch):
    page = _RebuildPage(_distributed_page_blocks(), _rebuild_items()).patch(monkeypatch)

    def _fail_heading(env, bid, text):
        raise RuntimeError("notion down")

    monkeypatch.setattr(dist, "set_heading_text", _fail_heading)
    with pytest.raises(RuntimeError, match="failed while clearing") as exc:
        dist.rebuild(_env(tmp_path), target="Page A")
    assert "items were already reset" in str(exc.value)
    for pid in ("folded", "skipped", "dropped", "moved"):
        assert page.items[pid]["status"] == "Tagged"
    # the block delete already landed before the heading-clear call that then failed
    assert page.ids() == _TEMPLATE_ABOVE + ["hx", "dv", "ep"]


def test_rebuild_reraises_fresh_read_template_error_with_rebuild_wording(tmp_path, monkeypatch):
    page = _RebuildPage(_distributed_page_blocks(), _rebuild_items()).patch(monkeypatch)
    real_get = page.get_page_blocks
    calls = {"n": 0}

    def _get_page_blocks(env, pid):
        calls["n"] += 1
        if calls["n"] == 2:
            qa = next(b for b in page.blocks if b["id"] == "qa")
            qa["type"] = "heading_1"
            qa["heading_1"] = qa.pop("heading_2")
        return real_get(env, pid)

    monkeypatch.setattr(dist, "get_page_blocks", _get_page_blocks)
    with pytest.raises(RuntimeError, match="items were already reset") as exc:
        dist.rebuild(_env(tmp_path), target="Page A")
    assert "nothing on the page was deleted" in str(exc.value)
    assert "Quick Access" in str(exc.value)
    assert page.page_writes() == []
    # items were already reset before the fresh re-read that then failed
    assert page.items["folded"]["status"] == "Tagged"
