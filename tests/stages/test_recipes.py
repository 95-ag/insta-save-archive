from insta_save.stages import recipes

FAMILIES = {"FamilyOne": "d", "FamilyTwo": "d"}


def _obj(**over):
    base = {
        "is_recipe": True,
        "family": "FamilyOne",
        "variant": "Thing",
        "ingredients": ["1 cup a"],
        "steps": ["Do it."],
        "tips": {},
        "cuisine": [],
        "taste": ["Savory"],
        "course": ["Meal"],
        "nutrition": [],
        "effort": "Quick",
    }
    base.update(over)
    return base


def test_parse_recipe_builds_a_title_from_family_and_variant():
    out = recipes.parse_recipe(_obj(), families=FAMILIES, source_id="s1")
    assert out.title == "FamilyOne - Thing"


def test_a_null_family_yields_a_bare_title():
    """A family is optional - forcing one invents a category to hold a single row."""
    out = recipes.parse_recipe(_obj(family=None), families=FAMILIES, source_id="s1")
    assert out.family is None and out.title == "Thing"


def test_an_unknown_family_is_rejected_not_created():
    out = recipes.parse_recipe(_obj(family="Invented"), families=FAMILIES, source_id="s1")
    assert isinstance(out, recipes.Rejection) and "family" in out.reason


def test_is_recipe_false_is_a_rejection_carrying_the_models_reason():
    out = recipes.parse_recipe(
        {"is_recipe": False, "reason": "a storage tip, no ingredients or method"},
        families=FAMILIES,
        source_id="s1",
    )
    assert isinstance(out, recipes.Rejection)
    assert out.reason == "a storage tip, no ingredients or method"


def test_is_recipe_false_without_a_reason_still_says_something():
    """A rejection with an empty reason would write an empty note; say what happened instead."""
    out = recipes.parse_recipe({"is_recipe": False}, families=FAMILIES, source_id="s1")
    assert isinstance(out, recipes.Rejection) and out.reason


def test_missing_steps_is_a_rejection():
    out = recipes.parse_recipe(_obj(steps=[]), families=FAMILIES, source_id="s1")
    assert isinstance(out, recipes.Rejection) and "steps" in out.reason


def test_missing_ingredients_is_a_rejection():
    out = recipes.parse_recipe(_obj(ingredients=[]), families=FAMILIES, source_id="s1")
    assert isinstance(out, recipes.Rejection) and "ingredients" in out.reason


def test_a_blank_variant_is_a_rejection():
    out = recipes.parse_recipe(_obj(variant="  "), families=FAMILIES, source_id="s1")
    assert isinstance(out, recipes.Rejection) and "variant" in out.reason


def test_named_method_steps_survive_parsing():
    out = recipes.parse_recipe(
        _obj(steps={"Oven Method": ["Bake."], "Air Fryer Method": ["Fry."]}),
        families=FAMILIES,
        source_id="s1",
    )
    assert out.steps == {"Oven Method": ["Bake."], "Air Fryer Method": ["Fry."]}


def test_a_named_method_with_no_steps_is_rejected():
    out = recipes.parse_recipe(_obj(steps={"Oven Method": []}), families=FAMILIES, source_id="s1")
    assert isinstance(out, recipes.Rejection) and "steps" in out.reason


def test_two_recipes_from_one_source_become_two_proposals(tmp_path, monkeypatch):
    """A post holding two distinct dishes gets a row each: a merged row's Course/Effort/Cuisine
    describe neither, and its Ingredients relation fuses two shopping lists."""
    env = _staged(tmp_path, [{**_item("a"), "content": {"caption": "c"}}])
    reply = "\n".join([_reply("a", variant="First"), _reply("a", variant="Second")])
    out = recipes.fill(
        env,
        cfg=_cfg(),
        model="m",
        template=TEMPLATE,
        call=lambda p, m: reply,
        sleep=lambda s: None,
        chunk_size=1,
    )
    assert out["proposed"] == 2
    props = json.loads((recipes.staging_dir(env) / "proposals.json").read_text(encoding="utf-8"))
    assert sorted(p["variant"] for p in props) == ["First", "Second"]


