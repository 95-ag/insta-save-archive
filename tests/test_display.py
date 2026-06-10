import pytest

from insta_save.adapters.instagram import session


def test_resolve_explicit_mode_passthrough():
    assert session.resolve_display_mode("native", is_wsl=lambda: True) == "native"
    assert session.resolve_display_mode("none", is_wsl=lambda: True) == "none"


def test_resolve_auto_picks_vcxsrv_on_wsl():
    assert session.resolve_display_mode("auto", is_wsl=lambda: True) == "wsl-vcxsrv"


def test_resolve_auto_picks_native_off_wsl():
    assert session.resolve_display_mode("auto", is_wsl=lambda: False) == "native"


def test_apply_strategy_noop_when_headless(monkeypatch):
    called = []
    monkeypatch.setattr(session, "_launch_vcxsrv_strategy", lambda: called.append("x"))
    session.apply_display_strategy("wsl-vcxsrv", headless=True)
    assert called == []  # headless never needs a display


def test_apply_strategy_noop_for_native(monkeypatch):
    called = []
    monkeypatch.setattr(session, "_launch_vcxsrv_strategy", lambda: called.append("x"))
    session.apply_display_strategy("native", headless=False)
    assert called == []  # native headed handled by playwright, no external X


def test_apply_strategy_invokes_vcxsrv_only_on_wsl_headed(monkeypatch):
    called = []
    monkeypatch.setattr(session, "_launch_vcxsrv_strategy", lambda: called.append("x"))
    session.apply_display_strategy("wsl-vcxsrv", headless=False)
    assert called == ["x"]


def test_resolve_display_mode_invalid_raises():
    with pytest.raises(ValueError):
        session.resolve_display_mode("bogus")
