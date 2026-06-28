import json
import pytest
from insta_save.orchestrator import calibrate_gate
from insta_save.config import tags as tagcfg
from insta_save.helpers import tui

_BACK = calibrate_gate._BACK


class _Enrich: model = "claude-sonnet"
class _Run: enrich = _Enrich()
class _Backend:
    @staticmethod
    def propose_vocab(prompt, model):
        return {"content_type": {"tool": "x"}, "groups": {"G": {"t1": "d1", "t2": "d2"}}, "cross_group": {}}


def _wire(tmp_path, monkeypatch, *, draft=True):
    """Common setup: a tags.json, a sampled prompt, and the path/sample stubs."""
    tags = tmp_path / "tags.json"
    tags.write_text(json.dumps({"content_type": {}, "groups": {}, "cross_group": {}}), encoding="utf-8")
    cal_dir = tmp_path / "calibrate"; cal_dir.mkdir()
    (cal_dir / "prompt.txt").write_text("SAMPLE PROMPT", encoding="utf-8")
    monkeypatch.setattr(calibrate_gate, "_sample", lambda env, group, collections_cfg: 3)
    monkeypatch.setattr(calibrate_gate, "_calibrate_prompt_path", lambda env: cal_dir / "prompt.txt")
    monkeypatch.setattr(calibrate_gate, "_proposed_path", lambda env: cal_dir / "proposed_tags.json")
    monkeypatch.setattr(calibrate_gate, "_tags_path", lambda: tags)
    return tags


def _selects(monkeypatch, values):
    """Stub tui.select with a scripted sequence (mode menu, edit loop, reject/add axis)."""
    it = iter(values)
    monkeypatch.setattr(tui, "select", lambda *a, **k: next(it))


def _texts(monkeypatch, values):
    it = iter(values)
    monkeypatch.setattr(tui, "text", lambda *a, **k: next(it))


def _confirms(monkeypatch, values):
    """Stub tui.confirm_action (preview menu + discard confirm)."""
    it = iter(values)
    monkeypatch.setattr(tui, "confirm_action", lambda *a, **k: next(it))


def _run(tags, monkeypatch):
    return calibrate_gate.run_calibrate_gate(
        object(), _Run(), collections_cfg=object(), backend=_Backend(), group="G")


# ---- preserved unit behavior ---------------------------------------------------

def test_draft_degrades_to_empty_on_propose_failure(tmp_path, monkeypatch, capsys):
    _wire(tmp_path, monkeypatch)

    class _Boom:
        @staticmethod
        def propose_vocab(prompt, model):
            raise RuntimeError("claude -p returned non-JSON")

    proposed = calibrate_gate._draft(object(), _Run(), _Boom(), "G")
    # never crashes — degrades to a usable empty skeleton + a clear message
    assert proposed == {"content_type": {}, "groups": {"G": {}}, "cross_group": {}}
    assert "empty draft" in capsys.readouterr().out.lower()


def test_calibrate_prompt_requests_inline_json_not_a_file():
    from pathlib import Path
    t = Path("prompts/calibrate_v2.0.txt").read_text(encoding="utf-8")
    assert "Write the proposal as JSON to tmp/calibrate" not in t   # old file-write idiom gone
    assert "Return ONLY the JSON" in t                              # inline-return contract


# ---- mode menu: the FIRST decision --------------------------------------------

def test_accept_as_is_locks_draft_unchanged(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["accept"])              # mode menu -> accept -> preview -> confirm
    _confirms(monkeypatch, ["confirm"])            # T4: accept now routes through preview+confirm
    vocab = _run(tags, monkeypatch)
    assert vocab.has_group("G") and set(vocab.group_topics("G")) == {"t1", "t2"}
    assert json.loads(tags.read_text())["groups"]["G"] == {"t1": "d1", "t2": "d2"}


def test_editor_draft_first_then_confirm(tmp_path, monkeypatch):
    """$EDITOR reachable as the FIRST choice (no inline loop), reload, preview, confirm."""
    tags = _wire(tmp_path, monkeypatch)

    def fake_editor(path):
        d = json.loads(open(path).read()); d["groups"]["G"]["t9"] = "d9"
        open(path, "w").write(json.dumps(d))
    monkeypatch.setattr(calibrate_gate, "_editor", fake_editor)
    _selects(monkeypatch, ["editor_draft"])
    _confirms(monkeypatch, ["confirm"])
    vocab = _run(tags, monkeypatch)
    assert "t9" in vocab.group_topics("G")


