# tests/backends/test_cowork.py
from insta_save.backends import cowork
from insta_save.backends.base import FillResult


def test_cowork_fill_is_external():
    assert cowork.fill(None, None, None) == FillResult(external=True)


def test_cowork_batch_budgets_forwards_run_cfg():
    class RC:
        char_budget = 80000; max_items = 15; image_token_budget = 120000
    b = cowork.batch_budgets(RC())
    assert b.char_budget == 80000 and b.max_items == 15 and b.image_token_budget == 120000


def test_status_counts_remaining_per_group(monkeypatch):
    # status(env, collections_cfg, group) returns the count of items still enrichable
    # for the group — drives the cowork loop's stop condition (0 == drained).
    fake = [{"page_id": "p1"}, {"page_id": "p2"}, {"page_id": "p3"}]
    monkeypatch.setattr(cowork, "_enrichable_stubs",
                        lambda env, collections_cfg, group: list(fake))
    assert cowork.status(env=None, collections_cfg=None, group="Hustling") == 3