def test_apply_creates_a_row_per_proposal_and_marks_the_item_done_once(tmp_path, monkeypatch):
    env = _staged(tmp_path, [_item("a")])
    d = recipes.staging_dir(env)
    base = {
        "source_id": "a",
        "family": None,
        "ingredients": ["1/2 cup fried gram"],
        "steps": ["Do it."],
        "tips": {},
        "cuisine": [],
        "taste": [],
        "course": [],
        "nutrition": [],
        "effort": None,
    }
    (d / "proposals.json").write_text(
        json.dumps([{**base, "variant": "First"}, {**base, "variant": "Second"}]), encoding="utf-8"
    )
    (d / "rejections.json").write_text("[]", encoding="utf-8")
    calls = _stub(monkeypatch, items=[_item("a")], recipe_rows=[])
    made = []

    def _create(env_, *, title, properties, children):
        made.append(title)
        return f"row-{len(made)}"

    monkeypatch.setattr(recipes, "create_recipe_row", _create)
    out = recipes.apply(env, cfg=_cfg(), version="v")
    assert out["created"] == 2
    assert made == ["First", "Second"]
    assert len(calls["relations"]) == 2, "both rows relate back to the one item"
    assert calls["distributed"] == ["page-a"], "the item is marked done exactly once"


def test_apply_does_not_mark_the_item_done_if_one_of_its_rows_fails(tmp_path, monkeypatch):
    """Partial success must not look complete — the item comes back round for the missing row."""
    env = _staged(tmp_path, [_item("a")])
    d = recipes.staging_dir(env)
    base = {
        "source_id": "a",
        "family": None,
        "ingredients": ["i"],
        "steps": ["s"],
        "tips": {},
        "cuisine": [],
        "taste": [],
        "course": [],
        "nutrition": [],
        "effort": None,
    }
    (d / "proposals.json").write_text(
        json.dumps([{**base, "variant": "First"}, {**base, "variant": "Second"}]), encoding="utf-8"
    )
    (d / "rejections.json").write_text("[]", encoding="utf-8")
    calls = _stub(monkeypatch, items=[_item("a")], recipe_rows=[])
    n = {"i": 0}

    def _create(env_, *, title, properties, children):
        n["i"] += 1
        if n["i"] == 2:
            raise RuntimeError("notion down")
        return "row-1"

    monkeypatch.setattr(recipes, "create_recipe_row", _create)
    out = recipes.apply(env, cfg=_cfg(), version="v")
    assert out["created"] == 1 and out["failed"] == 1
    assert calls["distributed"] == [], "a partially-written item stays Routed"


def test_prepare_blocks_when_any_related_row_is_empty(tmp_path, monkeypatch):
    """One source can own two rows; a half-filled pair must surface, not count as captured."""
    env = _env(tmp_path)
    calls = _stub(
        monkeypatch,
        items=[_item("a", recipes_rel=["r1", "r2"])],
        recipe_rows=[],
        row_blocks={"r1": [_bullet("Real content")], "r2": _shell_blocks()},
    )
    assert recipes.prepare(env, cfg=_cfg(), version="v") == 0
    batch = json.loads((recipes.staging_dir(env) / "batch.json").read_text(encoding="utf-8"))
    assert batch["blocked"] == ["a"]
    assert calls["distributed"] == []


def test_an_out_of_vocabulary_axis_value_is_rejected():
    out = recipes.parse_recipe(_obj(course=["Brunch"]), families=FAMILIES, source_id="s1")
    assert isinstance(out, recipes.Rejection) and "Course" in out.reason


def test_an_out_of_vocabulary_effort_is_rejected():
    out = recipes.parse_recipe(_obj(effort="Medium-ish"), families=FAMILIES, source_id="s1")
    assert isinstance(out, recipes.Rejection) and "Effort" in out.reason


def test_a_null_effort_is_allowed():
    """Effort is a judgment; no basis in the source is a correct answer."""
    out = recipes.parse_recipe(_obj(effort=None), families=FAMILIES, source_id="s1")
    assert out.effort is None


def test_an_unknown_tip_section_is_rejected():
    out = recipes.parse_recipe(_obj(tips={"Nonsense": ["x"]}), families=FAMILIES, source_id="s1")
    assert isinstance(out, recipes.Rejection) and "tip" in out.reason


