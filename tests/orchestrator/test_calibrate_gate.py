import json
import pytest
from insta_save.orchestrator import calibrate_gate
from insta_save.config import tags as tagcfg
from insta_save.helpers import tui


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
    monkeypatch.setattr(calibrate_gate, "_tags_path", lambda: tags)
    return tags


def _selects(monkeypatch, values):
    it = iter(values)
    monkeypatch.setattr(tui, "select", lambda *a, **k: next(it))


def _texts(monkeypatch, values):
    it = iter(values)
    monkeypatch.setattr(tui, "text", lambda *a, **k: next(it))


def _confirms(monkeypatch, values):
    it = iter(values)
    monkeypatch.setattr(tui, "confirm_action", lambda *a, **k: next(it))


def _run(tags, monkeypatch):
    return calibrate_gate.run_calibrate_gate(
        object(), _Run(), collections_cfg=object(), backend=_Backend(), group="G")


def test_confirm_locks_draft(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["done"])              # edit loop: straight to preview
    _confirms(monkeypatch, ["confirm"])
    vocab = _run(tags, monkeypatch)
    assert vocab.has_group("G") and set(vocab.group_topics("G")) == {"t1", "t2"}
    assert json.loads(tags.read_text())["groups"]["G"] == {"t1": "d1", "t2": "d2"}


def test_reject_removes_granular_topic(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    # edit loop: reject -> (sub)select t1 -> done ; then confirm
    _selects(monkeypatch, ["reject", "t1", "done"])
    _confirms(monkeypatch, ["confirm"])
    vocab = _run(tags, monkeypatch)
    assert vocab.group_topics("G") == ["t2"]      # t1 rejected


def test_add_appends_granular_topic(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    # edit loop: add -> axis=granular -> label/def via text -> done ; confirm
    _selects(monkeypatch, ["add", "granular", "done"])
    _texts(monkeypatch, ["t3", "d3"])
    _confirms(monkeypatch, ["confirm"])
    vocab = _run(tags, monkeypatch)
    assert "t3" in vocab.group_topics("G")
    assert json.loads(tags.read_text())["groups"]["G"]["t3"] == "d3"


def test_go_back_returns_to_edit_loop_then_locks(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    # first edit loop: done -> preview -> back -> second edit loop: done -> preview -> confirm
    _selects(monkeypatch, ["done", "done"])
    _confirms(monkeypatch, ["back", "confirm"])
    vocab = _run(tags, monkeypatch)
    assert vocab.has_group("G")


def test_abort_raises(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    _selects(monkeypatch, ["done"])
    _confirms(monkeypatch, ["abort"])
    with pytest.raises(SystemExit):
        _run(tags, monkeypatch)


def test_edit_current_reloads_proposal(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    # The "editor" rewrites the proposed file (adds a topic); then confirm locks it.
    def fake_editor(path):
        d = json.loads(open(path).read()); d["groups"]["G"]["t9"] = "d9"
        open(path, "w").write(json.dumps(d))
    monkeypatch.setattr(calibrate_gate, "_editor", fake_editor)
    monkeypatch.setattr(calibrate_gate, "_proposed_path",
                        lambda env: tmp_path / "calibrate" / "proposed_tags.json")
    _selects(monkeypatch, ["done"])
    _confirms(monkeypatch, ["edit_current", "confirm"])
    vocab = _run(tags, monkeypatch)
    assert "t9" in vocab.group_topics("G")


def test_edit_all_loads_without_lock(tmp_path, monkeypatch):
    tags = _wire(tmp_path, monkeypatch)
    # The "editor" rewrites tags.json directly (the only path that can add a NEW group/cross-group);
    # the gate just load_vocab()s it — no merge, no lock_vocab.
    def fake_editor(path):
        open(path, "w").write(json.dumps(
            {"content_type": {}, "groups": {"G": {"edited": "e"}}, "cross_group": {"shared": "s"}}))
    monkeypatch.setattr(calibrate_gate, "_editor", fake_editor)
    called = {"lock": False}
    monkeypatch.setattr(calibrate_gate, "lock_vocab",
                        lambda *a, **k: called.__setitem__("lock", True))
    _selects(monkeypatch, ["done"])
    _confirms(monkeypatch, ["edit_all"])
    vocab = _run(tags, monkeypatch)
    assert vocab.group_topics("G") == ["edited"] and "shared" in vocab.cross_group_topics
    assert called["lock"] is False                # edit-all does NOT route through lock_vocab
