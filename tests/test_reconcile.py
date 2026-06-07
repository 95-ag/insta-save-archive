"""
Reconciliation safety invariants — pure-function tests, no I/O.

Each test maps to an invariant from the ingest sync plan. These are the proof
that a transient Instagram render glitch can never strip valid collection tags.
"""

from pipeline.reconcile import reconcile


def _state(**pages):
    """Helper: build notion_state from {source_id: (page_id, [collections])}."""
    return {sid: {"page_id": pid, "collections": set(cols)}
            for sid, (pid, cols) in pages.items()}


def test_new_post_created_with_all_tags():
    """Invariant 3 + create: a post we crawled but Notion lacks → create with its tags."""
    plan = reconcile(
        desired={"NEW": {"Coding - AI", "Coding - Web Design"}},
        post_urls={"NEW": "https://www.instagram.com/p/NEW/"},
        notion_state={},
        complete_map={"Coding - AI": True, "Coding - Web Design": True},
    )
    assert len(plan.creates) == 1
    action = plan.creates[0]
    assert action.page_id is None
    assert action.url == "https://www.instagram.com/p/NEW/"
    assert action.final == {"Coding - AI", "Coding - Web Design"}
    assert not plan.retags


def test_move_between_complete_collections_adds_and_removes():
    """Invariant 1: post moved A→B, both complete → add B, remove A."""
    plan = reconcile(
        desired={"X": {"B"}},                       # now only crawled in B
        post_urls={"X": "u"},
        notion_state=_state(X=("page-x", ["A"])),   # Notion still has A
        complete_map={"A": True, "B": True},
    )
    assert len(plan.retags) == 1
    a = plan.retags[0]
    assert a.final == {"B"}
    assert a.added == {"B"}
    assert a.removed == {"A"}
    assert not plan.skipped_unsafe


def test_incomplete_crawl_does_not_remove():
    """Invariant 1 (the safety case): A's crawl incomplete → A kept, logged unsafe."""
    plan = reconcile(
        desired={"X": set()},                       # X not seen this run
        post_urls={},
        notion_state=_state(X=("page-x", ["A"])),
        complete_map={"A": False},                  # crawl of A did NOT complete
    )
    assert plan.retags == []                        # no change applied
    assert plan.unchanged == 1
    assert len(plan.skipped_unsafe) == 1
    assert plan.skipped_unsafe[0]["collection"] == "A"


def test_absent_collection_without_confirm_keeps_tag():
    """Invariant 4: a collection not crawled at all + no confirm → tag kept."""
    plan = reconcile(
        desired={},                                 # nothing crawled
        post_urls={},
        notion_state=_state(X=("page-x", ["Old Collection"])),
        complete_map={},                            # Old Collection unknown
    )
    assert plan.retags == []
    assert plan.unchanged == 1
    assert plan.skipped_unsafe[0]["collection"] == "Old Collection"


def test_confirmed_removal_strips_tag():
    """Invariant 4: with --confirm-removed, the tag is removed even if not crawled."""
    plan = reconcile(
        desired={},
        post_urls={},
        notion_state=_state(X=("page-x", ["Old Collection"])),
        complete_map={},
        confirmed_removed={"Old Collection"},
    )
    assert len(plan.retags) == 1
    assert plan.retags[0].final == set()
    assert plan.retags[0].removed == {"Old Collection"}


def test_unchanged_post_no_write():
    """Invariant 6: desired == current → no write, counted unchanged."""
    plan = reconcile(
        desired={"X": {"A", "B"}},
        post_urls={"X": "u"},
        notion_state=_state(X=("page-x", ["A", "B"])),
        complete_map={"A": True, "B": True},
    )
    assert plan.creates == []
    assert plan.retags == []
    assert plan.unchanged == 1


def test_page_never_deleted_only_emptied():
    """Invariant 2: post in none of desired, all complete → tags emptied, page kept."""
    plan = reconcile(
        desired={"X": set()},
        post_urls={},
        notion_state=_state(X=("page-x", ["A"])),
        complete_map={"A": True},                   # complete → safe to remove
    )
    assert len(plan.retags) == 1
    a = plan.retags[0]
    assert a.page_id == "page-x"                    # page identity preserved
    assert a.final == set()                         # emptied, not deleted
    assert a.removed == {"A"}


def test_all_posts_excluded_from_reconciliation():
    """Invariant 5: 'All Posts' is never added or removed."""
    plan = reconcile(
        desired={"X": {"A"}},                       # crawled in A only
        post_urls={"X": "u"},
        notion_state=_state(X=("page-x", ["A", "All Posts"])),
        complete_map={"A": True},                   # A complete; All Posts not crawled
    )
    # A unchanged, All Posts preserved → no managed change at all
    assert plan.retags == []
    assert plan.unchanged == 1


def test_add_from_incomplete_crawl_is_safe():
    """Invariant 3: presence is trustworthy even when the crawl was incomplete."""
    plan = reconcile(
        desired={"X": {"A"}},
        post_urls={"X": "u"},
        notion_state=_state(X=("page-x", [])),
        complete_map={"A": False},                  # incomplete, but we DID find X in A
    )
    assert len(plan.retags) == 1
    assert plan.retags[0].added == {"A"}
    assert plan.skipped_unsafe == []