def test_empty_tip_subsections_are_dropped():
    out = recipes.parse_recipe(
        _obj(tips={"Serving": [], "Technique": ["Low heat."]}), families=FAMILIES, source_id="s1"
    )
    assert out.tips == {"Technique": ["Low heat."]}


def test_the_prompt_states_the_four_effort_definitions():
    from pathlib import Path

    text = Path("prompts/recipe_v1.0.txt").read_text(encoding="utf-8")
    for word in ("Quick", "Medium", "Heavy", "Weekend"):
        assert word in text
    assert "prep time dominates" in text


def test_the_prompt_forbids_inventing_a_family():
    from pathlib import Path

    text = Path("prompts/recipe_v1.0.txt").read_text(encoding="utf-8")
    assert "null" in text and "never invent a family" in text


def test_the_real_prompt_carries_every_placeholder_the_renderer_substitutes():
    """A stand-in template in a test proves nothing about the shipped file. The first live run
    returned "I don't see any post data" because prompts/recipe_v1.0.txt had no {items} slot."""
    from pathlib import Path

    text = Path("prompts/recipe_v1.0.txt").read_text(encoding="utf-8")
    for slot in ("{families}", "{items}"):
        assert slot in text, f"the shipped prompt is missing {slot}"


def test_render_items_includes_the_enrich_summary_labelled_as_derived():
    """enrich distils a foreign-language post into English; dropping that threw away a real
    method (a Tamil chicken fry whose transcript was unusable but whose summary carried the
    quantities). It is passed ALONGSIDE the raw content, never instead of it."""
    out = recipes._render_items(
        [
            {
                "source_id": "a",
                "title": "T",
                "summary": "Half a kilo of chicken with ginger, fried dry.",
                "content": {"caption": "cap", "transcript": "garbled", "ocr_text": None},
            }
        ]
    )
    assert "summary (English distillation of this post, by an earlier stage):" in out
    assert "Half a kilo of chicken" in out
    assert "cap" in out and "garbled" in out, "raw source must still be present"


def test_render_items_omits_an_absent_summary():
    out = recipes._render_items(
        [{"source_id": "a", "title": "T", "summary": None, "content": {"caption": "cap"}}]
    )
    assert "summary" not in out


def test_render_prompt_substitutes_both_slots_in_the_real_prompt():
    from pathlib import Path

    template = Path("prompts/recipe_v1.0.txt").read_text(encoding="utf-8")
    out = recipes._render_prompt(
        template, cfg=_cfg(), items=[{**_item("a"), "content": {"caption": "cap text"}}]
    )
    assert "{families}" not in out and "{items}" not in out
    assert "FamilyOne" in out and "cap text" in out and "id: a" in out


def test_the_prompt_says_sweet_is_not_a_course():
    """Measured live: of 14 Sweet rows only 5 are Dessert - 8 are Snacks and 2 are Meal."""
    from pathlib import Path

    text = Path("prompts/recipe_v1.0.txt").read_text(encoding="utf-8")
    assert "not a course" in text


# --- prepare / fill / apply -------------------------------------------------
import json
import types

import pytest

from insta_save.config.recipes import RecipeConfig


def _h2(text):
    return {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": text}]}}


def _divider():
    return {"type": "divider", "divider": {}}


def _para(text):
    return {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": text}]}}


def _bullet(text):
    return {
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"plain_text": text}]},
    }


def _shell_blocks():
    return [
        _h2("INSTRUCTIONS"),
        _divider(),
        _para(""),
        _h2("TIPs"),
        _divider(),
        _para(""),
        _h2("INGREDIENTS"),
        _divider(),
        _para(""),
    ]


def _cfg(**over):
    base = {
        "target": "TargetDB",
        "families": {"FamilyOne": "d"},
        "tag_axes": {"tag-a": {"Taste": ["Savory"]}},
    }
    base.update(over)
    return RecipeConfig(**base)


