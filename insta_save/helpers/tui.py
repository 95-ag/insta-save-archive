"""Keyboard-select TUI helpers over questionary (D27).

Gates call ONLY these four primitives; tests monkeypatch them (questionary needs a
real TTY, so it lives only here). A single pinned Style gives every gate the same
look — accent-colored pointer + bold highlight + dim instructions — regardless of
the terminal's ANSI theme. .ask() returns None on Ctrl-C/EOF; callers treat None as abort."""
import questionary
from questionary import Choice, Style

OTHER = "__other__"

_STYLE = Style([
    ("qmark", "fg:#1D9E75 bold"),
    ("question", "bold"),
    ("pointer", "fg:#1D9E75 bold"),
    ("highlighted", "fg:#1D9E75 bold"),
    ("answer", "fg:#1D9E75"),
    ("instruction", "fg:#888780"),
])


def select(message, choices, *, default=None):
    """Arrow-select one option. `choices`: list of (label, value, help). Only the label is
    shown — questionary highlights a plain-string title's pointed row (an inline-hint title
    would suppress that highlight). Labels must be self-contained; `help` is unused in the
    display (kept for signature compatibility)."""
    qchoices = [Choice(title=label, value=value) for (label, value, _help) in choices]
    return questionary.select(message, choices=qchoices, default=default,
                              style=_STYLE, show_description=False, qmark="?").ask()


def select_or_other(message, choices, *, default=None, other_label="Other…"):
    """select with a trailing 'Other…' that drops to a text prompt for a custom value."""
    val = select(message, list(choices) + [(other_label, OTHER, "type a custom value")],
                 default=default)
    if val == OTHER:
        return text("  value")
    return val


def text(message, *, default=None):
    """Free-text entry (the 'Other…' target and novel names)."""
    return questionary.text(message, default=default or "", style=_STYLE, qmark="?").ask()


def confirm_action(message, actions):
    """Arrow-select over named actions. `actions`: list of (label, value, help). Returns value."""
    return select(message, actions)
