import threading
import pytest
from insta_save.orchestrator import run_control
from insta_save.orchestrator.run_control import RunControl, RunStopped


def test_checkpoint_raises_run_stopped_when_stop_requested():
    rc = RunControl(mode="first-time")
    rc.request_stop()
    with pytest.raises(RunStopped):
        rc.checkpoint()


def test_checkpoint_blocks_while_paused_then_resumes():
    rc = RunControl(mode="first-time")
    rc.request_pause_toggle()          # now paused
    released = []
    t = threading.Thread(target=lambda: (rc.checkpoint(), released.append(True)))
    t.start()
    t.join(timeout=0.2)
    assert released == []               # still blocked at the checkpoint
    rc.request_pause_toggle()          # resume
    t.join(timeout=1.0)
    assert released == [True]


def test_module_checkpoint_is_noop_without_active_control():
    # No active RunControl -> free checkpoint() must do nothing (standalone --stage / tests).
    run_control.checkpoint()           # must not raise


def test_entering_runcontrol_activates_module_checkpoint():
    rc = RunControl(mode="first-time")
    rc.request_stop()
    with rc:
        with pytest.raises(RunStopped):
            run_control.checkpoint()    # routed to the active control
    run_control.checkpoint()           # inactive again -> no-op


def test_gate_is_noop_context_when_inactive():
    with run_control.gate():
        pass                            # must not raise with no active control


import signal


def test_sigint_once_requests_stop(monkeypatch):
    rc = RunControl(mode="first-time")
    monkeypatch.setattr(signal, "signal", lambda *a, **k: signal.SIG_DFL)  # don't touch real handlers
    rc._on_sigint(signal.SIGINT, None)
    with pytest.raises(RunStopped):
        rc.checkpoint()


def test_sigint_twice_restores_and_raises(monkeypatch):
    rc = RunControl(mode="first-time")
    monkeypatch.setattr(signal, "signal", lambda *a, **k: signal.SIG_DFL)
    rc._on_sigint(signal.SIGINT, None)
    with pytest.raises(KeyboardInterrupt):
        rc._on_sigint(signal.SIGINT, None)   # second press forces