def _item(sid, *, ig=None, recipes_rel=(), tags=("tag-a",)):
    return {
        "page_id": f"page-{sid}",
        "source_id": sid,
        "title": f"T {sid}",
        "summary": None,
        "tags": list(tags),
        "ig_link": ig or f"https://www.instagram.com/reel/{sid.upper()}/",
        "knowledge_base": [],
        "recipes": list(recipes_rel),
    }


def _stub(monkeypatch, *, items, recipe_rows, row_blocks=None, grocery=None, calls=None):
    calls = calls if calls is not None else {}
    calls.setdefault("distributed", [])
    calls.setdefault("misrouted", [])
    calls.setdefault("relations", [])
    calls.setdefault("created", [])
    calls.setdefault("order", [])
    row_blocks = row_blocks or {}
    monkeypatch.setattr(recipes, "query_routed_for_target", lambda env, t: items)
    monkeypatch.setattr(recipes, "query_recipe_rows", lambda env: recipe_rows)
    monkeypatch.setattr(recipes, "query_grocery_rows", lambda env: grocery or {})
    monkeypatch.setattr(recipes, "get_blocks_deep", lambda env, pid: row_blocks.get(pid, []))
    monkeypatch.setattr(
        recipes,
        "get_page_content",
        lambda env, pid: {"caption": "cap", "transcript": "tr", "ocr_text": None},
    )

    def _dist(env, pid, version, **kw):
        calls["distributed"].append(pid)
        calls["order"].append("write_distributed")

    def _mis(env, pid, reason, version):
        calls["misrouted"].append((pid, reason))

    def _rel(env, pid, row):
        calls["relations"].append((pid, row))
        calls["order"].append("set_relation")
        return True

    monkeypatch.setattr(recipes, "write_distributed", _dist)
    monkeypatch.setattr(recipes, "write_misrouted", _mis)
    monkeypatch.setattr(recipes, "set_recipe_relation", _rel)
    return calls


def _env(tmp_path):
    return types.SimpleNamespace(tmp_dir=str(tmp_path), notion_write_delay=0)


def test_shortcodes_on_file_collects_every_video_link_shortcode():
    rows = [
        {"video_link": "https://www.instagram.com/reel/AAA/?utm_source=x"},
        {"video_link": "https://youtu.be/BBB"},
        {"video_link": None},
    ]
    assert recipes.shortcodes_on_file(rows) == {"AAA"}


def test_content_words_ignores_the_template_scaffolding():
    """9 live rows hold 17 blocks of headings, dividers and empty paragraphs and no text at all;
    a word count that credited the headings would call them filled."""
    assert recipes.content_words(_shell_blocks()) == 0
    assert recipes.content_words(_shell_blocks() + [_para("350F for 50-60 minutes")]) == 4


def test_content_words_counts_text_nested_inside_columns():
    """Live rows keep every ingredient and step inside column -> children, so a top-level-only
    read reports 0 words for a fully written recipe. That misread blocked all 42 related items
    on the first live run."""
    nested = [
        {
            "type": "column_list",
            "column_list": {},
            "children": [
                {
                    "type": "column",
                    "column": {},
                    "children": [_h2("INGREDIENTS"), _divider(), _bullet("1 cup rice")],
                }
            ],
        }
    ]
    assert recipes.content_words(nested) == 3


def test_prepare_stages_a_fresh_item(tmp_path, monkeypatch):
    env = _env(tmp_path)
    _stub(monkeypatch, items=[_item("a")], recipe_rows=[])
    assert recipes.prepare(env, cfg=_cfg(), version="v") == 1
    batch = json.loads((recipes.staging_dir(env) / "batch.json").read_text(encoding="utf-8"))
    assert [i["source_id"] for i in batch["items"]] == ["a"]


def test_prepare_skips_an_item_whose_related_row_has_content(tmp_path, monkeypatch):
    env = _env(tmp_path)
    calls = _stub(
        monkeypatch,
        items=[_item("a", recipes_rel=["r1"])],
        recipe_rows=[],
        row_blocks={"r1": _shell_blocks() + [_bullet("Rinse the rice well")]},
    )
    assert recipes.prepare(env, cfg=_cfg(), version="v") == 0
    assert calls["distributed"] == ["page-a"]


