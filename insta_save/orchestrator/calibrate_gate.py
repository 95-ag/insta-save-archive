"""Interactive per-group calibrate vocab editor (D18, D28).

Samples the group, gets a DRAFT vocab from the backend (propose_vocab), then runs an
interactive gate whose FIRST decision is how to edit: accept the draft as-is, edit inline
(guided reject/add), hand-edit the draft in $EDITOR, hand-edit the whole config/tags.json in
$EDITOR, or abort. Inline editing and the preview offer a visible "← Back" at every level;
Ctrl-C (the tui primitives return None) means "go up one level" everywhere — it NEVER aborts
the run. The only destructive exit is the explicit Abort entry, guarded by a discard confirm.

Confirm locks via lock_vocab (granular outright, content-type/cross-group additive); edit-all
is the only path that can remove a shared item (the editor write IS the lock, no lock_vocab).
Reachable inline (sequence._run_loop) and standalone (isa run --stage calibrate --group G).
The human lock is preserved — the backend only drafts."""
import json
import os
import subprocess
from pathlib import Path

from rich.console import Console
from rich.table import Table

from insta_save.config.tags import Vocab, lock_vocab, load_vocab, merge_vocab
from insta_save.helpers import observability, tui
from insta_save.helpers.observability import stage_section, RULE_NESTED, INDENT
from insta_save.stages import calibrate as _calibrate

# Sentinel for a visible "← Back" choice. The gate treats both _BACK and None (Ctrl-C) as
# "cancel this level / go up one" — so a back-out is discoverable AND keyboard-interrupt safe.
_BACK = "__back__"

_console = Console()


def _calibrate_prompt_path(env) -> Path:
    return Path(env.tmp_dir) / "calibrate" / "prompt.txt"


def _proposed_path(env) -> Path:
    return _calibrate_prompt_path(env).parent / "proposed_tags.json"


def _tags_path() -> Path:
    return Path("config") / "tags.json"


def _sample(env, group, collections_cfg) -> int:
    """Thin wrapper: reads the calibrate prompt template and delegates to calibrate.sample."""
    template = Path("prompts/calibrate_v2.0.txt").read_text(encoding="utf-8")
    return _calibrate.sample(
        env,
        group=group,
        collections_cfg=collections_cfg,
        limit=None,
        statuses=["Extracted"],
        prompt_template=template,
    )


def _editor(path) -> None:
    """Open $EDITOR on `path` (wrapped so tests can stub it)."""
    subprocess.run([os.environ.get("EDITOR", "nano"), str(path)], check=False)


def _draft(env, run_cfg, backend, group) -> dict:
    """Return a draft proposal (from the backend, or an empty skeleton), persisted to the
    proposed file. Always shaped content_type/groups[group]/cross_group."""
    prompt = _calibrate_prompt_path(env).read_text(encoding="utf-8")
    if hasattr(backend, "propose_vocab"):
        try:
            with observability.spinner(f"Drafting vocab for {group} via the backend…"):
                proposed = backend.propose_vocab(prompt, run_cfg.enrich.model)
        except Exception as exc:
            # A flaky/malformed AI draft must never crash a multi-hour run — degrade to an
            # empty draft and let the human build it via the edit loop.
            print(f"Backend draft failed ({exc}) — starting from an empty draft for {group}. "
                  f"Use 'Edit inline (guided)' or 'Edit this draft in $EDITOR' to build it.")
            proposed = {}
    else:
        print(f"Backend has no propose_vocab — starting from an empty draft for {group}. "
              f"Use 'Edit inline (guided)' or 'Edit this draft in $EDITOR' to build it.")
        proposed = {}
    proposed.setdefault("content_type", {})
    proposed.setdefault("groups", {}).setdefault(group, {})
    proposed.setdefault("cross_group", {})
    _proposed_path(env).write_text(
        json.dumps(proposed, ensure_ascii=False, indent=2), encoding="utf-8")
    return proposed


def _topic_table(rows) -> Table:
    """Build a shared-style rich Table with marker / topic / definition columns.
    `rows` is an iterable of (marker_markup, topic, definition) tuples."""
    table = Table(show_header=True, header_style="bold", box=None,
                  padding=(0, 1), show_edge=False)
    table.add_column("", style="bold", width=2)
    table.add_column("Topic", style="")
    table.add_column("Definition", style="dim")
    for marker, topic, defn in rows:
        table.add_row(marker, topic, defn or "")
    return table


def _print_context(proposed, group, current) -> None:
    """Read-only orientation across the three axes. Rendered as a rich Table so the
    layout matches the preview diff — no em-dash separators."""
    pg = proposed.get("groups", {}).get(group, {})
    _console.print(f"\n[bold]=== calibrate context: {group} ===[/bold]")
    rows = [(f"[green]•[/green]", topic, defn) for topic, defn in pg.items()] if pg else [("", "(none)", "")]
    _console.print(_topic_table(rows))
    others = {g: list(t) for g, t in current.get("groups", {}).items() if g != group}
    if others:
        _console.print("[dim]Other groups:[/dim]")
        for g, topics in others.items():
            _console.print(f"[dim]  {g}: {', '.join(topics) or '(none)'}[/dim]")
    cross = sorted(set(current.get("cross_group", {})) | set(proposed.get("cross_group", {})))
    _console.print(f"[dim]Cross-group: {', '.join(cross) or '(none)'}[/dim]")
    ctypes = sorted(set(current.get("content_type", {})) | set(proposed.get("content_type", {})))
    _console.print(f"[dim]Content-types: {', '.join(ctypes) or '(none)'}[/dim]")


