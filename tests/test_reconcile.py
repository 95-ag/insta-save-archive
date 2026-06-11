from insta_save.reconcile import reconcile, DEFAULT_EXCLUDED


def test_add_is_always_safe_even_when_incomplete():
    plan = reconcile(
        desired={"a": {"Dev"}}, post_urls={"a": "u"},
        notion_state={"a": {"page_id": "p", "collections": set()}},
        complete_map={"Dev": False})
    assert len(plan.retags) == 1
    assert plan.retags[0].final == {"Dev"}


def test_remove_blocked_when_incomplete_and_unconfirmed():
    plan = reconcile(
        desired={"a": set()}, post_urls={"a": "u"},
        notion_state={"a": {"page_id": "p", "collections": {"Dev"}}},
        complete_map={"Dev": False})
    assert plan.unchanged == 1
    assert plan.skipped_unsafe and plan.skipped_unsafe[0]["collection"] == "Dev"


def test_remove_allowed_when_complete():
    plan = reconcile(
        desired={"a": set()}, post_urls={"a": "u"},
        notion_state={"a": {"page_id": "p", "collections": {"Dev"}}},
        complete_map={"Dev": True})
    assert len(plan.retags) == 1 and plan.retags[0].final == set()


def test_remove_allowed_when_confirmed():
    plan = reconcile(
        desired={"a": set()}, post_urls={"a": "u"},
        notion_state={"a": {"page_id": "p", "collections": {"Dev"}}},
        complete_map={"Dev": False}, confirmed_removed={"Dev"})
    assert len(plan.retags) == 1


def test_new_page_becomes_create():
    plan = reconcile(
        desired={"a": {"Dev"}}, post_urls={"a": "u"},
        notion_state={}, complete_map={"Dev": True})
    assert len(plan.creates) == 1
    assert plan.creates[0].page_id is None and plan.creates[0].url == "u"


def test_excluded_never_touched():
    name = next(iter(DEFAULT_EXCLUDED))
    plan = reconcile(
        desired={"a": {name}}, post_urls={"a": "u"},
        notion_state={"a": {"page_id": "p", "collections": set()}},
        complete_map={name: True})
    assert plan.unchanged == 1 and not plan.retags
