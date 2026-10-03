"""The `recipe` stage: one Notion row per saved food post.

Sibling of `distribute`, deliberately separate. distribute MERGES items into an existing page and
needs a markdown round-trip to do it; a recipe is a brand-new page built from structured fields by
`recipe_blocks`, so no converter is involved and no existing page content is ever rewritten.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from insta_save.adapters.notion import (
    create_recipe_row,
    get_blocks_deep,
    get_page_content,
    normalise_grocery_name,
    query_grocery_rows,
    query_recipe_rows,
    query_routed_for_target,
    set_recipe_relation,
    write_distributed,
    write_misrouted,
)
from insta_save.backends import claude_p
from insta_save.backends.base import parse_results_jsonl
from insta_save.recipe_blocks import TIP_SECTIONS, recipe_blocks

log = logging.getLogger(__name__)

CUISINE = ("Indian", "Korean", "Asian", "Western", "Mediterranean")
TASTE = ("Savory", "Sweet", "Spicy")
COURSE = ("Meal", "Side Dish", "Snack", "Dessert", "Drink")
NUTRITION = ("Healthy", "High-Protein", "Treat")
EFFORT = ("Quick", "Medium", "Heavy", "Weekend")
_AXES = {"Cuisine": CUISINE, "Taste": TASTE, "Course": COURSE, "Nutrition": NUTRITION}
_SHORTCODE = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)")
# Section headings the template writes whether or not the row was ever filled, so they must not
# count as content: 9 live rows hold 17 blocks of pure scaffolding and no text.
_SCAFFOLD = {"INSTRUCTIONS", "TIPS", "INGREDIENTS"}
_CONTENT_BLOCKS = ("paragraph", "bulleted_list_item", "numbered_list_item")


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
    if isinstance(steps, dict) and not all(steps.values()):
        empty = sorted(name for name, lines in steps.items() if not lines)
        return Rejection(source_id, f"named method(s) with no steps: {empty}")
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


def staging_dir(env) -> Path:
    d = Path(env.tmp_dir) / "recipe"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _shortcode(url):
    m = _SHORTCODE.search(url or "")
    return m.group(1) if m else None


def shortcodes_on_file(recipe_rows) -> set:
    """Instagram shortcodes already captured as recipe rows. A second save of the SAME post must
    not create a second row; a different post of the same dish still gets its own."""
    found = {_shortcode(row.get("video_link")) for row in recipe_rows}
    return {code for code in found if code}


def content_words(blocks) -> int:
    """Words of real body text, excluding the template's own section headings and dividers.

    Recurses: a recipe row keeps every ingredient and step inside `column -> children`, so a
    top-level-only count reports 0 for a fully written recipe. Measured on the first live run —
    that misread blocked all 42 related items."""
    total = 0
    for block in blocks:
        kind = block.get("type")
        if kind in _CONTENT_BLOCKS:
            spans = block.get(kind, {}).get("rich_text", [])
            text = "".join(s.get("plain_text", "") for s in spans).strip()
            if text and text.upper() not in _SCAFFOLD:
                total += len(text.split())
        total += content_words(block.get("children") or [])
    return total


def match_ingredients(ingredients, grocery, aliases=None, ignore=None) -> tuple:
    """Link an ingredient line to an existing Grocery row by normalised name. Match-only: a
    missing grocery row is REPORTED, never created — Buy/Expiry are physical-world state the
    pipeline cannot know. A missed link is recoverable; a wrong one rewrites a shopping list.

    Resolution order is exact name, then grocery-name-as-substring, then a curated alias. The
    alias comes LAST so it can never displace a row that genuinely bears the name: with a
    `lime -> lime juice` alias in play, a line reading "1 lime" still prefers a `Lime` row.

    A line naming only an `ignore` word (water) is dropped rather than reported: it is not a
    grocery item and never will be. The check is word-boundary and runs only AFTER matching
    fails, so "1 cup watermelon" still matches a watermelon row."""
    aliases = aliases or {}
    ignore_res = [re.compile(rf"\b{re.escape(w)}\b") for w in (ignore or []) if w]
    matched, unmatched = [], []
    for line in ingredients:
        key = normalise_grocery_name(line)
        hit = grocery.get(key) or next(
            (pid for name, pid in grocery.items() if name and name in key), None
        )
        if hit is None:
            # Longest alias first: "cooking oil" must not be decided by the shorter "oil" rule.
            for word in sorted((w for w in aliases if w), key=len, reverse=True):
                if word not in key:
                    continue
                targets = aliases[word]
                if isinstance(targets, str):
                    targets = [targets]
                # An ingredient can genuinely BE several items (pico de gallo). A target that
                # is not stocked is skipped, never created.
                found = [
                    pid
                    for pid in (
                        grocery.get(t.strip().lower()) or grocery.get(normalise_grocery_name(t))
                        for t in targets
                    )
                    if pid
                ]
                if found:
                    for pid in found:
                        if pid not in matched:
                            matched.append(pid)
                    hit = found[0]
                    break
        if hit:
            if hit not in matched:
                matched.append(hit)
            continue
        if any(rx.search(key) for rx in ignore_res):
            continue
        unmatched.append(line)
    return matched, unmatched


def properties_for(cfg, *, tags, proposal, ig_link) -> dict:
    """Two independent layers: the curated tag map states what the archive already knows, the
    model states what it read in the content. Both are evidence, so their values are unioned.
    An empty axis is omitted rather than written empty — unset means unknown."""
    tag_layer = cfg.axes_for(tags)
    model_layer = {
        "Cuisine": proposal.cuisine,
        "Taste": proposal.taste,
        "Course": proposal.course,
        "Nutrition": proposal.nutrition,
    }
    props = {}
    for axis in _AXES:
        values = list(dict.fromkeys(list(tag_layer.get(axis, [])) + list(model_layer[axis])))
        if values:
            props[axis] = {"multi_select": [{"name": v} for v in values]}
    if proposal.effort:
        props["Effort"] = {"select": {"name": proposal.effort}}
    if ig_link:
        props["Video Link"] = {"url": ig_link}
    return props


def prepare(env, *, cfg, version) -> int:
    """Resolve every routed item to an existing recipe row, or stage it for creation.

    Three outcomes. A row with content means the item is already captured -> Distributed. A row
    with NO content means the item would be marked done against an empty page, so it stays Routed
    and is reported; filling those is a separate, one-off operation."""
    rows = query_recipe_rows(env)
    by_code = {}
    for row in rows:
        code = _shortcode(row.get("video_link"))
        if code:
            by_code.setdefault(code, row["page_id"])

    staged, blocked, skipped = [], [], 0
    for item in query_routed_for_target(env, cfg.target):
        related = list(item["recipes"])
        if not related:
            by_shortcode = by_code.get(_shortcode(item["ig_link"]))
            related = [by_shortcode] if by_shortcode else []
        if not related:
            content = get_page_content(env, item["page_id"])
            staged.append({**item, "content": content})
            continue
        # One source can own several rows (two distinct dishes in one post), so a half-filled
        # pair must surface rather than count as captured.
        if any(content_words(get_blocks_deep(env, rid)) == 0 for rid in related):
            blocked.append(item["source_id"])
            continue
        for rid in related:
            set_recipe_relation(env, item["page_id"], rid)
        write_distributed(env, item["page_id"], version)
        skipped += 1

    d = staging_dir(env)
    batch = {"target": cfg.target, "items": staged, "blocked": blocked, "skipped": skipped}
    (d / "batch.json").write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
    if skipped:
        log.info("recipe.prepare: %d item(s) already captured by a recipe row", skipped)
    if blocked:
        log.warning(
            "recipe.prepare: %d item(s) point at a recipe row with an EMPTY body and stay Routed "
            "— fill those rows before re-running: %s",
            len(blocked),
            ", ".join(blocked),
        )
    log.info("recipe.prepare: staged %d item(s) for %s", len(staged), cfg.target)
    return len(staged)


_SUMMARY_LABEL = "summary (English distillation of this post, by an earlier stage):"


def _render_items(items) -> str:
    """One block per post: raw source first, then enrich's summary.

    The summary is DERIVED, so it is labelled and added alongside the raw content rather than
    replacing it. It matters for non-English posts: a Tamil chicken fry's transcript was
    unusable while its summary carried the method and quantities, and omitting it lost the
    recipe entirely."""
    out = []
    for item in items:
        content = item.get("content") or {}
        parts = [f"id: {item['source_id']}", f"title: {item.get('title') or ''}"]
        for key in ("caption", "transcript", "ocr_text"):
            if content.get(key):
                parts.append(f"{key}: {content[key]}")
        if item.get("summary"):
            parts.append(f"{_SUMMARY_LABEL} {item['summary']}")
        out.append("\n".join(parts))
    return "\n\n---\n\n".join(out)


def _render_prompt(template, *, cfg, items) -> str:
    families = "\n".join(f"    - {name}: {desc}" for name, desc in cfg.families.items())
    return template.replace("{families}", families).replace("{items}", _render_items(items))


def fill(env, *, cfg, model, template, call=None, sleep=time.sleep, chunk_size=8) -> dict:
    """Ask the backend to structure each staged item, then validate every reply totally.

    A rejected item is recorded with its reason rather than dropped: apply marks it Misrouted so
    it leaves Routed with an explanation, instead of being re-read every run."""
    call = call or claude_p.consolidate
    d = staging_dir(env)
    batch = json.loads((d / "batch.json").read_text(encoding="utf-8"))
    items = batch["items"]
    by_id = {i["source_id"]: i for i in items}
    proposals, rejections = [], []
    for start in range(0, len(items), chunk_size):
        chunk = items[start : start + chunk_size]
        prompt = _render_prompt(template, cfg=cfg, items=chunk)
        reply = call(prompt, model)
        objects, bad = parse_results_jsonl(reply)
        for raw in bad:
            log.warning("recipe.fill: unparseable reply line: %s", raw[:160])
        answered = {obj.get("source_id") for obj in objects}
        if any(i["source_id"] not in answered for i in chunk):
            # A chunk that silently omits items is undiagnosable without the reply text — the
            # same gap enrich closed with raw_reply.txt.
            path = d / f"raw_reply_{start // chunk_size + 1}.txt"
            path.write_text(reply, encoding="utf-8")
            log.warning("recipe.fill: chunk left items unanswered; raw reply kept at %s", path)
        for obj in objects:
            sid = obj.get("source_id")
            if sid not in by_id:
                log.warning("recipe.fill: reply names unknown id %r — ignored", sid)
                continue
            parsed = parse_recipe(obj, families=cfg.families, source_id=sid)
            if isinstance(parsed, Rejection):
                rejections.append({"source_id": sid, "reason": parsed.reason})
            else:
                proposals.append(
                    {
                        "source_id": sid,
                        "family": parsed.family,
                        "variant": parsed.variant,
                        "ingredients": parsed.ingredients,
                        "steps": parsed.steps,
                        "tips": parsed.tips,
                        "cuisine": parsed.cuisine,
                        "taste": parsed.taste,
                        "course": parsed.course,
                        "nutrition": parsed.nutrition,
                        "effort": parsed.effort,
                    }
                )
        sleep(1)
    (d / "proposals.json").write_text(
        json.dumps(proposals, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (d / "rejections.json").write_text(
        json.dumps(rejections, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    accounted = {p["source_id"] for p in proposals} | {r["source_id"] for r in rejections}
    missing = [i["source_id"] for i in items if i["source_id"] not in accounted]
    if missing:
        log.warning(
            "recipe.fill: %d staged item(s) got no reply and stay Routed: %s",
            len(missing),
            ", ".join(missing),
        )
    return {"proposed": len(proposals), "rejected": len(rejections), "unanswered": len(missing)}


def apply(env, *, cfg, version) -> dict:
    """Create a row per proposal, then relate, then mark the item Distributed — in that order.

    The relation proves the content landed, so it can never precede the write. A failed create
    leaves the item Routed with no relation, to be picked up next run."""
    d = staging_dir(env)
    proposals = json.loads((d / "proposals.json").read_text(encoding="utf-8"))
    rejections = json.loads((d / "rejections.json").read_text(encoding="utf-8"))
    items = {i["source_id"]: i for i in query_routed_for_target(env, cfg.target)}
    grocery = query_grocery_rows(env)
    counts = {"created": 0, "misrouted": 0, "failed": 0}
    review = []

    for rej in rejections:
        item = items.get(rej["source_id"])
        if item is None:
            continue
        write_misrouted(env, item["page_id"], rej["reason"], version)
        counts["misrouted"] += 1

    # Grouped, because one source can yield several rows: two distinct dishes in one post get a
    # row each. The item is marked done once, and only when EVERY one of its rows was written —
    # a partial success must not look complete.
    by_source = {}
    for raw in proposals:
        by_source.setdefault(raw["source_id"], []).append(raw)

    for source_id, group in by_source.items():
        item = items.get(source_id)
        if item is None:
            log.warning("recipe.apply: %s is no longer Routed — skipped", source_id)
            continue
        written, failed = [], 0
        for raw in group:
            proposal = RecipeProposal(**raw)
            matched, unmatched = match_ingredients(
                proposal.ingredients,
                grocery,
                aliases=cfg.ingredient_aliases,
                ignore=cfg.ingredient_ignore,
            )
            if unmatched:
                review.append({"source_id": source_id, "unmatched": unmatched})
            props = properties_for(
                cfg, tags=item["tags"], proposal=proposal, ig_link=item["ig_link"]
            )
            if matched:
                props["Ingredients"] = {"relation": [{"id": pid} for pid in matched]}
            body = recipe_blocks(
                ingredients=proposal.ingredients, steps=proposal.steps, tips=proposal.tips
            )
            try:
                row_id = create_recipe_row(
                    env, title=proposal.title, properties=props, children=body
                )
            except RuntimeError as e:
                log.error("recipe.apply: %s stays Routed — %s", source_id, e)
                failed += 1
                continue
            set_recipe_relation(env, item["page_id"], row_id)
            written.append(row_id)
        counts["created"] += len(written)
        counts["failed"] += failed
        if written and not failed:
            write_distributed(env, item["page_id"], version)
        elif written:
            log.warning(
                "recipe.apply: %s wrote %d of %d row(s) and stays Routed — re-run to finish it",
                source_id,
                len(written),
                len(group),
            )

    (d / "unmatched_ingredients.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if review:
        log.info(
            "recipe.apply: %d row(s) have ingredients with no Grocery match — see %s",
            len(review),
            d / "unmatched_ingredients.json",
        )
    return counts
