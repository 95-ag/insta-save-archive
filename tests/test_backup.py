import json

import insta_save.backup as backup_mod
from insta_save.backup import backup, restore_check


def test_backup_writes_json_snapshot(tmp_path, monkeypatch):
    """backup() writes notion-<ts>.json with the documented shape and all page dicts."""
    fake_pages = [
        {"page_id": "p1", "properties": {"status": "Imported"}},
        {"page_id": "p2", "properties": {"status": "Tagged"}},
        {"page_id": "p3", "properties": {"status": "Extracted"}},
    ]
    monkeypatch.setattr(backup_mod, "query_all_pages", lambda env: fake_pages)

    env = object()
    out = backup(env, out_dir=tmp_path, ts="20260616_120000")

    assert out == tmp_path / "notion-20260616_120000.json"
    assert out.exists()

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["snapshot_ts"] == "20260616_120000"
    assert data["count"] == 3
    assert data["pages"] == fake_pages


# ---------------------------------------------------------------------------
# Helpers for restore_check tests
# ---------------------------------------------------------------------------

def _make_page(page_id, status, collections=None):
    """Build a raw Notion-shape properties dict for a backup page."""
    cols = collections or []
    return {
        "page_id": page_id,
        "properties": {
            "status": {"select": {"name": status}},
            "collection": {"multi_select": [{"name": c} for c in cols]},
        },
    }


def _write_backup(tmp_path, pages, ts="20260616_120000"):
    out = tmp_path / f"notion-{ts}.json"
    out.write_text(
        json.dumps({"snapshot_ts": ts, "count": len(pages), "pages": pages},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def _fake_collections_cfg(mapping=None):
    """Return a minimal CollectionsConfig-alike with group_of()."""
    mapping = mapping or {}  # collection_name -> group

    class _Cfg:
        def group_of(self, name):
            return mapping.get(name, "uncategorized")

    return _Cfg()


# ---------------------------------------------------------------------------
# restore_check: matching backup and live → ok
# ---------------------------------------------------------------------------

def test_restore_check_ok_when_counts_and_tallies_match(tmp_path, monkeypatch):
    """backup matches live exactly → ok=True, no mismatches."""
    pages = [
        _make_page("p1", "Imported", ["Hustling"]),
        _make_page("p2", "Tagged",   ["Hustling"]),
        _make_page("p3", "Extracted",["Biz"]),
    ]
    backup_path = _write_backup(tmp_path, pages)
    cfg = _fake_collections_cfg({"Hustling": "Hustling", "Biz": "Biz"})
    monkeypatch.setattr(backup_mod, "query_all_pages", lambda env: pages)

    result = restore_check(object(), backup_path, cfg)

    assert result["ok"] is True
    assert result["count"] == 3
    assert result["mismatches"] == []


# ---------------------------------------------------------------------------
# restore_check: count mismatch (live has one more page)
# ---------------------------------------------------------------------------

def test_restore_check_count_mismatch(tmp_path, monkeypatch):
    """Live has one more page than the backup → ok=False, mismatch entry present."""
    backup_pages = [
        _make_page("p1", "Imported"),
        _make_page("p2", "Tagged"),
    ]
    live_pages = backup_pages + [_make_page("p3", "Imported")]

    backup_path = _write_backup(tmp_path, backup_pages)
    cfg = _fake_collections_cfg()
    monkeypatch.setattr(backup_mod, "query_all_pages", lambda env: live_pages)

    result = restore_check(object(), backup_path, cfg)

    assert result["ok"] is False
    assert result["count"] == 2  # backup count
    assert len(result["mismatches"]) >= 1
    # At least one mismatch must mention the count difference
    combined = " ".join(result["mismatches"])
    assert "count" in combined.lower() or "2" in combined or "3" in combined


# ---------------------------------------------------------------------------
# restore_check: field problem — page missing page_id
# ---------------------------------------------------------------------------

def test_restore_check_field_mismatch_missing_page_id(tmp_path, monkeypatch):
    """A backup page without page_id is recorded as a field mismatch, ok=False."""
    pages = [
        {"page_id": "p1", "properties": {"status": {"select": {"name": "Imported"}},
                                          "collection": {"multi_select": []}}},
        # missing page_id
        {"properties": {"status": {"select": {"name": "Tagged"}},
                        "collection": {"multi_select": []}}},
    ]
    backup_path = _write_backup(tmp_path, pages)
    cfg = _fake_collections_cfg()
    # live matches backup structurally (count=2)
    monkeypatch.setattr(backup_mod, "query_all_pages", lambda env: pages)

    result = restore_check(object(), backup_path, cfg)

    assert result["ok"] is False
    combined = " ".join(result["mismatches"])
    assert "page_id" in combined.lower() or "field" in combined.lower() or "missing" in combined.lower()


# ---------------------------------------------------------------------------
# restore_check: field problem — page with no resolvable status
# ---------------------------------------------------------------------------

def test_restore_check_field_mismatch_missing_status(tmp_path, monkeypatch):
    """A backup page where status cannot be resolved is a field mismatch, ok=False."""
    pages = [
        _make_page("p1", "Imported"),
        # status key present but no select->name
        {"page_id": "p2", "properties": {"status": {}, "collection": {"multi_select": []}}},
    ]
    backup_path = _write_backup(tmp_path, pages)
    cfg = _fake_collections_cfg()
    monkeypatch.setattr(backup_mod, "query_all_pages", lambda env: pages)

    result = restore_check(object(), backup_path, cfg)

    assert result["ok"] is False
    combined = " ".join(result["mismatches"])
    assert "status" in combined.lower() or "field" in combined.lower()
