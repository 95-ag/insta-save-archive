import json

import pytest

from insta_save.config.recipes import load_recipes


def _write(tmp_path, data):
    p = tmp_path / "recipes.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_load_recipes_reads_target_families_and_axes(tmp_path):
    p = _write(
        tmp_path,
        {
            "target": "TargetDB",
            "families": {"FamilyOne": "first family"},
            "tag_axes": {"tag-a": {"Taste": ["Savory"]}},
        },
    )
    cfg = load_recipes(p)
    assert cfg.target == "TargetDB"
    assert cfg.families == {"FamilyOne": "first family"}
    assert cfg.tag_axes == {"tag-a": {"Taste": ["Savory"]}}


def test_axes_for_merges_every_matching_tag(tmp_path):
    cfg = load_recipes(
        _write(
            tmp_path,
            {
                "target": "T",
                "families": {},
                "tag_axes": {
                    "tag-a": {"Taste": ["Savory"]},
                    "tag-b": {"Taste": ["Spicy"], "Course": ["Meal"]},
                },
            },
        )
    )
    assert cfg.axes_for(["tag-a", "tag-b", "tag-z"]) == {
        "Taste": ["Savory", "Spicy"],
        "Course": ["Meal"],
    }


def test_axes_for_deduplicates_and_sorts(tmp_path):
    cfg = load_recipes(
        _write(
            tmp_path,
            {
                "target": "T",
                "families": {},
                "tag_axes": {"tag-a": {"Taste": ["Savory"]}, "tag-b": {"Taste": ["Savory"]}},
            },
        )
    )
    assert cfg.axes_for(["tag-a", "tag-b"]) == {"Taste": ["Savory"]}


def test_axes_for_returns_nothing_when_no_tag_matches(tmp_path):
    """No matching tag means no deterministic value - the model fills it or it stays empty."""
    cfg = load_recipes(
        _write(
            tmp_path,
            {
                "target": "T",
                "families": {},
                "tag_axes": {"tag-a": {"Taste": ["Savory"]}},
            },
        )
    )
    assert cfg.axes_for(["tag-z"]) == {}


def test_load_recipes_fails_loud_without_a_target(tmp_path):
    p = _write(tmp_path, {"families": {}, "tag_axes": {}})
    with pytest.raises(ValueError, match="target"):
        load_recipes(p)


def test_load_recipes_fails_loud_on_a_non_object_families_block(tmp_path):
    p = _write(tmp_path, {"target": "T", "families": ["FamilyOne"], "tag_axes": {}})
    with pytest.raises(ValueError, match="objects"):
        load_recipes(p)


def test_load_recipes_fails_loud_when_the_file_is_missing(tmp_path):
    """Unlike routes.json, an absent recipes.json is not 'disabled' - the stage cannot run
    without a target, so it must say so rather than write nowhere."""
    with pytest.raises(FileNotFoundError):
        load_recipes(tmp_path / "nope.json")


def test_the_example_config_loads():
    """Every config template must load through its own loader. config/routes.example.json
    drifted into an unloadable shape precisely because nothing asserted this."""
    cfg = load_recipes("config/recipes.example.json")
    assert cfg.target and cfg.families and cfg.tag_axes


def test_ingredient_aliases_load(tmp_path):
    """Curated by the user: lime juice is what you get from a lime, and Sonamasoori/Ponni are
    the default rice. Neither is inferable, so neither is guessed."""
    cfg = load_recipes(
        _write(
            tmp_path,
            {
                "target": "T",
                "families": {},
                "tag_axes": {},
                "ingredient_aliases": {"lime": "lime juice", "rice": "rice - sonamasoori"},
            },
        )
    )
    assert cfg.ingredient_aliases == {"lime": "lime juice", "rice": "rice - sonamasoori"}


def test_ingredient_aliases_default_to_empty(tmp_path):
    cfg = load_recipes(_write(tmp_path, {"target": "T", "families": {}, "tag_axes": {}}))
    assert cfg.ingredient_aliases == {}
