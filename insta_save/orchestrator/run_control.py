"""Cooperative pause/stop control for the long `isa run` process.

A RunControl is entered (context manager) around the automated run. The drain
primitives call the free ``checkpoint()`` at their safe boundaries (between enrich
batches / items / sequencer steps); it is a no-op unless a run is active, so
standalone ``--stage`` commands and tests are unaffected.

  p -> toggle pause (finish current unit, flush logs, wait; press p to resume)
  q / Ctrl-C -> graceful stop (RunStopped unwinds to a clean resume-hint exit)
  Ctrl-C twice -> force (default handler restored, KeyboardInterrupt re-raised)

Notion (client-per-call) and claude-p (subprocess-per-batch) hold nothing at the
boundaries, so the only active teardown on pause/stop is an explicit log flush.
"""
import signal
import threading
from contextlib import contextmanager

from insta_save.helpers.observability import flush_logs


class RunStopped(Exception):
    """Raised at a checkpoint when a graceful stop (q / Ctrl-C) was requested."""


_active: "RunControl | None" = None


class RunControl:
    def __init__(self, *, mode: str):
        self.mode = mode
        self._resume = threading.Event()
        self._resume.set()              # set = running, clear = paused
        self._stop = False
        self._sigint_count = 0
        self._prev_sigint = None

    # --- state transitions (called by the key-listener and the signal handler) ---
    def request_pause_toggle(self) -> None:
        if self._resume.is_set():
            self._resume.clear()        # pause
        else:
            self._resume.set()          # resume

    def request_stop(self) -> None:
        self._stop = True
        self._resume.set()              # unblock a paused checkpoint so it sees the stop

    # --- the boundary checkpoint (called by the drains via the free checkpoint()) ---
    def checkpoint(self) -> None:
        if self._stop:
            flush_logs()
            raise RunStopped()
        if not self._resume.is_set():
            flush_logs()
            print("⏸  paused · press p to resume", flush=True)
            self._resume.wait()
            if self._stop:              # stop pressed while paused
                flush_logs()
                raise RunStopped()
            print("▶  resumed", flush=True)

    # --- key-listener hooks (filled in Task 4) ---
    def suspend_keys(self) -> None:
        pass

    def resume_keys(self) -> None:
        pass

    # --- SIGINT handler: first press = graceful stop, second = force ---
    def _on_sigint(self, signum, frame) -> None:
        self._sigint_count += 1
        if self._sigint_count >= 2:
            self._restore_sigint()
            raise KeyboardInterrupt
        print("\n⏹  stopping after the current batch… (Ctrl-C again to stop now)", flush=True)
        self.request_stop()

    def _install_sigint(self) -> None:
        try:
            self._prev_sigint = signal.signal(signal.SIGINT, self._on_sigint)
        except (ValueError, OSError):
            self._prev_sigint = None      # not on the main thread (e.g. some test runners)

    def _restore_sigint(self) -> None:
        if self._prev_sigint is not None:
            try:
                signal.signal(signal.SIGINT, self._prev_sigint)
            except (ValueError, OSError):
                pass
            self._prev_sigint = None

    # --- lifecycle: activate/deactivate the module singleton ---
    def __enter__(self) -> "RunControl":
        global _active
        _active = self
        self._install_sigint()
        return self

    def __exit__(self, *exc) -> bool:
        global _active
        self._restore_sigint()
        _active = None
        return False


def checkpoint() -> None:
    """Free checkpoint the drains call. No-op unless a RunControl is active."""
    if _active is not None:
        _active.checkpoint()


@contextmanager
def gate():
    """Stand the keyboard controls down around an interactive (questionary) gate.
    No-op unless a RunControl is active."""
    if _active is None:
        yield
        return
    _active.suspend_keys()
    try:
        yield
    finally:
        _active.resume_keys()