# ---- inline editing (back-aware at every level) -------------------------------

def _reject(proposed, group) -> None:
    """Remove one of the current group's granular topics. A visible '← Back' (and Ctrl-C)
    cancels without removing. Shared content-type/cross-group removal is 'Edit all'."""
    granular = proposed.get("groups", {}).get(group, {})
    if not granular:
        print(f"  {group} has no granular topics to reject "
              f"(remove shared content-type/cross-group items via 'Edit all').")
        return
    choices = [(t, t, granular[t]) for t in granular] + [("← Back", _BACK, "cancel, remove nothing")]
    topic = tui.select(f"Reject which granular topic from {group}?", choices)
    if topic in (None, _BACK):
        return
    proposed["groups"][group].pop(topic, None)
    print(f"  rejected {topic!r}")


_AXES = [
    ("granular (this group)", "granular", ""),
    ("content-type (shared, additive)", "content_type", ""),
    ("cross-group (shared, additive)", "cross_group", ""),
    ("← Back", _BACK, ""),
]


def _add(proposed, group) -> None:
    """Add a topic on a chosen axis. '← Back' / Ctrl-C cancels at the axis menu; a blank
    label cancels before anything is added."""
    axis = tui.select("Add a topic to which axis?", _AXES)
    if axis in (None, _BACK):
        return
    label = (tui.text("  topic label (blank = cancel)") or "").strip()
    if not label:
        print("  cancelled — nothing added.")
        return
    definition = (tui.text("  one-line definition") or "").strip()
    if axis == "granular":
        proposed.setdefault("groups", {}).setdefault(group, {})[label] = definition
    else:
        proposed.setdefault(axis, {})[label] = definition
    print(f"  added {label!r} to {axis}")


_EDIT_ACTIONS = [
    ("Remove a topic from this group", "reject", ""),
    ("Add a topic to any axis", "add", ""),
    ("Show the context table again", "context", ""),
    ("Done — review & lock", "done", ""),
    ("← Back (lock nothing)", "back", ""),
]


def _edit_loop(proposed, group, current) -> bool:
    """Mutate `proposed` in place via tui actions. Returns True when the user chose Done
    (proceed to preview), False when they backed out (Ctrl-C or '← Back' → return to the
    top menu). Never raises — Ctrl-C means 'go up one level', not 'abort the run'."""
    while True:
        action = tui.select(f"Edit vocab for {group}", _EDIT_ACTIONS)
        if action in (None, "back"):
            return False
        if action == "done":
            return True
        if action == "reject":
            _reject(proposed, group)
        elif action == "add":
            _add(proposed, group)
        elif action == "context":
            _print_context(proposed, group, current)


# ---- mode menu, preview, discard confirm --------------------------------------

_MODE_ACTIONS = [
    ("Accept the draft, then review and lock", "accept", ""),
    ("Edit topics one by one", "inline", ""),
    ("Open the draft in a text editor", "editor_draft", ""),
    ("Open the whole tag file in a text editor", "editor_all", ""),
    ("Cancel — save nothing", "abort", ""),
]


def _mode_menu(group, proposed) -> str | None:
    g = len(proposed.get("groups", {}).get(group, {}))
    c = len(proposed.get("content_type", {}))
    x = len(proposed.get("cross_group", {}))
    msg = f"How do you want to set {group}'s vocab?  (draft: {g} granular, +{c} content, +{x} cross)"
    return tui.select(msg, _MODE_ACTIONS)


def _preview_diff(current, proposed, group) -> dict:
    """Print a rich table with per-axis diff (added/removed, with definitions) and return the
    merged dict. Granular is set outright; content-type/cross-group are additive.
    Uses the same _topic_table layout as _print_context — no em-dash separators."""
    merged = merge_vocab(current, group, proposed)

    rows = []
    # Granular axis (this group)
    cur_g = set(current.get("groups", {}).get(group, {}))
    new_g_dict = merged.get("groups", {}).get(group, {})
    removed_g = [t for t in cur_g if t not in new_g_dict]
    for topic, defn in new_g_dict.items():
        marker = "[green]+[/green]" if topic not in cur_g else " "
        rows.append((marker, topic, defn or ""))
    for topic in removed_g:
        rows.append(("[red]−[/red]", topic, ""))

    # Content-type and cross-group: only show newly added rows
    for key, axis_label in (("content_type", "content-type"), ("cross_group", "cross-group")):
        cur_axis = set(current.get(key, {}))
        merged_axis = merged.get(key, {})
        for topic, defn in merged_axis.items():
            if topic not in cur_axis:
                rows.append(("[green]+[/green]", f"{topic}  [dim]({axis_label})[/dim]", defn or ""))

    print(f"\n=== Preview: {group} ===")
    _console.print(_topic_table(rows))
    return merged


