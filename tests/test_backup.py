import json

import insta_save.backup as backup_mod
from insta_save.backup import backup


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
