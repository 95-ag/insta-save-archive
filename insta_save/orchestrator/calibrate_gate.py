"""Interactive per-group calibrate gate for the one-call orchestrator (D18).

Samples the group, gets a DRAFT vocab from the backend (if it supports propose_vocab),
lets the human accept / edit / abort, then locks it into config/tags.json. The human
lock is preserved — the backend only drafts. Input is injected for testability.
"""
import json
import os
import subprocess
from pathlib import Path

from insta_save.config.tags import lock_vocab, load_vocab
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


def run_calibrate_gate(env, run_cfg, *, collections_cfg, backend, group, prompt_input=input):
    """Sample -> draft -> human lock for `group`. Returns the reloaded Vocab.

    Raises SystemExit if the human aborts or there is nothing to sample.
    """
    n = _sample(env, group, collections_cfg)
    if n == 0:
        raise SystemExit(f"calibrate gate: no Extracted items to sample for group {group!r}")

    prompt = _calibrate_prompt_path(env).read_text(encoding="utf-8")
    proposed_path = _proposed_path(env)

    if hasattr(backend, "propose_vocab"):
        proposed = backend.propose_vocab(prompt, run_cfg.enrich.model)
        proposed_path.write_text(
            json.dumps(proposed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nProposed vocab for {group}:\n{json.dumps(proposed, ensure_ascii=False, indent=2)}")
    else:
        print(
            f"\nBackend has no propose_vocab. Read {_calibrate_prompt_path(env)}, "
            f"write {proposed_path} (calibrate shape: content_type/groups/cross_group), "
            f"then return here."
        )

    while True:
        choice = prompt_input(f"Lock vocab for {group}? [y = accept / edit / abort]: ").strip().lower()
        if choice == "y":
            break
        if choice == "edit":
            subprocess.run(
                [os.environ.get("EDITOR", "nano"), str(proposed_path)], check=False
            )
            continue
        if choice == "abort":
            raise SystemExit(f"calibrate gate: aborted for group {group!r} — nothing locked")
        print("Please answer 'y', 'edit', or 'abort'.")

    if not proposed_path.exists():
        # Manual-draft path (backend has no propose_vocab): the human accepted without
        # writing the file. Fail with a clear instruction instead of a raw read error.
        raise SystemExit(
            f"calibrate gate: no proposed vocab at {proposed_path} for group {group!r} — "
            f"write it (calibrate shape: content_type/groups/cross_group) before accepting."
        )

    proposed = json.loads(proposed_path.read_text(encoding="utf-8"))
    lock_vocab(group, proposed, path=_tags_path())
    return load_vocab(path=_tags_path())
