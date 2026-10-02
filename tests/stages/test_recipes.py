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