def test_prepare_skips_an_item_whose_shortcode_is_already_a_row(tmp_path, monkeypatch):
    """43 of the 45 instagram-sourced rows came from saves; re-importing would duplicate. The
    relation is set on the way through, so the next run resolves it without the shortcode."""
    env = _env(tmp_path)
    rows = [
        {
            "page_id": "r1",
            "name": "FamilyOne - Thing",
            "family": "FamilyOne",
            "video_link": "https://www.instagram.com/reel/AAA/",
            "web_link": None,
        }
    ]
    calls = _stub(
        monkeypatch,
        items=[_item("a", ig="https://www.instagram.com/reel/AAA/")],
        recipe_rows=rows,
        row_blocks={"r1": [_bullet("Real content here")]},
    )
    assert recipes.prepare(env, cfg=_cfg(), version="v") == 0
    assert calls["relations"] == [("page-a", "r1")]
    assert calls["distributed"] == ["page-a"]


def test_prepare_blocks_an_item_whose_related_row_is_empty(tmp_path, monkeypatch):
    """Skipping it would mark the item done against an empty page; filling it is a separate,
    one-off operation. So it stays Routed and is reported."""
    env = _env(tmp_path)
    calls = _stub(
        monkeypatch,
        items=[_item("a", recipes_rel=["r1"])],
        recipe_rows=[],
        row_blocks={"r1": _shell_blocks()},
    )
    assert recipes.prepare(env, cfg=_cfg(), version="v") == 0
    batch = json.loads((recipes.staging_dir(env) / "batch.json").read_text(encoding="utf-8"))
    assert batch["blocked"] == ["a"]
    assert calls["distributed"] == [] and calls["misrouted"] == []


def test_match_ingredients_matches_on_the_normalised_name():
    grocery = {"fried gram": "g1", "flaxseed": "g2"}
    matched, unmatched = recipes.match_ingredients(
        ["1/2 cup fried gram", "2 tbsp melted ghee"], grocery
    )
    assert matched == ["g1"] and unmatched == ["2 tbsp melted ghee"]


def test_match_ingredients_never_invents_a_grocery_row():
    """Creating a row would pollute a live shopping list whose Buy/Expiry we cannot know."""
    matched, unmatched = recipes.match_ingredients(["nothing like it"], {})
    assert matched == [] and unmatched == ["nothing like it"]


def test_match_ingredients_deduplicates_a_repeated_match():
    grocery = {"salt": "g1"}
    matched, _ = recipes.match_ingredients(["1 tsp salt", "a pinch of salt"], grocery)
    assert matched == ["g1"]


def test_properties_for_unions_the_tag_layer_with_the_model_layer():
    """Two independent signals: the curated tag map and the model reading the content."""
    cfg = _cfg(tag_axes={"tag-a": {"Taste": ["Savory"]}})
    prop = recipes.properties_for(
        cfg,
        tags=["tag-a"],
        proposal=recipes.RecipeProposal(
            source_id="a",
            family=None,
            variant="X",
            ingredients=["i"],
            steps=["s"],
            taste=["Spicy"],
            course=["Meal"],
            effort="Quick",
        ),
        ig_link="https://ig/x",
    )
    assert prop["Taste"]["multi_select"] == [{"name": "Savory"}, {"name": "Spicy"}]
    assert prop["Course"]["multi_select"] == [{"name": "Meal"}]
    assert prop["Effort"]["select"]["name"] == "Quick"
    assert prop["Video Link"]["url"] == "https://ig/x"


def test_properties_for_omits_an_empty_axis_and_a_null_effort():
    """An empty list is a correct answer; writing an empty multi_select claims nothing is known
    rather than leaving it unset."""
    prop = recipes.properties_for(
        _cfg(tag_axes={}),
        tags=[],
        proposal=recipes.RecipeProposal(
            source_id="a", family=None, variant="X", ingredients=["i"], steps=["s"]
        ),
        ig_link=None,
    )
    assert "Taste" not in prop and "Effort" not in prop and "Video Link" not in prop


# --- fill / apply ----------------------------------------------------------
TEMPLATE = "families:\n{families}\nitems:\n{items}\n"