def test_editor_draft_invalid_json_discards_edit_and_recovers(tmp_path, monkeypatch, capsys):
    """A garbage $EDITOR write on the draft is discarded (proposal unchanged); the gate
    returns to the top menu — then accept locks the ORIGINAL draft."""
    tags = _wire(tmp_path, monkeypatch)
    monkeypatch.setattr(calibrate_gate, "_editor", lambda path: open(path, "w").write("{not json"))
    _selects(monkeypatch, ["editor_draft", "accept"])   # bad edit -> back to menu -> accept
    _confirms(monkeypatch, ["confirm"])                  # T4: accept now goes through confirm
    vocab = _run(tags, monkeypatch)
    assert "discarding that edit" in capsys.readouterr().out
    assert set(vocab.group_topics("G")) == {"t1", "t2"}   # original draft, edit dropped


def test_editor_all_invalid_json_reprompts(tmp_path, monkeypatch, capsys):
    """A malformed config/tags.json from $EDITOR doesn't crash — it prints + re-loops; a
    second editor_all that writes valid JSON then loads and returns."""
    tags = _wire(tmp_path, monkeypatch)
    calls = {"n": 0}

    def fake_editor(path):
        calls["n"] += 1
        if calls["n"] == 1:
            open(path, "w").write("{bad")               # first edit: garbage
        else:
            open(path, "w").write(json.dumps(           # second edit: valid, fixes it
                {"content_type": {}, "groups": {"G": {"fixed": "f"}}, "cross_group": {}}))
    monkeypatch.setattr(calibrate_gate, "_editor", fake_editor)
    _selects(monkeypatch, ["editor_all", "editor_all"])  # bad -> re-loop -> good -> keep
    _confirms(monkeypatch, ["keep"])                     # T5: editor_all now shows preview+confirm
    vocab = _run(tags, monkeypatch)
    assert "invalid tags.json" in capsys.readouterr().out
    assert vocab.group_topics("G") == ["fixed"]


def test_ctrlc_at_preview_menu_returns_to_top_menu(tmp_path, monkeypatch):
    """Ctrl-C at the preview menu (confirm_action -> None) is treated as Back, not abort."""
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["inline", "done", "accept"])
    _confirms(monkeypatch, [None, "confirm"])              # Ctrl-C at preview -> top menu -> accept -> confirm
    vocab = _run(tags, monkeypatch)
    assert vocab.has_group("G")


def test_editor_all_first_loads_without_lock(tmp_path, monkeypatch):
    """Edit-all = the only path that can add/remove a shared item; load_vocab, no lock_vocab."""
    tags = _wire(tmp_path, monkeypatch)

    def fake_editor(path):
        open(path, "w").write(json.dumps(
            {"content_type": {}, "groups": {"G": {"edited": "e"}}, "cross_group": {"shared": "s"}}))
    monkeypatch.setattr(calibrate_gate, "_editor", fake_editor)
    called = {"lock": False}
    monkeypatch.setattr(calibrate_gate, "lock_vocab", lambda *a, **k: called.__setitem__("lock", True))
    _selects(monkeypatch, ["editor_all"])
    _confirms(monkeypatch, ["keep"])                     # T5: editor_all now shows preview+confirm
    vocab = _run(tags, monkeypatch)
    assert vocab.group_topics("G") == ["edited"] and "shared" in vocab.cross_group_topics
    assert called["lock"] is False


# ---- inline editing ------------------------------------------------------------

def test_inline_reject_then_lock(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["inline", "reject", "t1", "done"])
    _confirms(monkeypatch, ["confirm"])
    vocab = _run(tags, monkeypatch)
    assert vocab.group_topics("G") == ["t2"]


def test_inline_add_granular_then_lock(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["inline", "add", "granular", "done"])
    _texts(monkeypatch, ["t3", "d3"])
    _confirms(monkeypatch, ["confirm"])
    vocab = _run(tags, monkeypatch)
    assert "t3" in vocab.group_topics("G")
    assert json.loads(tags.read_text())["groups"]["G"]["t3"] == "d3"


