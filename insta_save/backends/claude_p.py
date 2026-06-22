# insta_save/backends/claude_p.py
"""Headless `claude -p` enrich backend (AUTOMATED). Uses Claude Max via the claude CLI —
no API key. fill() reads tmp/enrich/{batch.json,prompt.txt}, runs `claude -p --output-format
json` with the prompt on STDIN (enrich prompts exceed argv limits), parses the envelope's
`result` text into the results array, writes results.json. Mirrors api_anthropic's fill shape.

Spike-confirmed (2026-06-18): envelope text key is `result`; no fences in practice; claude -p
reads slide images by file path so the vision lane works with the same IMAGES:-path prompt
contract as claude-code -> VISION_CAPABLE=True. fill is lane-agnostic (prompt.txt carries the
image paths); claude -p must run with cwd=repo root so repo-relative image paths resolve."""
import json
import logging
import subprocess
from pathlib import Path

from insta_save.backends.base import (Budgets, FillResult, parse_results_array,
                                       parse_results_object, normalize_results)

log = logging.getLogger(__name__)

NAME = "claude-p"
AUTOMATED = True
VISION_CAPABLE = True
_TIMEOUT_S = 600
_INLINE_OVERRIDE = ("\n\nIMPORTANT: Do NOT write any files. Return ONLY the JSON result as "
                    "your reply — no prose, no explanation, no markdown code fences.")


def batch_budgets(run_cfg) -> Budgets:
    return Budgets(char_budget=run_cfg.char_budget, max_items=run_cfg.max_items,
                   image_token_budget=run_cfg.image_token_budget)


def _cli_model(model: str) -> str:
    """Map the run-config model alias to a claude CLI --model value (sonnet/opus/haiku).
    run.json uses 'claude-sonnet'; the CLI wants 'sonnet'. Strip the 'claude-' prefix."""
    return model.removeprefix("claude-")


def _run_claude_p(prompt: str, model: str) -> str:
    """Run the claude CLI headlessly; return the assistant's final text (the JSON array).
    Prompt goes on stdin. `--model` controls cost (default fast/Opus is ~5x sonnet).
    Raises RuntimeError on non-zero exit or an error envelope."""
    proc = subprocess.run(
        ["claude", "-p", "--model", _cli_model(model), "--output-format", "json"],
        input=prompt + _INLINE_OVERRIDE, capture_output=True, text=True, timeout=_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p exited {proc.returncode}: {proc.stderr[:500]}")
    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude -p error envelope: {str(envelope)[:500]}")
    text = envelope["result"]
    # spike showed no fences in practice; strip defensively (harmless if absent)
    return text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()


def propose_vocab(prompt: str, model: str) -> dict:
    """Draft a calibrate vocab (content_type/groups/cross_group dict) from the calibrate
    prompt via `claude -p`. The human reviews/locks it — this only produces the draft.
    Uses parse_results_object (not a bare json.loads): a real `claude -p` reply often wraps
    the object in prose/fences (e.g. 'here's the proposal: ```json …```'), so robust
    extraction is required. Raises ValueError if no JSON object can be parsed."""
    text = _run_claude_p(prompt, model)
    return parse_results_object(text)


def fill(env, run_cfg, enrich_dir) -> FillResult:
    d = Path(enrich_dir)
    batch = json.loads((d / "batch.json").read_text(encoding="utf-8"))
    items = batch["items"]
    prompt = (d / "prompt.txt").read_text(encoding="utf-8")
    text = _run_claude_p(prompt, run_cfg.enrich.model)
    results = normalize_results(parse_results_array(text), items)
    (d / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    log.info("claude-p fill: %d filled, %d failed", len(results), max(len(items) - len(results), 0))
    return FillResult(filled=len(results), failed=max(len(items) - len(results), 0))
