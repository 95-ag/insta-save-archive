import json
from pathlib import Path
from insta_save.orchestrator import calibrate_gate


class _Enrich: model = "claude-sonnet"
class _Run: enrich = _Enrich()
class _Backend:
    @staticmethod
    def propose_vocab(prompt, model):
        return {"content_type": {"tool": "x"}, "groups": {"G": {"t": "d"}}, "cross_group": {}}


class _BackendNoVocab:
    """A backend without propose_vocab — exercises the manual-draft path."""


def test_gate_auto_proposes_and_locks(tmp_path, monkeypatch):
    tags = tmp_path / "tags.json"
    tags.write_text(json.dumps({"content_type": {}, "groups": {}, "cross_group": {}}), encoding="utf-8")
    cal_dir = tmp_path / "calibrate"; cal_dir.mkdir()
    (cal_dir / "prompt.txt").write_text("SAMPLE PROMPT", encoding="utf-8")
    monkeypatch.setattr(calibrate_gate, "_sample", lambda env, group, collections_cfg: 3)
    monkeypatch.setattr(calibrate_gate, "_calibrate_prompt_path", lambda env: cal_dir / "prompt.txt")
    monkeypatch.setattr(calibrate_gate, "_tags_path", lambda: tags)

    env = object()
    vocab = calibrate_gate.run_calibrate_gate(
        env, _Run(), collections_cfg=object(), backend=_Backend(), group="G",
        prompt_input=lambda _p: "y")
    assert vocab.has_group("G") and "t" in vocab.group_topics("G")
    locked = json.loads(tags.read_text(encoding="utf-8"))
    assert locked["groups"]["G"] == {"t": "d"}


def test_gate_abort_raises(tmp_path, monkeypatch):
    cal_dir = tmp_path / "calibrate"; cal_dir.mkdir()
    (cal_dir / "prompt.txt").write_text("P", encoding="utf-8")
    monkeypatch.setattr(calibrate_gate, "_sample", lambda env, group, collections_cfg: 1)
    monkeypatch.setattr(calibrate_gate, "_calibrate_prompt_path", lambda env: cal_dir / "prompt.txt")
    import pytest
    with pytest.raises(SystemExit):
        calibrate_gate.run_calibrate_gate(object(), _Run(), collections_cfg=object(),
                                          backend=_Backend(), group="G",
                                          prompt_input=lambda _p: "abort")


def test_gate_manual_path_missing_file_raises_clear_error(tmp_path, monkeypatch):
    # Backend has no propose_vocab and the human accepts without writing the file:
    # expect a clear SystemExit, NOT a raw FileNotFoundError.
    cal_dir = tmp_path / "calibrate"; cal_dir.mkdir()
    (cal_dir / "prompt.txt").write_text("P", encoding="utf-8")
    monkeypatch.setattr(calibrate_gate, "_sample", lambda env, group, collections_cfg: 2)
    monkeypatch.setattr(calibrate_gate, "_calibrate_prompt_path", lambda env: cal_dir / "prompt.txt")
    import pytest
    with pytest.raises(SystemExit) as exc_info:
        calibrate_gate.run_calibrate_gate(object(), _Run(), collections_cfg=object(),
                                          backend=_BackendNoVocab(), group="G",
                                          prompt_input=lambda _p: "y")
    assert "no proposed vocab" in str(exc_info.value)
