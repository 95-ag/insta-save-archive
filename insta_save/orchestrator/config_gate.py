"""Interactive run-config gate for the one-call orchestrator (first-time).

Seeds config/run.json from a claude-p default if absent, then lets the user set
backend/model/effort inline (or edit the whole file in $EDITOR) and confirm before
the long run. Mirrors the collection select gate (inline/editor via --select-mode)
and the calibrate gate (accept/edit/abort). Incremental mode never calls this.
Input is injected for testability."""
import json
import os
import subprocess
from pathlib import Path

from insta_save.config.run import VALID_BACKENDS, load_run_config, _DEFAULT_RUN

_VALID_EFFORTS = {"low", "medium", "high"}

DEFAULT_RUN_TEMPLATE = {
    "mode": "first-time",
    "enrich": {"backend": "claude-p", "model": "claude-sonnet", "effort": "medium",
               "api_mode": "sync"},
    "extract": {"transcript": {"model": "base", "vad": True}, "ocr": {"mode": "rapidocr"}},
    "batch": {"max_items": 15, "max_char_budget": 80000, "max_image_tokens": 120000},
    "guardrails": {"max_items_per_run": None, "max_spend_usd": None},
    "deterministic": {"title_mode": "template"},
    "output_language": "english",
}


def ensure_run_json(path=_DEFAULT_RUN) -> None:
    """Write the claude-p default template if run.json is absent. No-op otherwise."""
    p = Path(path)
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(DEFAULT_RUN_TEMPLATE, ensure_ascii=False, indent=2), encoding="utf-8")


def _prompt_choice(prompt_input, label, current, valid):
    """Prompt until the answer (or the kept current) is in `valid`. Blank keeps current."""
    while True:
        ans = prompt_input(f"  {label} [{current}] ({'/'.join(sorted(valid))}): ").strip()
        val = ans or current
        if val in valid:
            return val
        print(f"  invalid {label} {val!r}")


def _inline_edit(path, run_cfg, prompt_input) -> None:
    """Prompt backend/model/effort (blank keeps current); write them into run.json,
    preserving all other keys."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    enrich = data.setdefault("enrich", {})
    enrich["backend"] = _prompt_choice(prompt_input, "backend",
                                       enrich.get("backend", run_cfg.enrich.backend), VALID_BACKENDS)
    cur_model = enrich.get("model", run_cfg.enrich.model)
    enrich["model"] = prompt_input(f"  model [{cur_model}]: ").strip() or cur_model
    enrich["effort"] = _prompt_choice(prompt_input, "effort",
                                      enrich.get("effort", run_cfg.enrich.effort), _VALID_EFFORTS)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _editor_edit(path) -> None:
    subprocess.run([os.environ.get("EDITOR", "nano"), str(path)], check=False)


def _show(run_cfg) -> None:
    e = run_cfg.enrich
    print(
        f"\nRun config:\n"
        f"  backend: {e.backend}\n  model: {e.model}\n  effort: {e.effort}\n"
        f"  api_mode: {e.api_mode}\n  output_language: {run_cfg.output_language}\n"
        f"  title_mode: {run_cfg.deterministic_title_mode}\n"
        f"  max_items: {run_cfg.max_items}  char_budget: {run_cfg.char_budget}  "
        f"image_token_budget: {run_cfg.image_token_budget}"
    )


def run_config_gate(run_cfg, *, path=_DEFAULT_RUN, select_mode="inline", prompt_input=input):
    """Inline picker (backend/model/effort) or $EDITOR on run.json, then a confirm loop.
    Returns the reloaded RunConfig. Raises SystemExit on abort."""
    while True:
        if select_mode == "editor":
            _editor_edit(path)
        else:
            _inline_edit(path, run_cfg, prompt_input)
        try:
            run_cfg = load_run_config(path)   # reload + validate (loader's _require checks)
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"  invalid run.json: {exc} — re-edit")
            continue
        _show(run_cfg)
        choice = prompt_input("Proceed with this run config? [y / edit / abort]: ").strip().lower()
        if choice == "y":
            return run_cfg
        if choice == "abort":
            raise SystemExit("run-config gate: aborted — nothing ran")
        # "edit" (or anything else) → loop and re-edit
