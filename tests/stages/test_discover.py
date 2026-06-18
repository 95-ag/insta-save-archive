import json

from insta_save.stages import discover
from insta_save.stages.discover import run_inline_select, EDIT_REST
from insta_save.config.collections import CollectionsConfig
from insta_save.helpers import tui


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _seed(p):
    p.write_text(json.dumps({"groups": ["uncategorized", "Biz"], "collections": {
        "A": {"group": "uncategorized", "extract": False, "slug": "a", "numeric_id": "1"},
        "B": {"group": "uncategorized", "extract": False, "slug": "b", "numeric_id": "2"}}}),
        encoding="utf-8")


# ---------------------------------------------------------------------------
# crawl_all
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# refresh_collections_config
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# run_inline_select — keyboard-select (tui-based)
# ---------------------------------------------------------------------------

def _make_select(values):
    """Return a drop-in for tui.select that accepts its (message, choices, *, default) signature
    but ignores arguments and just pops the next value from the iterator."""
    it = iter(values)
    def _select(*args, **kwargs):
        return next(it)
    return _select


def test_inline_select_sets_group_and_extract(tmp_path, monkeypatch):
    p = tmp_path / "collections.json"
    _seed(p)
    # tui.select order: mode, A-group=Biz, A-extract=True, B-group=_NEW_GROUP(→text), B-extract=False
    monkeypatch.setattr(tui, "select", _make_select(["inline", "Biz", True, discover._NEW_GROUP, False]))
    monkeypatch.setattr(tui, "text", lambda *a, **k: "NewGrp")
    monkeypatch.setattr(tui, "confirm_action", lambda *a, **k: "proceed")
    run_inline_select(p, ["A", "B"], select_mode="inline")
    data = json.loads(p.read_text())
    assert data["collections"]["A"] == {"group": "Biz", "extract": True, "slug": "a", "numeric_id": "1"}
    assert data["collections"]["B"]["group"] == "NewGrp" and data["collections"]["B"]["extract"] is False
    assert "NewGrp" in data["groups"]


def test_edit_rest_escape_calls_batch_confirm(tmp_path, monkeypatch):
    p = tmp_path / "collections.json"
    _seed(p)
    called = {}
    monkeypatch.setattr(discover, "batch_confirm", lambda path, names: called.update(names=list(names)))
    # mode, A-group=Biz, A-extract=True, B-group=EDIT_REST → batch_confirm(["B"])
    monkeypatch.setattr(tui, "select", _make_select(["inline", "Biz", True, discover.EDIT_REST]))
    monkeypatch.setattr(tui, "confirm_action", lambda *a, **k: "proceed")
    run_inline_select(p, ["A", "B"], select_mode="inline")
    assert called["names"] == ["B"]                  # remaining handed to the editor
    assert json.loads(p.read_text())["collections"]["A"]["group"] == "Biz"  # A kept


def test_edit_rest_escape_flushes_new_group_into_top_level_groups(tmp_path, monkeypatch):
    """New group created for A must appear in data["groups"] in the flushed file even when
    B escapes via EDIT_REST.  Without `data["groups"] = groups` before write_text this fails."""
    p = tmp_path / "collections.json"
    _seed(p)
    monkeypatch.setattr(discover, "batch_confirm", lambda path, names: None)
    # mode=inline, A-group=_NEW_GROUP(→text "BrandNew"), A-extract=True, B-group=EDIT_REST
    monkeypatch.setattr(tui, "select", _make_select(["inline", discover._NEW_GROUP, True, discover.EDIT_REST]))
    monkeypatch.setattr(tui, "text", lambda *a, **k: "BrandNew")
    monkeypatch.setattr(tui, "confirm_action", lambda *a, **k: "proceed")
    run_inline_select(p, ["A", "B"], select_mode="inline")
    data = json.loads(p.read_text())
    assert "BrandNew" in data["groups"], "new group must be in top-level groups list after EDIT_REST flush"
    assert data["collections"]["A"]["group"] == "BrandNew"


def test_inline_select_noop_when_empty(tmp_path, monkeypatch):
    p = tmp_path / "collections.json"
    _seed(p)
    # no tui calls expected — should return immediately
    called = []
    monkeypatch.setattr(tui, "select", lambda *a, **k: called.append("select") or "inline")
    run_inline_select(p, [], select_mode="inline")
    assert called == []
