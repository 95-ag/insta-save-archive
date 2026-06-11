import json

from insta_save.stages import discover
from insta_save.config.collections import CollectionsConfig


def test_crawl_all_writes_snapshots_and_reuses(tmp_path):
    cfg = CollectionsConfig(groups=("Hustling", "uncategorized"), collections={
        "Dev": {"group": "Hustling", "extract": True, "slug": "dev", "numeric_id": "1"},
        "Art": {"group": "uncategorized", "extract": False, "slug": "art", "numeric_id": "2"},
    })
    calls = []

    def fake_crawl(ctx, user, slug, numeric_id):
        calls.append(slug)
        return [{"shortcode": slug + "1", "url": "u"}], True

    discover.crawl_all(context=None, ig_username="me", collections_cfg=cfg,
                       tmp_dir=tmp_path, crawl_fn=fake_crawl, fresh=True,
                       names=None, max_age_min=360, now="2026-06-11T00:00:00Z")
    assert sorted(calls) == ["art", "dev"]
    calls.clear()
    discover.crawl_all(context=None, ig_username="me", collections_cfg=cfg,
                       tmp_dir=tmp_path, crawl_fn=fake_crawl, fresh=False,
                       names=None, max_age_min=360, now="2026-06-11T01:00:00Z")
    assert calls == []


def test_crawl_all_skips_collections_without_ids(tmp_path):
    cfg = CollectionsConfig(groups=("uncategorized",), collections={
        "NoIds": {"group": "uncategorized", "extract": False, "slug": None, "numeric_id": None}})
    skipped = discover.crawl_all(context=None, ig_username="me", collections_cfg=cfg,
                                 tmp_dir=tmp_path, crawl_fn=lambda *a: ([], True),
                                 fresh=True, names=None, max_age_min=360)
    assert "NoIds" in skipped


def test_refresh_collections_config_preserves_and_reports_missing(tmp_path, monkeypatch):
    p = tmp_path / "collections.json"
    p.write_text(json.dumps({
        "groups": ["uncategorized"],
        "collections": {
            "Dev": {"group": "uncategorized", "extract": True, "slug": "dev", "numeric_id": "1"},
            "Gone": {"group": "uncategorized", "extract": False, "slug": "gone", "numeric_id": "9"},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(discover, "discover_collections",
                        lambda ctx, user: ({"Dev": {"slug": "dev", "numeric_id": "1"}}, True))

    merged, new_names, missing, complete = discover.refresh_collections_config(
        None, "me", collections_path=p, persist=True)

    assert "Dev" in merged["collections"]
    assert missing == ["Gone"]
    assert new_names == []
    assert complete is True
    written = json.loads(p.read_text(encoding="utf-8"))
    assert "Dev" in written["collections"]
