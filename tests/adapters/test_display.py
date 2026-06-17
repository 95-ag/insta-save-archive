import os
import types

import pytest

from insta_save.adapters.instagram import display, session


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


# --- X-readiness gate (display.py) -------------------------------------------
# Regression: ensure_display() used to return as soon as TCP 6001 opened, but VcXsrv
# serves X only a few seconds later — a premature client died with "Missing X server".

def test_wait_x_ready_returns_once_server_answers(monkeypatch):
    monkeypatch.setattr(display.shutil, "which", lambda _: "/usr/bin/xdpyinfo")
    monkeypatch.setattr(display.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def fake_ready(_display):
        calls["n"] += 1
        return calls["n"] >= 3  # not ready for the first two polls

    monkeypatch.setattr(display, "_x_ready", fake_ready)
    display._wait_x_ready("host:1.0", timeout=5)
    assert calls["n"] == 3  # polled until the handshake succeeded


def test_wait_x_ready_raises_when_never_serves(monkeypatch):
    monkeypatch.setattr(display.shutil, "which", lambda _: "/usr/bin/xdpyinfo")
    monkeypatch.setattr(display.time, "sleep", lambda _s: None)
    monkeypatch.setattr(display, "_x_ready", lambda _display: False)
    with pytest.raises(RuntimeError):
        display._wait_x_ready("host:1.0", timeout=0.0)


def test_wait_x_ready_settles_when_xdpyinfo_absent(monkeypatch):
    monkeypatch.setattr(display.shutil, "which", lambda _: None)
    slept = []
    monkeypatch.setattr(display.time, "sleep", lambda s: slept.append(s))
    probed = []
    monkeypatch.setattr(display, "_x_ready", lambda d: probed.append(d) or True)
    display._wait_x_ready("host:1.0")
    assert slept and not probed  # fell back to a settle, never probed


# --- prepare_display: DISPLAY must be set BEFORE Playwright's driver starts ---------
# Regression: the driver freezes os.environ at launch, so a DISPLAY set later (inside
# _launch_browser on a headed re-auth) never reached Chromium ("Missing X server").

def test_prepare_display_sets_display_for_vcxsrv(monkeypatch):
    monkeypatch.setattr(display, "display_string", lambda: "1.2.3.4:1.0")
    monkeypatch.setenv("DISPLAY", ":0")  # ambient (e.g. WSLg) — must be overridden
    session.prepare_display(types.SimpleNamespace(display_mode="wsl-vcxsrv"))
    assert os.environ["DISPLAY"] == "1.2.3.4:1.0"


def test_prepare_display_noop_for_native(monkeypatch):
    monkeypatch.setattr(display, "display_string", lambda: "1.2.3.4:1.0")
    monkeypatch.setenv("DISPLAY", ":0")
    session.prepare_display(types.SimpleNamespace(display_mode="native"))
    assert os.environ["DISPLAY"] == ":0"  # untouched on the native path
