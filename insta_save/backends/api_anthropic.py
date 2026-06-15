# insta_save/backends/api_anthropic.py
"""api enrich backend — automated, in-process via the Anthropic SDK (D5).

AUTOMATED + VISION_CAPABLE: fill() calls the Claude Messages API directly and
writes results.json itself (no agent/subagent). The vision lane attaches each
carousel/post slide as a base64 image block so the model actually SEES the
images — unlike claude-code, which lists paths for a subagent to Read.

Two modes (run_cfg.enrich.api_mode):
  - "sync" (default): one Messages.create per batch. The batch is already
    char/image-budget-bounded by the shared prepare step.
  - "batches": one Message Batches request carrying the same whole-batch prompt,
    polled to completion. Per-item parallel batching is a future optimization.

We deliberately do NOT use output_config.format structured outputs (uncertain
typing in SDK 0.105.2). Instead the prompt instructs the model to emit ONLY a
JSON array; _parse_json_array strips any ```json fence and json.loads it.

Identity (page_id/source_id) is normalized from the batch where matchable — the
shared apply() validates content_type/topics against the locked vocab, so fill
does not validate vocab. The model id (run_cfg.enrich.model) must be a Claude id
(a config concern, not enforced here)."""

import base64
import json
import logging
from pathlib import Path

from insta_save.backends.base import Budgets, FillResult

log = logging.getLogger(__name__)

NAME = "api"; AUTOMATED = True; VISION_CAPABLE = True

MAX_TOKENS = 16000

_MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp"}


def _messages(env, run_cfg):
    """Return the Anthropic client's messages resource. Monkeypatched in tests."""
    import anthropic
    return anthropic.Anthropic(api_key=env.anthropic_api_key).messages


def batch_budgets(run_cfg) -> Budgets:
    return Budgets(char_budget=run_cfg.char_budget, max_items=run_cfg.max_items,
                   image_token_budget=run_cfg.image_token_budget)


def _parse_json_array(text: str) -> list:
    """Strip a leading/trailing ```json fence if present, then json.loads.
    Raise ValueError if the result is not a list."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    data = json.loads(stripped)
    if not isinstance(data, list):
        raise ValueError(f"expected a JSON array, got {type(data).__name__}")
    return data


def _image_block(path) -> dict | None:
    """Read an image file and return a base64 image content block. Returns None
    (logging a skip) if the file is missing or the extension is unsupported."""
    p = Path(path)
    media_type = _MEDIA_TYPES.get(p.suffix.lower())
    if media_type is None:
        log.warning("api fill: skipping image with unsupported extension %s", path)
        return None
    try:
        raw = p.read_bytes()
    except OSError as exc:
        log.warning("api fill: skipping missing image %s — %s", path, exc)
        return None
    return {"type": "image",
            "source": {"type": "base64", "media_type": media_type,
                       "data": base64.standard_b64encode(raw).decode("ascii")}}


def _image_blocks(items) -> list[dict]:
    """Image content blocks for every slide across all items (vision lane only;
    text-lane items have no slide_images)."""
    blocks = []
    for item in items:
        for path in item.get("slide_images") or []:
            block = _image_block(path)
            if block is not None:
                blocks.append(block)
    return blocks


def _create_kwargs(run_cfg, content) -> dict:
    """Shared Messages.create kwargs for both the sync and batches paths."""
    return {
        "model": run_cfg.enrich.model,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": content}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": run_cfg.enrich.effort},
    }


def _first_text(message) -> str:
    for block in message.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise ValueError("no text block in response")


def fill(env, run_cfg, enrich_dir) -> FillResult:
    d = Path(enrich_dir)
    batch = json.loads((d / "batch.json").read_text(encoding="utf-8"))
    items = batch["items"]
    prompt = (d / "prompt.txt").read_text(encoding="utf-8")

    content = [{"type": "text", "text": prompt}] + _image_blocks(items)
    kwargs = _create_kwargs(run_cfg, content)

    if run_cfg.enrich.api_mode == "batches":
        text = _fill_batches(env, run_cfg, kwargs)
    else:
        text = _fill_sync(env, run_cfg, kwargs)

    parsed = _parse_json_array(text)
    (d / "results.json").write_text(json.dumps(parsed, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    filled = len(parsed)
    failed = max(len(items) - filled, 0)
    log.info("api fill (%s): %d filled, %d failed", run_cfg.enrich.api_mode, filled, failed)
    return FillResult(filled=filled, failed=failed)


def _fill_sync(env, run_cfg, kwargs) -> str:
    resp = _messages(env, run_cfg).create(**kwargs)
    return _first_text(resp)


def _fill_batches(env, run_cfg, kwargs) -> str:
    # NOTE: one whole-batch request per fill. Per-item parallel batching (a
    # Request per item) is a future optimization — not built here.
    import time

    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    messages = _messages(env, run_cfg)
    batch = messages.batches.create(requests=[
        Request(custom_id="enrich-batch",
                params=MessageCreateParamsNonStreaming(**kwargs))])
    while messages.batches.retrieve(batch.id).processing_status != "ended":
        time.sleep(5)
    for result in messages.batches.results(batch.id):
        return _first_text(result.result.message)
    raise ValueError("batch returned no results")