def test_inline_add_cross_group_then_lock(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["inline", "add", "cross_group", "done"])
    _texts(monkeypatch, ["shared", "sdef"])
    _confirms(monkeypatch, ["confirm"])
    vocab = _run(tags, monkeypatch)
    assert "shared" in vocab.cross_group_topics


# ---- back / cancel at every level (never rely on Ctrl-C) ----------------------

def test_reject_back_keeps_topic(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["inline", "reject", _BACK, "done"])   # back out of reject
    _confirms(monkeypatch, ["confirm"])
    vocab = _run(tags, monkeypatch)
    assert set(vocab.group_topics("G")) == {"t1", "t2"}          # nothing removed


def test_add_back_at_axis_adds_nothing(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["inline", "add", _BACK, "done"])      # back out at the axis menu
    _confirms(monkeypatch, ["confirm"])
    vocab = _run(tags, monkeypatch)
    assert set(vocab.group_topics("G")) == {"t1", "t2"}


def test_add_blank_label_adds_nothing(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["inline", "add", "granular", "done"])
    _texts(monkeypatch, [""])                                    # blank label cancels; no def prompt
    _confirms(monkeypatch, ["confirm"])
    vocab = _run(tags, monkeypatch)
    assert set(vocab.group_topics("G")) == {"t1", "t2"}


def test_ctrlc_in_edit_loop_returns_to_top_menu_not_systemexit(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    # inline -> edit loop Ctrl-C (None) -> back to top menu -> accept -> confirm
    _selects(monkeypatch, ["inline", None, "accept"])
    _confirms(monkeypatch, ["confirm"])                          # T4: accept now routes through confirm
    vocab = _run(tags, monkeypatch)
    assert vocab.has_group("G")


def test_preview_back_returns_to_top_menu(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["inline", "done", "accept"])
    _confirms(monkeypatch, ["back", "confirm"])                  # preview -> back to menu -> accept -> confirm
    vocab = _run(tags, monkeypatch)
    assert vocab.has_group("G")


# T4: "Show full JSON" option removed — delete test_preview_show_full_json_then_confirm


# ---- abort = the only destructive exit, and it's confirmed --------------------

def test_abort_keep_editing_does_not_exit(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["abort", "accept"])                   # abort -> keep -> loop -> accept
    _confirms(monkeypatch, ["keep", "confirm"])                  # T4: accept now routes through confirm
    vocab = _run(tags, monkeypatch)
    assert vocab.has_group("G")


def test_abort_discard_exits(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["abort"])
    _confirms(monkeypatch, ["discard"])
    with pytest.raises(SystemExit):
        _run(tags, monkeypatch)


def test_mode_menu_ctrlc_discard_exits(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, [None])                              # Ctrl-C at the mode menu
    _confirms(monkeypatch, ["discard"])
    with pytest.raises(SystemExit):
        _run(tags, monkeypatch)


def test_mode_menu_ctrlc_keep_loops(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, [None, "accept"])                    # Ctrl-C -> keep -> loop -> accept
    _confirms(monkeypatch, ["keep", "confirm"])                # T4: accept now routes through confirm
    vocab = _run(tags, monkeypatch)
    assert vocab.has_group("G")


def test_no_extracted_items_raises(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    monkeypatch.setattr(calibrate_gate, "_sample", lambda env, group, collections_cfg: 0)
    with pytest.raises(SystemExit):
        _run(tags, monkeypatch)


# ---- framing preserved ---------------------------------------------------------

def test_calibrate_gate_framing(tmp_path, monkeypatch, capsys):
    """Gate prints nested stage_section header/footer rules and an indented ✔ outcome line."""
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["accept"])
    _confirms(monkeypatch, ["confirm"])                          # T4: accept now goes through confirm
    _run(tags, monkeypatch)
    out = capsys.readouterr().out
    assert "calibrate · G" in out           # nested stage_section header rule
    assert "done · calibrate · G" in out    # nested stage_section footer rule
    assert "✔" in out                       # outcome line printed
    for line in out.splitlines():
        if "✔" in line:
            assert line.startswith(" "), "✔ outcome line must be indented"
            break


# ---- T2: proposed granular as bulleted list with definitions --------------------

def test_context_lists_proposed_topics_with_definitions(tmp_path, monkeypatch, capsys):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["abort"])           # print context + mode menu, then bail
    _confirms(monkeypatch, ["discard"])
    with pytest.raises(SystemExit):
        _run(tags, monkeypatch)
    out = capsys.readouterr().out
    assert "• t1" in out and "d1" in out
    assert "• t2" in out and "d2" in out