def _reply(sid, **over):
    obj = {
        "source_id": sid,
        "is_recipe": True,
        "family": "FamilyOne",
        "variant": "Thing",
        "ingredients": ["1/2 cup fried gram"],
        "steps": ["Do it."],
        "tips": {},
        "cuisine": [],
        "taste": ["Spicy"],
        "course": ["Meal"],
        "nutrition": [],
        "effort": "Quick",
    }
    obj.update(over)
    return json.dumps(obj)


def _staged(tmp_path, items, blocked=()):
    env = _env(tmp_path)
    d = recipes.staging_dir(env)
    batch = {"target": "TargetDB", "items": items, "blocked": list(blocked), "skipped": 0}
    (d / "batch.json").write_text(json.dumps(batch), encoding="utf-8")
    return env


def test_fill_renders_the_family_list_into_the_prompt(tmp_path, monkeypatch):
    env = _staged(tmp_path, [{**_item("a"), "content": {"caption": "c"}}])
    seen = {}

    def _call(prompt, model):
        seen["prompt"] = prompt
        return _reply("a")

    recipes.fill(env, cfg=_cfg(), model="m", template=TEMPLATE, call=_call, sleep=lambda s: None)
    assert "FamilyOne" in seen["prompt"]


def test_fill_writes_proposals_and_rejections_separately(tmp_path, monkeypatch):
    env = _staged(
        tmp_path,
        [{**_item("a"), "content": {"caption": "c"}}, {**_item("b"), "content": {"caption": "c"}}],
    )
    replies = iter(
        [
            _reply("a"),
            json.dumps({"source_id": "b", "is_recipe": False, "reason": "a storage tip"}),
        ]
    )

    def _call(prompt, model):
        return next(replies)

    out = recipes.fill(
        env,
        cfg=_cfg(),
        model="m",
        template=TEMPLATE,
        call=_call,
        sleep=lambda s: None,
        chunk_size=1,
    )
    assert out["proposed"] == 1 and out["rejected"] == 1
    d = recipes.staging_dir(env)
    props = json.loads((d / "proposals.json").read_text(encoding="utf-8"))
    rejs = json.loads((d / "rejections.json").read_text(encoding="utf-8"))
    assert [p["source_id"] for p in props] == ["a"]
    assert rejs == [{"source_id": "b", "reason": "a storage tip"}]


def test_fill_keeps_the_raw_reply_when_a_chunk_leaves_items_unanswered(tmp_path, monkeypatch):
    """A chunk that silently omits items is undiagnosable without the reply text — the same gap
    enrich closed with raw_reply.txt. Measured: 5 of 82 items got no line back."""
    env = _staged(
        tmp_path,
        [{**_item("a"), "content": {"caption": "c"}}, {**_item("b"), "content": {"caption": "c"}}],
    )
    recipes.fill(
        env,
        cfg=_cfg(),
        model="m",
        template=TEMPLATE,
        call=lambda p, m: _reply("a"),
        sleep=lambda s: None,
        chunk_size=2,
    )
    raw = (recipes.staging_dir(env) / "raw_reply_1.txt").read_text(encoding="utf-8")
    assert '"source_id": "a"' in raw


def test_fill_keeps_no_raw_reply_when_every_item_is_answered(tmp_path, monkeypatch):
    env = _staged(tmp_path, [{**_item("a"), "content": {"caption": "c"}}])
    recipes.fill(
        env,
        cfg=_cfg(),
        model="m",
        template=TEMPLATE,
        call=lambda p, m: _reply("a"),
        sleep=lambda s: None,
        chunk_size=1,
    )
    assert not list(recipes.staging_dir(env).glob("raw_reply_*.txt"))


def test_apply_misroutes_a_rejected_item_with_its_reason(tmp_path, monkeypatch):
    env = _staged(tmp_path, [_item("a")])
    d = recipes.staging_dir(env)
    (d / "proposals.json").write_text("[]", encoding="utf-8")
    (d / "rejections.json").write_text(
        json.dumps([{"source_id": "a", "reason": "a storage tip, no method"}]), encoding="utf-8"
    )
    calls = _stub(monkeypatch, items=[_item("a")], recipe_rows=[])
    monkeypatch.setattr(recipes, "create_recipe_row", lambda *a, **k: "new")
    out = recipes.apply(env, cfg=_cfg(), version="v")
    assert out["misrouted"] == 1
    assert calls["misrouted"] == [("page-a", "a storage tip, no method")]


