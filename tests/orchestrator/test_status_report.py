"""Tests for status_report: build_status tally and retry_failed inference."""
import pytest
from insta_save.config.collections import CollectionsConfig
from insta_save.orchestrator import status_report


_GROUPS = ("Hustling", "Biz", "Lifestyle", "uncategorized")
_COLLECTIONS_CFG = CollectionsConfig(
    groups=_GROUPS,
    collections={
        "Reels": {"group": "Hustling", "extract": True, "slug": "reels", "numeric_id": "1"},
        "Fashion": {"group": "Biz", "extract": True, "slug": "fashion", "numeric_id": "2"},
        "Outfits": {"group": "Biz", "extract": True, "slug": "outfits", "numeric_id": "3"},
        "Plants": {"group": "Lifestyle", "extract": False, "slug": "plants", "numeric_id": "4"},
    },
)


def _page(page_id, status, collections, has_raw=False):
    """Build a fake raw page dict as returned by query_all_pages."""
    raw_extraction = {"rich_text": [{"text": {"content": '{"v": {}}'}}]} if has_raw else {"rich_text": []}
    return {
        "page_id": page_id,
        "properties": {
            "status": {"select": {"name": status}},
            "collection": {"multi_select": [{"name": c} for c in collections]},
            "raw_extraction": raw_extraction,
        },
    }


# --- _parse_page ---

def test_parse_page_extracts_status_and_collections():
    page = _page("p1", "Extracted", ["Reels"])
    pid, status, collections, has_content = status_report._parse_page(page)
    assert pid == "p1"
    assert status == "Extracted"
    assert collections == ["Reels"]
    assert has_content is False


def test_parse_page_has_content_when_raw_extraction_nonempty():
    page = _page("p2", "Tagged", ["Fashion"], has_raw=True)
    _, _, _, has_content = status_report._parse_page(page)
    assert has_content is True


def test_parse_page_tolerates_missing_keys():
    page = {"page_id": "p3", "properties": {}}
    pid, status, collections, has_content = status_report._parse_page(page)
    assert pid == "p3"
    assert status is None
    assert collections == []
    assert has_content is False


# --- build_status ---

def _make_pages():
    return [
        _page("p1", "Imported", ["Reels"]),
        _page("p2", "Imported", ["Reels"]),
        _page("p3", "Extracted", ["Reels"], has_raw=True),
        _page("p4", "Tagged", ["Reels"]),
        _page("p5", "Imported", ["Fashion"]),
        _page("p6", "Queued", ["Outfits"]),
        _page("p7", "Failed", ["Fashion"], has_raw=False),
        _page("p8", "Imported", ["Plants"]),
        _page("p9", "Routed", []),      # no collection -> uncategorized
    ]


def test_build_status_returns_rows_with_correct_counts(monkeypatch):
    monkeypatch.setattr(status_report, "query_all_pages", lambda env: _make_pages())

    rows = status_report.build_status("env", _COLLECTIONS_CFG)

    hustling = next(r for r in rows if r["group"] == "Hustling")
    assert hustling["Imported"] == 2
    assert hustling["Extracted"] == 1
    assert hustling["Tagged"] == 1
    assert hustling["Queued"] == 0
    assert hustling["Failed"] == 0
    assert hustling["remaining"] == 3  # Imported + Queued + Extracted


def test_build_status_biz_group(monkeypatch):
    monkeypatch.setattr(status_report, "query_all_pages", lambda env: _make_pages())

    rows = status_report.build_status("env", _COLLECTIONS_CFG)

    biz = next(r for r in rows if r["group"] == "Biz")
    # Fashion: 1 Imported + 1 Failed; Outfits: 1 Queued
    assert biz["Imported"] == 1
    assert biz["Queued"] == 1
    assert biz["Failed"] == 1
    assert biz["remaining"] == 2  # Imported(1) + Queued(1) + Extracted(0)


def test_build_status_total_row_sums_all(monkeypatch):
    monkeypatch.setattr(status_report, "query_all_pages", lambda env: _make_pages())

    rows = status_report.build_status("env", _COLLECTIONS_CFG)

    total = next(r for r in rows if r["group"] == "TOTAL")
    # All statuses: 2+1+1 (Hustling) + 1+1+1 (Biz) + 1 (Lifestyle) + 1 (uncategorized)
    assert total["Imported"] == 4  # p1,p2 (Reels), p5 (Fashion), p8 (Plants)
    assert total["Queued"] == 1    # p6 (Outfits)
    assert total["Extracted"] == 1 # p3 (Reels)
    assert total["Tagged"] == 1    # p4 (Reels)
    assert total["Routed"] == 1    # p9 (uncategorized)
    assert total["Failed"] == 1    # p7 (Fashion)


def test_build_status_rows_ordered_by_groups_config(monkeypatch):
    monkeypatch.setattr(status_report, "query_all_pages", lambda env: _make_pages())

    rows = status_report.build_status("env", _COLLECTIONS_CFG)
    non_total = [r["group"] for r in rows if r["group"] != "TOTAL"]

    # Groups from config come first in order, uncategorized after
    hustling_idx = non_total.index("Hustling")
    biz_idx = non_total.index("Biz")
    lifestyle_idx = non_total.index("Lifestyle")
    uncategorized_idx = non_total.index("uncategorized")
    assert hustling_idx < biz_idx < lifestyle_idx < uncategorized_idx