# ---- T3: preview shows topic definitions ----------------------------------------

def test_preview_shows_definitions(tmp_path, monkeypatch, capsys):
    """The preview table must show both the topic name and its definition."""
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["accept"])
    _confirms(monkeypatch, ["confirm"])        # accept -> preview -> confirm
    _run(tags, monkeypatch)
    out = capsys.readouterr().out
    assert "t1" in out and "d1" in out         # topic AND its definition rendered


# ---- T4: loop-coverage tests ---------------------------------------------------

def test_editor_all_ctrlc_at_menu_returns_to_top(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    monkeypatch.setattr(calibrate_gate, "_editor", lambda path: open(path, "w").write(json.dumps(
        {"content_type": {}, "groups": {"G": {"e": "ed"}}, "cross_group": {}})))
    _selects(monkeypatch, ["editor_all", "accept"])   # editor_all -> None at EDITALL menu -> top -> accept
    _confirms(monkeypatch, [None, "confirm"])         # None at EDITALL menu = back to top; confirm locks
    vocab = _run(tags, monkeypatch)
    assert vocab.has_group("G")


def test_edit_loop_multiple_actions_then_lock(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["inline", "add", "granular", "reject", "t1", "done"])
    _texts(monkeypatch, ["t3", "d3"])
    _confirms(monkeypatch, ["confirm"])
    vocab = _run(tags, monkeypatch)
    topics = vocab.group_topics("G")
    assert "t3" in topics and "t1" not in topics


def test_preview_back_then_inline_then_lock(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["accept", "inline", "done"])   # accept->preview->back->top->inline->done->preview
    _confirms(monkeypatch, ["back", "confirm"])
    vocab = _run(tags, monkeypatch)
    assert vocab.has_group("G")


# ---- T5: editor_all reedit-then-keep -------------------------------------------

def test_editor_all_reedit_then_keep(tmp_path, monkeypatch):
    """editor_all: reedit -> keep — editor should be called twice."""
    tags = _wire(tmp_path, monkeypatch)
    calls = {"n": 0}

    def fake_editor(path):
        calls["n"] += 1
        open(path, "w").write(json.dumps(
            {"content_type": {}, "groups": {"G": {"edited": "e"}}, "cross_group": {}}))
    monkeypatch.setattr(calibrate_gate, "_editor", fake_editor)
    _selects(monkeypatch, ["editor_all"])
    _confirms(monkeypatch, ["reedit", "keep"])   # first: reedit; second: keep
    vocab = _run(tags, monkeypatch)
    assert calls["n"] == 2                       # editor called twice
    assert vocab.group_topics("G") == ["edited"]


# ---- C1 review: guard keep branch when load_vocab fails -----------------------

def test_editor_all_keep_with_invalid_json_reprompts(tmp_path, monkeypatch, capsys):
    """editor_all: reedit writes garbage -> keep while still invalid -> must NOT raise (re-loops);
    a subsequent reedit with valid JSON then keep returns the vocab."""
    tags = _wire(tmp_path, monkeypatch)
    calls = {"n": 0}

    def fake_editor(path):
        calls["n"] += 1
        if calls["n"] == 1:
            # First edit (initial editor_all): valid JSON
            open(path, "w").write(json.dumps(
                {"content_type": {}, "groups": {"G": {"v1": "d1"}}, "cross_group": {}}))
        elif calls["n"] == 2:
            # reedit: write garbage — load_vocab will fail
            open(path, "w").write("{bad json")
        else:
            # Third call (reedit again): fix it
            open(path, "w").write(json.dumps(
                {"content_type": {}, "groups": {"G": {"recovered": "r"}}, "cross_group": {}}))
    monkeypatch.setattr(calibrate_gate, "_editor", fake_editor)
    _selects(monkeypatch, ["editor_all"])
    # Initial edit valid -> reedit (writes garbage) -> keep (invalid file, must re-loop not crash)
    # -> reedit (writes valid) -> keep (returns)
    _confirms(monkeypatch, ["reedit", "keep", "reedit", "keep"])
    vocab = _run(tags, monkeypatch)
    assert "invalid tags.json" in capsys.readouterr().out
    assert vocab.group_topics("G") == ["recovered"]
