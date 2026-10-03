"""Recipe-mode vocabulary: the destination DB's name, the controlled family list, and the
deterministic tag -> property-axis map. Private, so it lives in gitignored config rather than
in code (no tag or page name is committed). An absent file is not "disabled" as with routes:
the stage cannot run without a target, so loading raises."""

import json
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_RECIPES = Path("config") / "recipes.json"


@dataclass(frozen=True)
class RecipeConfig:
    # route_target value AND the recipe DB's title - routing and resolution key on one string.
    target: str
    # family -> one-line definition. A family is OPTIONAL: a dish with no siblings takes a bare
    # title rather than a category invented to hold a single row.
    families: dict = field(default_factory=dict)
    # archive tag -> {recipe property: [values]}. The deterministic first layer; the model fills
    # only what no tag can state (Effort, Spicy, Course for a drink).
    tag_axes: dict = field(default_factory=dict)
    # ingredient word -> the grocery row it should resolve to. Curated, because neither half is
    # inferable: lime juice is what you get FROM a lime, and Sonamasoori is the default rice.
    ingredient_aliases: dict = field(default_factory=dict)
    # Ingredient words that are not groceries and never will be ("water"). Reporting them as
    # unmatched every run is noise, not a finding — 22 of 200 lines were water.
    ingredient_ignore: list = field(default_factory=list)

    def axes_for(self, tags) -> dict:
        merged = {}
        for tag in tags:
            for prop, values in self.tag_axes.get(tag, {}).items():
                merged.setdefault(prop, set()).update(values)
        return {prop: sorted(values) for prop, values in merged.items()}


def load_recipes(path=_DEFAULT_RECIPES) -> RecipeConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    target = (data.get("target") or "").strip()
    if not target:
        raise ValueError(f"{path}: 'target' is required — it names the recipe database")
    families = data.get("families", {})
    tag_axes = data.get("tag_axes", {})
    if not isinstance(families, dict) or not isinstance(tag_axes, dict):
        raise ValueError(f"{path}: 'families' and 'tag_axes' must both be objects")
    aliases = data.get("ingredient_aliases", {})
    if not isinstance(aliases, dict):
        raise ValueError(f"{path}: 'ingredient_aliases' must be an object")
    ignore = data.get("ingredient_ignore", [])
    if not isinstance(ignore, list):
        raise ValueError(f"{path}: 'ingredient_ignore' must be a list")
    return RecipeConfig(
        target=target,
        families=families,
        tag_axes=tag_axes,
        ingredient_aliases=aliases,
        ingredient_ignore=ignore,
    )
