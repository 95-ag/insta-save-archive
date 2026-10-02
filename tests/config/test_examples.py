"""Every config template must load through its own loader.

`config/routes.example.json` drifted into an unloadable shape — its `by_tag` was a dict while
`load_routes` reads an ordered list of pairs — and nothing caught it, because no template was
load-tested. Copying it to `config/routes.json` raised
`ValueError: too many values to unpack (expected 2)`.
"""

import pytest

from insta_save.config.collections import load_collections
from insta_save.config.recipes import load_recipes
from insta_save.config.routes import load_routes
from insta_save.config.run import load_run_config
from insta_save.config.tags import load_vocab

CASES = [
    ("config/collections.example.json", load_collections),
    ("config/recipes.example.json", load_recipes),
    ("config/routes.example.json", load_routes),
    ("config/run.example.json", load_run_config),
    ("config/tags.example.json", load_vocab),
]


@pytest.mark.parametrize("path,loader", CASES, ids=[c[0].split("/")[-1] for c in CASES])
def test_every_example_config_loads_through_its_own_loader(path, loader):
    assert loader(path) is not None


def test_the_routes_template_exercises_every_rule_type():
    """A template that omits a rule type lets that type drift unnoticed — `by_primary_topic`
    now WINS precedence and was absent from the template entirely."""
    cfg = load_routes("config/routes.example.json")
    assert cfg.by_tag, "by_tag must be a non-empty ordered list of [tag, target] pairs"
    assert cfg.by_primary_topic, "by_primary_topic must be shown — it outranks by_tag"
    assert isinstance(cfg.by_tag[0], tuple) and len(cfg.by_tag[0]) == 2


def test_the_routes_template_routes_a_primary_topic_ahead_of_a_tag():
    """Proves the precedence the template is meant to demonstrate."""
    cfg = load_routes("config/routes.example.json")
    topic, topic_target = next(iter(cfg.by_primary_topic.items()))
    tag, tag_target = cfg.by_tag[0]
    assert cfg.route_for([tag], [], [], primary_topic=topic) == topic_target
    assert cfg.route_for([tag], [], []) == tag_target
