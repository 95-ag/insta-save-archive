# tests/backends/test_base.py
import json
import pytest
from insta_save.backends import base


def test_registry_returns_each_backend_with_flags():
    cc = base.get_backend("claude-code")
    assert cc.NAME == "claude-code" and cc.AUTOMATED is False and cc.VISION_CAPABLE is True
    loc = base.get_backend("local")
    assert loc.NAME == "local" and loc.AUTOMATED is True and loc.VISION_CAPABLE is False
    api = base.get_backend("api")
    assert api.AUTOMATED is True and api.VISION_CAPABLE is True
    cw = base.get_backend("cowork")
    assert cw.AUTOMATED is False and cw.VISION_CAPABLE is True


def test_registry_rejects_unknown():
    with pytest.raises(ValueError):
        base.get_backend("nope")


def test_parse_results_reads_array(tmp_path):
    p = tmp_path / "results.json"
    p.write_text(json.dumps([{"page_id": "p1"}]), encoding="utf-8")
    assert base.parse_results(p)[0]["page_id"] == "p1"


def test_parse_results_rejects_non_list(tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"page_id": "p1"}', encoding="utf-8")
    with pytest.raises(ValueError):
        base.parse_results(p)


def test_parse_results_array_rejects_non_array():
    with pytest.raises(ValueError):
        base.parse_results_array('{"not":"an array"}')


def test_parse_results_object_reads_clean_object():
    assert base.parse_results_object('{"a": 1}') == {"a": 1}


def test_parse_results_object_strips_fence():
    assert base.parse_results_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_results_object_extracts_from_prose_wrapped_output():
    # the real claude -p crash case: prose preamble + fenced JSON + trailing notes
    text = ("I don't have write permission to tmp/calibrate/. Here's the proposal:\n\n"
            '```json\n{"content_type": {"tool": "x"}, "groups": {"G": {}}}\n```\n\n'
            "**Notes:** some rationale prose after the object.")
    out = base.parse_results_object(text)
    assert out["content_type"]["tool"] == "x" and "G" in out["groups"]


def test_parse_results_object_rejects_non_object():
    with pytest.raises(ValueError):
        base.parse_results_object('[1, 2, 3]')


def test_parse_results_object_raises_on_no_object():
    with pytest.raises(ValueError):
        base.parse_results_object('just prose, no JSON object anywhere')


def test_normalize_results_takes_identity_from_batch():
    # identity comes from the batch items, never from model output
    items = [{"page_id": "p1", "source_id": "src1"}]
    model_out = [{"content_type": "tutorial", "topics": ["x"], "title": "t",
                  "summary": "s", "externals": None,
                  "page_id": "p1", "source_id": "HACKED"}]
    out = base.normalize_results(model_out, items)
    assert out[0]["page_id"] == "p1" and out[0]["source_id"] == "src1"
