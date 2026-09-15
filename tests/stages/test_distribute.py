import json
import types

import pytest

from insta_save.backends.base import TerminalBackendError
from insta_save.config.routes import Routes
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

    text = Path("prompts/distribute_v2.0.txt").read_text(encoding="utf-8")
    assert "every entry you add or change" in text


def test_prompt_rule_7_explains_a_missing_title_or_url():
    from pathlib import Path

    text = Path("prompts/distribute_v2.0.txt").read_text(encoding="utf-8")
    assert "use its source_id as the link text" in text
    assert "cite it without a link" in text


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
