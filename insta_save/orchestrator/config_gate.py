"""Interactive run-config gate for the one-call orchestrator (first-time).

Seeds config/run.json from a claude-p default if absent, then lets the user set
backend/model/effort inline via keyboard-select (or edit the whole file in $EDITOR)
and confirm before the long run. Mirrors the collection select gate (inline/editor
via --select-mode) and the calibrate gate (accept/edit/abort). Incremental mode
never calls this."""
import json
import os
import subprocess
from pathlib import Path

from insta_save.config.run import load_run_config, _DEFAULT_RUN
from insta_save.helpers import tui
from insta_save.helpers.observability import stage_section, RULE_TOP

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

_BACKEND_CHOICES = [
    ("claude-p  (Claude Max, no API key)", "claude-p", ""),
    ("api  (Anthropic API, needs ANTHROPIC_API_KEY)", "api", ""),
    ("local  (Ollama, title-grade)", "local", ""),
    ("claude-code  (agent-filled session loop)", "claude-code", ""),
    ("cowork  (self-paced loop)", "cowork", ""),
]
_EFFORT_CHOICES = [("low  (smaller context)", "low", ""),
                   ("medium  (balanced, default)", "medium", ""),
                   ("high  (largest context)", "high", "")]
_API_MODE_CHOICES = [("sync  (one request per batch, default)", "sync", ""),
                     ("batches  (Message Batches API)", "batches", "")]
_MODEL_CHOICES = {
    "local": [("qwen2.5:7b  (default local model)", "qwen2.5:7b", ""),
              ("qwen2.5:3b  (smaller/faster)", "qwen2.5:3b", "")],
}
_DEFAULT_MODEL_CHOICES = [("claude-sonnet  (balanced, default)", "claude-sonnet", ""),
                          ("claude-opus  (highest quality)", "claude-opus", ""),
                          ("claude-haiku  (fastest/cheapest)", "claude-haiku", "")]
_LANG_CHOICES = [("english", "english", ""), ("spanish", "spanish", ""),
                 ("french", "french", ""), ("german", "german", ""), ("hindi", "hindi", "")]


_KEEP_CURRENT = "keep_current"


def ensure_run_json(path=_DEFAULT_RUN) -> None:
    """Write the claude-p default template if run.json is absent. No-op otherwise."""
    p = Path(path)
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(DEFAULT_RUN_TEMPLATE, ensure_ascii=False, indent=2), encoding="utf-8")


def _inline_pick(path, run_cfg) -> None:
    """Keyboard-select backend/model/effort (and api_mode for api backend); write into run.json,
    preserving all other keys."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    enrich = data.setdefault("enrich", {})
    enrich["backend"] = tui.select("backend", _BACKEND_CHOICES,
                                   default=enrich.get("backend", run_cfg.enrich.backend))
    models = _MODEL_CHOICES.get(enrich["backend"], _DEFAULT_MODEL_CHOICES)
    enrich["model"] = tui.select_or_other("model", models,
                                          default=enrich.get("model", run_cfg.enrich.model))
    enrich["effort"] = tui.select("effort", _EFFORT_CHOICES,
                                  default=enrich.get("effort", run_cfg.enrich.effort))
    if enrich["backend"] == "api":
        enrich["api_mode"] = tui.select("api_mode", _API_MODE_CHOICES,
                                        default=enrich.get("api_mode", "sync"))
    data["output_language"] = tui.select_or_other("output language", _LANG_CHOICES,
                                                   default=data.get("output_language", "english"))
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


def run_config_gate(run_cfg, *, path=_DEFAULT_RUN, select_mode="inline"):
    """Mode-prompt (keep-current/inline/editor) -> edit -> 4-way confirm. Returns RunConfig.
    Raises SystemExit on abort (or Ctrl-C → tui returns None).

    Default (Enter) is keep-current: skips all field prompts and returns the loaded
    run_cfg unchanged. Inline or editor choices enter the edit→confirm loop as before.

    The stage_section frame opens BEFORE the mode prompt so it encloses the entire
    gate interaction — including the keep-current early return."""
    with stage_section("run config", width=RULE_TOP):
        mode = tui.select("Set run config via", [
            ("Use existing config (run.json as-is)", _KEEP_CURRENT, "keep current settings, skip prompts"),
            ("Inline picker", "inline", "pick fields here"),
            ("Edit in $EDITOR", "editor", "edit the whole run.json"),
        ], default=("editor" if select_mode == "editor" else _KEEP_CURRENT))
        if mode is None:
            raise SystemExit("run-config gate: aborted")
        if mode == _KEEP_CURRENT:
            print("  ✔ kept current config")
            return run_cfg
        while True:
            if mode == "editor":
                _editor_edit(path)
            else:
                _inline_pick(path, run_cfg)
            try:
                run_cfg = load_run_config(path)
            except (ValueError, json.JSONDecodeError) as exc:
                print(f"  invalid run.json: {exc} — re-edit"); mode = "editor"; continue
            _show(run_cfg)
            action = tui.confirm_action("Proceed?", [
                ("Confirm", "proceed", "preflight, then the pipeline"),
                ("Go back", "back", "re-pick the fields inline"),
                ("Edit in $EDITOR", "editor", "edit the whole run.json"),
                ("Abort", "abort", "exit, nothing runs"),
            ])
            if action in (None, "abort"):
                raise SystemExit("run-config gate: aborted")
            if action == "proceed":
                e = run_cfg.enrich
                print(f"  ✔ {e.backend} / {e.model} / {e.effort} · {run_cfg.output_language}")
                return run_cfg
            mode = "inline" if action == "back" else "editor"
