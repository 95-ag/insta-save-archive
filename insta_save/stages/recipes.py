"""The `recipe` stage: one Notion row per saved food post.

Sibling of `distribute`, deliberately separate. distribute MERGES items into an existing page and
needs a markdown round-trip to do it; a recipe is a brand-new page built from structured fields by
`recipe_blocks`, so no converter is involved and no existing page content is ever rewritten.
"""

import logging
from dataclasses import dataclass, field

from insta_save.recipe_blocks import TIP_SECTIONS

log = logging.getLogger(__name__)

CUISINE = ("Indian", "Korean", "Asian", "Western", "Mediterranean")
TASTE = ("Savory", "Sweet", "Spicy")
COURSE = ("Meal", "Side Dish", "Snack", "Dessert", "Drink")
NUTRITION = ("Healthy", "High-Protein", "Treat")
EFFORT = ("Quick", "Medium", "Heavy", "Weekend")
_AXES = {"Cuisine": CUISINE, "Taste": TASTE, "Course": COURSE, "Nutrition": NUTRITION}


@dataclass(frozen=True)
class RecipeProposal:
    source_id: str
    family: str | None
    variant: str
    ingredients: list
    steps: list
    tips: dict = field(default_factory=dict)
    cuisine: list = field(default_factory=list)
    taste: list = field(default_factory=list)
    course: list = field(default_factory=list)
    nutrition: list = field(default_factory=list)
    effort: str | None = None

    @property
    def title(self) -> str:
        return f"{self.family} - {self.variant}" if self.family else self.variant


@dataclass(frozen=True)
class Rejection:
    """Not a recipe, or a reply this stage refuses to trust. Becomes Misrouted + the reason."""

    source_id: str
    reason: str


def _axis(obj, key, prop):
    values = obj.get(key) or []
    if not isinstance(values, list):
        return None, f"{prop} must be a list"
    bad = [v for v in values if v not in _AXES[prop]]
    if bad:
        return None, f"{prop} has value(s) outside the vocabulary: {bad}"
    return values, None


def parse_recipe(obj, *, families, source_id):
    """Total validation: anything out of vocabulary is a Rejection, never a silent correction.
    An item marked done on an unverified claim is the failure mode this stage must not have."""
    if not obj.get("is_recipe"):
        stated = (obj.get("reason") or "").strip()
        return Rejection(source_id, stated or "reported as not a recipe, with no reason given")
    family = obj.get("family")
    if family is not None and family not in families:
        return Rejection(source_id, f"unknown family {family!r} — the model invented one")
    variant = (obj.get("variant") or "").strip()
    if not variant:
        return Rejection(source_id, "no variant name to title the row with")
    steps = obj.get("steps") or []
    ingredients = obj.get("ingredients") or []
    if not steps:
        return Rejection(source_id, "no steps — a recipe without a method is not a recipe")
    if not ingredients:
        return Rejection(source_id, "no ingredients listed in the source")
    tips = obj.get("tips") or {}
    unknown = [k for k in tips if k not in TIP_SECTIONS]
    if unknown:
        return Rejection(source_id, f"unknown tip section(s) {sorted(unknown)}")
    tips = {k: v for k, v in tips.items() if v}
    axes = {}
    for prop in _AXES:  # the reply key is the property name lowercased
        values, err = _axis(obj, prop.lower(), prop)
        if err:
            return Rejection(source_id, err)
        axes[prop.lower()] = values
    effort = obj.get("effort")
    if effort is not None and effort not in EFFORT:
        return Rejection(source_id, f"Effort {effort!r} is outside {list(EFFORT)}")
    return RecipeProposal(
        source_id=source_id,
        family=family,
        variant=variant,
        ingredients=ingredients,
        steps=steps,
        tips=tips,
        effort=effort,
        **axes,
    )
