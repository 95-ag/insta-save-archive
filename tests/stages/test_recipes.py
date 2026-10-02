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
    monkeypatch.setattr(recipes, "get_page_blocks", lambda env, pid: row_blocks.get(pid, []))
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