def _stage_proposal(tmp_path, sid="a"):
    env = _staged(tmp_path, [_item(sid)])
    d = recipes.staging_dir(env)
    (d / "proposals.json").write_text(
        json.dumps(
            [
                {
                    "source_id": sid,
                    "family": "FamilyOne",
                    "variant": "Thing",
                    "ingredients": ["1/2 cup fried gram"],
                    "steps": ["Do it."],
                    "tips": {},
                    "cuisine": [],
                    "taste": ["Spicy"],
                    "course": ["Meal"],
                    "nutrition": [],
                    "effort": "Quick",
                }
            ]
        ),
        encoding="utf-8",
    )
    (d / "rejections.json").write_text("[]", encoding="utf-8")
    return env


def test_apply_creates_the_row_then_relates_then_marks_distributed(tmp_path, monkeypatch):
    """Order is the relation invariant: the link proves content landed, so it cannot precede it."""
    env = _stage_proposal(tmp_path)
    calls = _stub(monkeypatch, items=[_item("a")], recipe_rows=[])

    def _create(env_, *, title, properties, children):
        calls["created"].append(title)
        calls["order"].append("create_row")
        return "row-new"

    monkeypatch.setattr(recipes, "create_recipe_row", _create)
    out = recipes.apply(env, cfg=_cfg(), version="v")
    assert out["created"] == 1
    assert calls["order"] == ["create_row", "set_relation", "write_distributed"]
    assert calls["created"] == ["FamilyOne - Thing"]


def test_apply_leaves_the_item_routed_when_the_row_create_fails(tmp_path, monkeypatch):
    """No relation, no Distributed — the item must come back round on the next run."""
    env = _stage_proposal(tmp_path)
    calls = _stub(monkeypatch, items=[_item("a")], recipe_rows=[])

    def _boom(env_, **kw):
        raise RuntimeError("notion down")

    monkeypatch.setattr(recipes, "create_recipe_row", _boom)
    out = recipes.apply(env, cfg=_cfg(), version="v")
    assert out["failed"] == 1
    assert calls["relations"] == [] and calls["distributed"] == []


def test_apply_writes_unmatched_ingredients_to_a_review_file(tmp_path, monkeypatch):
    env = _stage_proposal(tmp_path)
    _stub(monkeypatch, items=[_item("a")], recipe_rows=[], grocery={})
    monkeypatch.setattr(recipes, "create_recipe_row", lambda *a, **k: "row-new")
    recipes.apply(env, cfg=_cfg(), version="v")
    review = json.loads(
        (recipes.staging_dir(env) / "unmatched_ingredients.json").read_text(encoding="utf-8")
    )
    assert review == [{"source_id": "a", "unmatched": ["1/2 cup fried gram"]}]


def test_apply_links_matched_ingredients_to_their_grocery_rows(tmp_path, monkeypatch):
    env = _stage_proposal(tmp_path)
    seen = {}
    _stub(monkeypatch, items=[_item("a")], recipe_rows=[], grocery={"fried gram": "g1"})

    def _create(env_, *, title, properties, children):
        seen["properties"] = properties
        return "row-new"

    monkeypatch.setattr(recipes, "create_recipe_row", _create)
    recipes.apply(env, cfg=_cfg(), version="v")
    assert seen["properties"]["Ingredients"] == {"relation": [{"id": "g1"}]}


def test_the_prompt_explains_named_methods_and_two_dishes():
    """Both rules live in the shipped file, not just in a test fixture."""
    from pathlib import Path

    text = Path("prompts/recipe_v1.0.txt").read_text(encoding="utf-8")
    assert "Instant Pot Method" in text
    assert "never park a complete method in a Variations tip" in text
    assert "ONE OBJECT PER DISH" in text


def test_the_prompt_explains_how_to_use_the_summary():
    from pathlib import Path

    text = Path("prompts/recipe_v1.0.txt").read_text(encoding="utf-8")
    assert "ENGLISH distillation of this post" in text
    assert "The summary never licenses invention" in text


