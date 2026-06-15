# insta_save/backends/claude_code.py
"""claude-code enrich backend — agent-filled session backend (D5).
Prompt/budget assembly now lives in backends.prompt; re-exported here for the
existing call sites and tests."""
import json
from pathlib import Path

from insta_save.backends.prompt import (  # noqa: F401  (re-export)
    PROMPT_VERSION, PER_SLIDE_IMAGE_TOKENS, image_token_estimate,
    _vocab_block, _header_lines, _item_block, build_prompt, header_len, item_len)


def parse_results(path) -> list[dict]:
    """Read results.json (a JSON array of result items). Raises ValueError if the
    file isn't a JSON array."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"results.json must be a JSON array, got {type(data).__name__}")
    return data
