from insta_save.stages import ingest
from insta_save.config.collections import CollectionsConfig


def test_build_reconcile_inputs_from_snapshots(tmp_path):
    from insta_save.snapshots import write_snapshot
    write_snapshot(tmp_path, name="Dev", slug="dev", numeric_id="1",
                   posts=[{"shortcode": "a", "url": "ua"}], complete=True)
    write_snapshot(tmp_path, name="Art", slug="art", numeric_id="2",
                   posts=[{"shortcode": "a", "url": "ua"}, {"shortcode": "b", "url": "ub"}],
                   complete=False)
    cfg = CollectionsConfig(groups=("uncategorized",), collections={
        "Dev": {"group": "uncategorized", "extract": True, "slug": "dev", "numeric_id": "1"},
        "Art": {"group": "uncategorized", "extract": False, "slug": "art", "numeric_id": "2"}})
    desired, urls, complete = ingest.build_reconcile_inputs(tmp_path, cfg, names=None)
    assert desired["a"] == {"Dev", "Art"}
    assert desired["b"] == {"Art"}
    assert urls["a"] == "ua"
    assert complete == {"Dev": True, "Art": False}


def test_apply_creates_and_retags(monkeypatch):
    from insta_save.reconcile import reconcile
    desired = {"a": {"Dev"}, "b": {"Dev"}}
    urls = {"a": "ua", "b": "ub"}
    state = {"b": {"page_id": "pb", "collections": set(), "needs_metadata": False}}
    plan = reconcile(desired, urls, state, {"Dev": True})
    created, retagged = [], []
    monkeypatch.setattr(ingest, "_meta_for", lambda env, url, wall, ctx: {
        "source_id": "a", "author": "natgeo", "ig_link": url, "type": "Reel"})
    monkeypatch.setattr(ingest.notion, "create_page", lambda env, m: created.append(m) or "pa")
    monkeypatch.setattr(ingest.notion, "set_collections",
                        lambda env, pid, cols: retagged.append((pid, cols)))
    ingest.apply_plan(env=None, plan=plan, context=None, cookies_txt="x",
                      refresh_targets=[], dry_run=False)
    assert created and created[0]["source_id"] == "a"
    assert retagged == [("pb", {"Dev"})]