_PREVIEW_ACTIONS = [
    ("Lock it", "confirm", ""),
    ("← Back to menu", "back", ""),
    ("Cancel", "abort", ""),
]


def _preview_menu() -> str | None:
    return tui.confirm_action("Lock this vocab?", _PREVIEW_ACTIONS)


_DISCARD_ACTIONS = [
    ("Keep editing", "keep", ""),
    ("Discard & exit", "discard", ""),
]


def _confirm_discard(group) -> bool:
    """True iff the user confirms discarding. Guards the only destructive exit so a
    fat-finger Abort / Ctrl-C can't silently throw away the vocab."""
    return tui.confirm_action(f"Discard {group} vocab and exit?", _DISCARD_ACTIONS) == "discard"


def _done(vocab, group, pad) -> Vocab:
    n_types = len(vocab.content_types)
    n_topics = len(vocab.group_topics(group)) if vocab.has_group(group) else 0
    print(f"{pad}✔ locked {n_types} types · {n_topics} topics")
    return vocab


def run_calibrate_gate(env, run_cfg, *, collections_cfg, backend, group):
    """Sample -> draft -> upfront mode menu (accept / inline / $EDITOR / abort) -> compact
    preview -> lock for `group`. Returns the reloaded Vocab. Raises SystemExit on a confirmed
    abort or nothing to sample. Ctrl-C anywhere goes up one level, never aborts the run."""
    with observability.spinner(f"Reading a balanced sample from {group}…"):
        n = _sample(env, group, collections_cfg)
    if n == 0:
        raise SystemExit(f"calibrate gate: no Extracted items to sample for group {group!r}")

    proposed = _draft(env, run_cfg, backend, group)
    tags_path = _tags_path()
    current = json.loads(tags_path.read_text(encoding="utf-8")) if tags_path.exists() else {}

    pad = " " * INDENT
    with stage_section(f"calibrate · {group}", width=RULE_NESTED, indent=INDENT):
        while True:                                    # top menu
            _print_context(proposed, group, current)
            mode = _mode_menu(group, proposed)
            if mode in (None, "abort"):
                if _confirm_discard(group):
                    raise SystemExit(f"calibrate gate: aborted for group {group!r} — nothing locked")
                continue

            if mode == "editor_all":
                _editor(tags_path)                     # the editor IS the write — load, don't lock
                try:
                    load_vocab(path=tags_path)  # validate — raises on malformed JSON
                except Exception as exc:
                    print(f"  invalid tags.json: {exc} — re-edit")
                    continue
                # Show a preview of what was written and let the user confirm, re-edit, or back out.
                reloaded_raw = json.loads(tags_path.read_text(encoding="utf-8"))
                _preview_diff(current, reloaded_raw, group)
                while True:
                    dec = tui.confirm_action("Keep these edits?", _EDITALL_ACTIONS)
                    if dec == "keep":
                        # Guard: the file may have become invalid after a failed reedit — re-loop
                        # rather than crash so the user can fix it.
                        try:
                            return _done(load_vocab(path=tags_path), group, pad)
                        except Exception as exc:
                            print(f"  invalid tags.json: {exc} — re-edit")
                            continue
                    if dec == "reedit":
                        _editor(tags_path)
                        try:
                            load_vocab(path=tags_path)  # validate — raises on malformed JSON
                        except Exception as exc:
                            print(f"  invalid tags.json: {exc} — re-edit")
                        # Show updated preview, loop back to the editall confirm menu.
                        try:
                            reloaded_raw = json.loads(tags_path.read_text(encoding="utf-8"))
                            _preview_diff(current, reloaded_raw, group)
                        except Exception:
                            pass
                        continue
                    # back / None -> return to top menu
                    break
                continue

            if mode == "inline":
                if not _edit_loop(proposed, group, current):
                    continue                           # backed out → top menu
            elif mode == "editor_draft":
                pp = _proposed_path(env)
                pp.write_text(json.dumps(proposed, ensure_ascii=False, indent=2), encoding="utf-8")
                _editor(pp)
                try:
                    proposed = json.loads(pp.read_text(encoding="utf-8"))
                except (ValueError, json.JSONDecodeError) as exc:
                    print(f"  invalid proposed JSON: {exc} — discarding that edit")
                    continue

            # All modes (accept, inline-done, editor_draft) fall through to preview+confirm.
            merged = _preview_diff(current, proposed, group)

            while True:                                # preview menu
                dec = _preview_menu()
                if dec == "confirm":
                    lock_vocab(group, proposed, path=tags_path)
                    return _done(load_vocab(path=tags_path), group, pad)
                if dec in (None, "back"):
                    break                              # back to top menu
                if dec == "abort":
                    if _confirm_discard(group):
                        raise SystemExit(f"calibrate gate: aborted for group {group!r} — nothing locked")
                    break                              # discard declined → back to top menu


_EDITALL_ACTIONS = [
    ("Keep these edits", "keep", ""),
    ("Re-edit", "reedit", ""),
    ("← Back to menu", "back", ""),
]
