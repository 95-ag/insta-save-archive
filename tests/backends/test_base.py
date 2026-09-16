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


def test_parse_results_array_extracts_from_prose_wrapped_output():
    text = ("I don't have write permission to tmp/enrich/. Here's the array:\n\n"
            '```json\n[{"page_id": "p1", "content_type": "tool", "topics": ["x"]}]\n```\n\n'
            "**Note:** done.")
    out = base.parse_results_array(text)
    assert out[0]["page_id"] == "p1"


def test_parse_results_array_raises_when_no_array():
    import pytest
    with pytest.raises(ValueError):
        base.parse_results_array("just prose, no array here")


def test_normalize_results_takes_identity_from_batch():
    # identity comes from the batch items, never from model output
    items = [{"page_id": "p1", "source_id": "src1"}]
    model_out = [{"content_type": "tutorial", "topics": ["x"], "title": "t",
                  "summary": "s", "externals": None,
                  "page_id": "p1", "source_id": "HACKED"}]
    out = base.normalize_results(model_out, items)
    assert out[0]["page_id"] == "p1" and out[0]["source_id"] == "src1"


from insta_save.backends.base import TerminalBackendError, is_terminal_error


def test_is_terminal_error_flags_usage_limit():
    assert is_terminal_error(RuntimeError("Claude usage limit reached. Your limit will reset at 5pm"))


def test_is_terminal_error_flags_auth():
    assert is_terminal_error(RuntimeError("Not logged in. Please run /login"))
    assert is_terminal_error(RuntimeError("authentication_error: invalid api key"))


def test_is_terminal_error_transient_is_false():
    # malformed JSON from a flaky model reply is transient (retryable)
    assert not is_terminal_error(ValueError("Expecting ',' delimiter: line 24 column 156"))
    assert not is_terminal_error(RuntimeError("claude -p exited 1: socket hang up"))


def test_terminal_backend_error_is_exception():
    assert issubclass(TerminalBackendError, Exception)


def test_is_terminal_error_flags_expired_oauth():
    # 2026-09-08 incident: claude -p returned this in its error envelope. It must be TERMINAL
    # (stop + re-auth), never retried-as-transient and mislabelled "malformed model output".
    assert is_terminal_error(RuntimeError(
        "claude -p error: Failed to authenticate. API Error: 401 OAuth access token has "
        "expired. Re-authenticate to continue."))


def test_parse_results_array_tolerates_raw_control_chars():
    # 2026-09-08: the model echoes food/fitness captions with unescaped newlines into JSON
    # string values; strict json.loads raised "Invalid control character". Must parse now.
    out = base.parse_results_array('[{"page_id": "p1", "summary": "line one\nline two"}]')
    assert out[0]["page_id"] == "p1"
    assert out[0]["summary"] == "line one\nline two"  # content preserved, not stripped


def test_parse_results_object_tolerates_raw_control_chars():
    out = base.parse_results_object('{"content_type": {"tool": "a\tb"}}')
    assert out["content_type"]["tool"] == "a\tb"


# ---------------------------------------------------------------------------
# JSON Lines results contract (enrich v2.1)
# ---------------------------------------------------------------------------

def test_parse_results_jsonl_parses_every_good_line():
    text = ('{"page_id": "p1", "title": "a"}\n'
            '{"page_id": "p2", "title": "b"}\n')
    items, bad = base.parse_results_jsonl(text)
    assert [i["page_id"] for i in items] == ["p1", "p2"]
    assert bad == []


def test_parse_results_jsonl_keeps_good_lines_and_returns_bad_ones():
    # the live failure mode: one unescaped quote in one summary. Under the array contract
    # that one line killed all 15 items; here the other lines must survive.
    text = ('{"page_id": "p1", "summary": "fine"}\n'
            '{"page_id": "p2", "summary": "he said "no" here"}\n'
            '{"page_id": "p3", "summary": "also fine"}\n')
    items, bad = base.parse_results_jsonl(text)
    assert [i["page_id"] for i in items] == ["p1", "p3"]
    assert len(bad) == 1 and "p2" in bad[0]


def test_parse_results_jsonl_ignores_blank_lines_and_fences():
    text = ('```json\n'
            '\n'
            '{"page_id": "p1"}\n'
            '   \n'
            '{"page_id": "p2"}\n'
            '```\n')
    items, bad = base.parse_results_jsonl(text)
    assert [i["page_id"] for i in items] == ["p1", "p2"]
    assert bad == []


def test_parse_results_jsonl_takes_objects_from_a_single_line_array():
    # a model that ignores the line-delimited contract and emits the whole array on one
    # line must still be usable — the objects are taken, not reported as a bad line.
    items, bad = base.parse_results_jsonl('[{"page_id": "p1"}, {"page_id": "p2"}]')
    assert [i["page_id"] for i in items] == ["p1", "p2"]
    assert bad == []


def test_parse_results_jsonl_never_raises_on_garbage():
    items, bad = base.parse_results_jsonl("not json at all\nnor this")
    assert items == []
    assert bad == ["not json at all", "nor this"]


def test_parse_results_jsonl_tolerates_raw_control_chars():
    items, bad = base.parse_results_jsonl('{"page_id": "p1", "summary": "a\tb"}')
    assert items[0]["summary"] == "a\tb" and bad == []


def test_parse_results_reads_jsonl_file(tmp_path):
    p = tmp_path / "results.json"
    p.write_text('{"page_id": "p1"}\n{"page_id": "p2"}\n', encoding="utf-8")
    assert [i["page_id"] for i in base.parse_results(p)] == ["p1", "p2"]


def test_parse_results_still_reads_a_single_line_array(tmp_path):
    p = tmp_path / "results.json"
    p.write_text('[{"page_id": "p1"}, {"page_id": "p2"}]', encoding="utf-8")
    assert [i["page_id"] for i in base.parse_results(p)] == ["p1", "p2"]


def test_parse_results_raises_on_unusable_content(tmp_path):
    p = tmp_path / "results.json"
    p.write_text("the model explained itself instead of answering", encoding="utf-8")
    with pytest.raises(ValueError):
        base.parse_results(p)