def test_match_ingredients_resolves_a_curated_alias():
    """`1 Whole Lime, squeezed` has no `lime` row — the user's alias points it at lime juice."""
    grocery = {"lime juice": "g1"}
    matched, unmatched = recipes.match_ingredients(
        ["1 Whole Lime, squeezed"], grocery, aliases={"lime": "lime juice"}
    )
    assert matched == ["g1"] and unmatched == []


def test_an_alias_never_overrides_a_direct_match():
    grocery = {"lime": "g-direct", "lime juice": "g-alias"}
    matched, _ = recipes.match_ingredients(["1 lime"], grocery, aliases={"lime": "lime juice"})
    assert matched == ["g-direct"]


def test_an_alias_pointing_at_a_missing_row_is_reported_not_invented():
    matched, unmatched = recipes.match_ingredients(["1 lime"], {}, aliases={"lime": "lime juice"})
    assert matched == [] and unmatched == ["1 lime"]


def test_match_ingredients_works_without_aliases():
    matched, _ = recipes.match_ingredients(["1 tsp salt"], {"salt": "g1"})
    assert matched == ["g1"]


def test_an_ignored_ingredient_is_neither_matched_nor_reported():
    """Water is not a grocery item; listing it as "unmatched" every run is noise, not a finding."""
    matched, unmatched = recipes.match_ingredients(
        ["600 ml water", "1 tsp salt"], {"salt": "g1"}, ignore=["water"]
    )
    assert matched == ["g1"]
    assert unmatched == [], "the water line is dropped, not reported"


def test_an_ignore_word_does_not_swallow_a_real_ingredient():
    """`water` must not silence `watermelon` or `rose water`-adjacent real items."""
    matched, unmatched = recipes.match_ingredients(
        ["1 cup watermelon"], {"watermelon": "g1"}, ignore=["water"]
    )
    assert matched == ["g1"] and unmatched == []


def test_an_ignored_line_is_dropped_even_when_nothing_else_matches():
    _, unmatched = recipes.match_ingredients(["water"], {}, ignore=["water"])
    assert unmatched == []


def test_an_alias_may_resolve_to_several_grocery_rows():
    """Some ingredients ARE several items: pico de gallo is tomatoes + onions + chilli + more,
    and cumin is stocked as both powder and seeds."""
    grocery = {"cumin powder": "g1", "cumin seeds": "g2"}
    matched, unmatched = recipes.match_ingredients(
        ["1 tsp cumin"], grocery, aliases={"cumin": ["cumin powder", "cumin seeds"]}
    )
    assert matched == ["g1", "g2"] and unmatched == []


def test_a_multi_target_alias_links_only_the_rows_that_exist():
    """A target that is not stocked is skipped, not invented — and the line still counts as
    matched because at least one real row was found."""
    matched, unmatched = recipes.match_ingredients(
        ["pico de gallo"],
        {"tomatoes": "g1"},
        aliases={"pico de gallo": ["tomatoes", "onions", "green chilli"]},
    )
    assert matched == ["g1"] and unmatched == []


def test_a_multi_target_alias_with_no_stocked_row_is_reported():
    matched, unmatched = recipes.match_ingredients(
        ["pico de gallo"], {}, aliases={"pico de gallo": ["tomatoes", "onions"]}
    )
    assert matched == [] and unmatched == ["pico de gallo"]


def test_a_single_string_alias_still_works():
    """The config may give one target or a list; both are accepted."""
    matched, _ = recipes.match_ingredients(
        ["1 lime"], {"lime juice": "g1"}, aliases={"lime": "lime juice"}
    )
    assert matched == ["g1"]


def test_the_longest_matching_alias_wins():
    """`cooking oil` must resolve as cooking oil, not fall to the shorter `oil` rule."""
    grocery = {"sunflower oil": "g-sun", "olive oil": "g-olive"}
    matched, _ = recipes.match_ingredients(
        ["2 tbsp cooking oil"],
        grocery,
        aliases={"oil": "olive oil", "cooking oil": "sunflower oil"},
    )
    assert matched == ["g-sun"]