def test_build_status_total_row_is_last(monkeypatch):
    monkeypatch.setattr(status_report, "query_all_pages", lambda env: _make_pages())

    rows = status_report.build_status("env", _COLLECTIONS_CFG)
    assert rows[-1]["group"] == "TOTAL"


def test_build_status_item_counted_once_per_distinct_group(monkeypatch):
    """An item in two collections of the SAME group is counted once (not twice)."""
    # p_multi belongs to both Fashion and Outfits — both in Biz
    pages = [_page("p_multi", "Imported", ["Fashion", "Outfits"])]
    monkeypatch.setattr(status_report, "query_all_pages", lambda env: pages)

    rows = status_report.build_status("env", _COLLECTIONS_CFG)

    biz = next(r for r in rows if r["group"] == "Biz")
    assert biz["Imported"] == 1  # only once, even though two Biz collections

    total = next(r for r in rows if r["group"] == "TOTAL")
    assert total["Imported"] == 1


def test_build_status_item_in_multiple_groups_counted_in_each(monkeypatch):
    """An item spanning two different groups is counted once per group."""
    pages = [_page("p_cross", "Extracted", ["Reels", "Fashion"], has_raw=True)]
    monkeypatch.setattr(status_report, "query_all_pages", lambda env: pages)

    rows = status_report.build_status("env", _COLLECTIONS_CFG)

    hustling = next(r for r in rows if r["group"] == "Hustling")
    biz = next(r for r in rows if r["group"] == "Biz")
    assert hustling["Extracted"] == 1
    assert biz["Extracted"] == 1


def test_build_status_remaining_is_imported_plus_queued_plus_extracted(monkeypatch):
    pages = [
        _page("p1", "Imported", ["Reels"]),
        _page("p2", "Queued", ["Reels"]),
        _page("p3", "Extracted", ["Reels"], has_raw=True),
        _page("p4", "Tagged", ["Reels"]),
        _page("p5", "Routed", ["Reels"]),
        _page("p6", "Failed", ["Reels"]),
    ]
    monkeypatch.setattr(status_report, "query_all_pages", lambda env: pages)

    rows = status_report.build_status("env", _COLLECTIONS_CFG)
    hustling = next(r for r in rows if r["group"] == "Hustling")
    assert hustling["remaining"] == 3  # Imported + Queued + Extracted (not Tagged/Routed/Failed)


# --- retry_failed ---

def test_retry_failed_infers_extracted_when_has_content(monkeypatch):
    pages = [
        _page("p1", "Failed", ["Reels"], has_raw=True),   # has content -> back to Extracted
        _page("p2", "Failed", ["Fashion"], has_raw=False), # no content -> back to Queued
    ]
    monkeypatch.setattr(status_report, "query_all_pages", lambda env: pages)

    requeue_calls = []
    monkeypatch.setattr(status_report, "requeue", lambda env, page_id, target: requeue_calls.append((page_id, target)))

    result = status_report.retry_failed("env")

    assert result["requeued"] == 2
    assert result["to_extracted"] == 1
    assert result["to_queued"] == 1

    targets = {pid: tgt for pid, tgt in requeue_calls}
    assert targets["p1"] == "Extracted"
    assert targets["p2"] == "Queued"


def test_retry_failed_no_failed_pages(monkeypatch):
    pages = [_page("p1", "Tagged", ["Reels"])]
    monkeypatch.setattr(status_report, "query_all_pages", lambda env: pages)
    monkeypatch.setattr(status_report, "requeue", lambda env, pid, tgt: None)

    result = status_report.retry_failed("env")
    assert result == {"requeued": 0, "to_extracted": 0, "to_queued": 0}


def test_retry_failed_continues_past_a_bad_requeue(monkeypatch):
    """One failing requeue must not abort the whole retry loop, and its item
    must NOT be counted in the success counters."""
    # p1 (has_raw=False) -> target Queued, but requeue raises -> NOT counted
    # p2 (has_raw=False) -> target Queued, requeue succeeds  -> counted in to_queued
    pages = [
        _page("p1", "Failed", ["Reels"], has_raw=False),
        _page("p2", "Failed", ["Fashion"], has_raw=False),
    ]
    monkeypatch.setattr(status_report, "query_all_pages", lambda env: pages)
    calls = []

    def _requeue(env, pid, target):
        calls.append(pid)
        if pid == "p1":
            raise RuntimeError("boom")

    monkeypatch.setattr(status_report, "requeue", _requeue)
    result = status_report.retry_failed(env=None)   # must not raise
    assert "p1" in calls and "p2" in calls          # p2 still attempted after p1 failed
    # p1's failed requeue must NOT bump any counter — only p2 counts
    assert result["requeued"] == 1
    assert result["to_queued"] == 1
    assert result["to_extracted"] == 0


def test_retry_failed_only_processes_failed_pages(monkeypatch):
    pages = [
        _page("p1", "Failed", ["Reels"], has_raw=False),
        _page("p2", "Imported", ["Reels"]),
        _page("p3", "Extracted", ["Reels"], has_raw=True),
    ]
    monkeypatch.setattr(status_report, "query_all_pages", lambda env: pages)

    requeue_calls = []
    monkeypatch.setattr(status_report, "requeue", lambda env, page_id, target: requeue_calls.append(page_id))

    status_report.retry_failed("env")
    assert requeue_calls == ["p1"]  # only the Failed page
