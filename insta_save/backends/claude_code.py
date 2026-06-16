# insta_save/backends/claude_code.py
"""claude-code enrich backend — agent-filled session backend (D5).
Prompt/budget assembly now lives in backends.prompt; re-exported here for the
existing call sites and tests."""

from insta_save.backends.base import Budgets, FillResult, parse_results  # noqa: F401  (re-export)
from insta_save.backends.prompt import (  # noqa: F401  (re-export)
    PROMPT_VERSION, PER_SLIDE_IMAGE_TOKENS, image_token_estimate,
    _vocab_block, _header_lines, _item_block, build_prompt, header_len, item_len)

NAME = "claude-code"
AUTOMATED = False
VISION_CAPABLE = True


def batch_budgets(run_cfg) -> Budgets:
    return Budgets(char_budget=run_cfg.char_budget, max_items=run_cfg.max_items,
                   image_token_budget=run_cfg.image_token_budget)


def fill(env, run_cfg, enrich_dir) -> FillResult:
    """Agent-filled: a Claude session (or fill-subagent) reads prompt.txt + slide
    images and writes results.json. No in-process work."""
    return FillResult(external=True)
