"""Interactive per-group calibrate vocab editor (D18, D28).

Samples the group, gets a DRAFT vocab from the backend (propose_vocab), then runs an
interactive editor: read-only CONTEXT across axes -> an edit loop (reject a current-group
granular topic / add a topic on any axis) -> a merged PREVIEW (merge_vocab, no write) ->
confirm / go back / edit-current ($EDITOR on the proposal) / edit-all ($EDITOR on
config/tags.json) / abort. Confirm locks via lock_vocab (granular outright, content-type/
cross-group additive); edit-all is the only path that can remove a shared item. Reachable
inline (sequence._run_loop) and standalone (isa run --stage calibrate --group G). The human
lock is preserved — the backend only drafts."""
import json
import os
import subprocess
from pathlib import Path

from insta_save.config.tags import lock_vocab, load_vocab, merge_vocab
from insta_save.helpers import tui
from insta_save.stages import calibrate as _calibrate

_SAMPLE_LIMIT = 20


def _calibrate_prompt_path(env) -> Path:
    return Path(env.tmp_dir) / "calibrate" / "prompt.txt"


def _proposed_path(env) -> Path:
    # Derived from the calibrate prompt dir so the abort test (which monkeypatches
    # _calibrate_prompt_path to a real tmp dir but does NOT monkeypatch _proposed_path)
    # still gets a writable path without needing env.tmp_dir directly.
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
        limit=_SAMPLE_LIMIT,
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
        proposed = backend.propose_vocab(prompt, run_cfg.enrich.model)
    else:
        print(f"Backend has no propose_vocab — starting from an empty draft for {group}. "
              f"Use 'Add a topic' or 'Edit current' to build it.")
        proposed = {}
    proposed.setdefault("content_type", {})
    proposed.setdefault("groups", {}).setdefault(group, {})
    proposed.setdefault("cross_group", {})
    _proposed_path(env).write_text(
        json.dumps(proposed, ensure_ascii=False, indent=2), encoding="utf-8")
    return proposed


def _print_context(proposed, group, current) -> None:
    """Read-only orientation across the three axes."""
    pg = proposed.get("groups", {}).get(group, {})
    print(f"\n=== Calibrate context: {group} ===")
    print(f"Proposed granular for {group}: {', '.join(pg) or '(none)'}")
    others = {g: list(t) for g, t in current.get("groups", {}).items() if g != group}
    if others:
        print("Other groups' granular:")
        for g, topics in others.items():
            print(f"  {g}: {', '.join(topics) or '(none)'}")
    cross = sorted(set(current.get("cross_group", {})) | set(proposed.get("cross_group", {})))
    print(f"Cross-group (current + proposed): {', '.join(cross) or '(none)'}")
    ctypes = sorted(set(current.get("content_type", {})) | set(proposed.get("content_type", {})))
    print(f"Content-types: {', '.join(ctypes) or '(none)'}")


def _reject(proposed, group) -> None:
    """Remove one of the current group's granular topics (cross-group/content-type removal
    is 'Edit all'). No-op when the group has no granular topics."""
    granular = proposed.get("groups", {}).get(group, {})
    if not granular:
        print(f"  {group} has no granular topics to reject "
              f"(remove shared content-type/cross-group items via 'Edit all').")
        return
    choices = [(t, t, granular[t]) for t in granular]
    topic = tui.select(f"Reject which granular topic from {group}?", choices)
    if topic is None:
        return
    proposed["groups"][group].pop(topic, None)
    print(f"  rejected {topic!r}")


_AXES = [
    ("granular (this group)", "granular", "a topic specific to this group"),
    ("content-type (shared)", "content_type", "kind-of-item axis; additive across groups"),
    ("cross-group (shared)", "cross_group", "a theme shared across groups; additive"),
]


def _add(proposed, group) -> None:
    """Add a topic on a chosen axis (granular -> this group; content-type/cross-group -> shared)."""
    axis = tui.select("Add a topic to which axis?", _AXES)
    if axis is None:
        return
    label = (tui.text("  topic label") or "").strip()
    if not label:
        print("  empty label — skipped.")
        return
    definition = (tui.text("  one-line definition") or "").strip()
    if axis == "granular":
        proposed.setdefault("groups", {}).setdefault(group, {})[label] = definition
    else:
        proposed.setdefault(axis, {})[label] = definition
    print(f"  added {label!r} to {axis}")


_EDIT_ACTIONS = [
    ("Reject a granular topic", "reject", "remove one of this group's granular topics"),
    ("Add a topic", "add", "add a topic on any axis"),
    ("Show context again", "context", "reprint the cross-axis context"),
    ("Done — preview", "done", "merge and preview before locking"),
]


def _edit_loop(proposed, group, current) -> None:
    """Mutate `proposed` in place via tui actions until Done. Ctrl-C (None) aborts."""
    while True:
        action = tui.select(f"Edit vocab for {group}", _EDIT_ACTIONS)
        if action is None:
            raise SystemExit(f"calibrate gate: aborted for group {group!r} — nothing locked")
        if action == "done":
            return
        if action == "reject":
            _reject(proposed, group)
        elif action == "add":
            _add(proposed, group)
        elif action == "context":
            _print_context(proposed, group, current)


def run_calibrate_gate(env, run_cfg, *, collections_cfg, backend, group):
    """Sample -> draft -> interactive edit -> merged preview -> lock for `group`.
    Returns the reloaded Vocab. Raises SystemExit on abort or nothing to sample."""
    n = _sample(env, group, collections_cfg)
    if n == 0:
        raise SystemExit(f"calibrate gate: no Extracted items to sample for group {group!r}")

    proposed = _draft(env, run_cfg, backend, group)
    tags_path = _tags_path()
    current = json.loads(tags_path.read_text(encoding="utf-8")) if tags_path.exists() else {}
    _print_context(proposed, group, current)
    _edit_loop(proposed, group, current)

    while True:                                    # preview <-> actions
        merged = merge_vocab(current, group, proposed)
        print(f"\n=== Preview: config/tags.json after locking {group} ===")
        print(json.dumps(merged, ensure_ascii=False, indent=2))
        action = tui.confirm_action("Lock this vocab?", [
            ("Confirm", "confirm", "write it and continue"),
            ("Go back", "back", "return to the edit loop"),
            ("Edit current in $EDITOR", "edit_current", "hand-edit this group's proposal"),
            ("Edit all in $EDITOR", "edit_all", "hand-edit the whole tags.json (can remove shared items)"),
            ("Abort", "abort", "exit, nothing locked"),
        ])
        if action in (None, "abort"):
            raise SystemExit(f"calibrate gate: aborted for group {group!r} — nothing locked")
        if action == "confirm":
            lock_vocab(group, proposed, path=tags_path)
            return load_vocab(path=tags_path)
        if action == "back":
            _edit_loop(proposed, group, current)
            continue
        if action == "edit_current":
            pp = _proposed_path(env)
            pp.write_text(json.dumps(proposed, ensure_ascii=False, indent=2), encoding="utf-8")
            _editor(pp)
            try:
                proposed = json.loads(pp.read_text(encoding="utf-8"))
            except (ValueError, json.JSONDecodeError) as exc:
                print(f"  invalid proposed JSON: {exc} — discarding that edit")
            continue
        if action == "edit_all":
            _editor(tags_path)
            try:
                return load_vocab(path=tags_path)   # the editor IS the write; nothing to lock
            except Exception as exc:
                print(f"  invalid tags.json: {exc} — re-edit")
                continue
