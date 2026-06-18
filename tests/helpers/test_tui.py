from insta_save.helpers import tui


def test_primitives_exist():
    for name in ("select", "select_or_other", "text", "confirm_action", "OTHER"):
        assert hasattr(tui, name)


def test_select_or_other_returns_plain_choice(monkeypatch):
    monkeypatch.setattr(tui, "select", lambda message, choices, **k: "claude-p")
    assert tui.select_or_other("backend", [("claude-p", "claude-p", "h")]) == "claude-p"


def test_select_or_other_falls_to_text_on_other(monkeypatch):
    monkeypatch.setattr(tui, "select", lambda message, choices, **k: tui.OTHER)
    monkeypatch.setattr(tui, "text", lambda message, **k: "custom-model")
    assert tui.select_or_other("model", [("claude-sonnet", "claude-sonnet", "h")]) == "custom-model"
