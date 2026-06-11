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


def test_run_ingest_applies_retags_without_browser(monkeypatch):
    """Retag-only plan (no creates/backfills) must still WRITE — the no-browser
    fast-path must not silently become a dry-run."""
    from insta_save.config.collections import CollectionsConfig
    cfg = CollectionsConfig(groups=("uncategorized",), collections={
        "Dev": {"group": "uncategorized", "extract": True, "slug": "dev", "numeric_id": "1"}})

    from insta_save.snapshots import write_snapshot

    class _Env:
        tmp_dir = None
        cookies_file = "x"

    import tempfile
    tmpdir = tempfile.mkdtemp()
    _Env.tmp_dir = tmpdir
    write_snapshot(tmpdir, name="Dev", slug="dev", numeric_id="1",
                   posts=[{"shortcode": "a", "url": "ua"}], complete=True)

    # Notion already has page "a" with NO collections → reconcile yields a retag (add Dev)
    monkeypatch.setattr(ingest.notion, "bulk_load_state",
                        lambda env: {"a": {"page_id": "pa", "collections": set(),
                                           "needs_metadata": False}})
    retagged = []
    monkeypatch.setattr(ingest.notion, "set_collections",
                        lambda env, pid, cols: retagged.append((pid, set(cols))))
    # create_page must NOT be called (no creates); fail loudly if it is
    monkeypatch.setattr(ingest.notion, "create_page",
                        lambda env, m: (_ for _ in ()).throw(AssertionError("no creates expected")))

    result = ingest.run_ingest(_Env(), collections_cfg=cfg, dry_run=False)
    assert retagged == [("pa", {"Dev"})], "retag-only run must write the new collection set"
    assert result["retagged"] == 1
