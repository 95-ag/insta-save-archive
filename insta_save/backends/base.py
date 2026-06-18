# insta_save/backends/base.py
"""Backend protocol + registry + shared results parsing (D5).

Backends are MODULES (consistent with the existing claude_code style) exposing:
  NAME: str · AUTOMATED: bool · VISION_CAPABLE: bool
  batch_budgets(run_cfg) -> Budgets
  fill(env, run_cfg, enrich_dir) -> FillResult

AUTOMATED True  -> fill() produces results.json in-process (local/api).
AUTOMATED False -> fill() is agent-filled; returns FillResult(external=True) (claude-code/cowork)."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Budgets:
    """Batch-sizing knobs (D7). char_budget/max_items bound the rendered prompt;
    image_token_budget bounds the vision lane. max_items=None means no item cap (local)."""
    char_budget: int
    max_items: int | None
    image_token_budget: int | None


@dataclass(frozen=True)
class FillResult:
    """external=True -> an agent must fill results.json (no in-process work done).
    Otherwise filled/failed count what fill() wrote."""
    external: bool = False
    filled: int = 0
    failed: int = 0


class Backend(Protocol):
    NAME: str
    AUTOMATED: bool
    VISION_CAPABLE: bool
    def batch_budgets(self, run_cfg) -> Budgets: ...
    def fill(self, env, run_cfg, enrich_dir) -> FillResult: ...


def parse_results_array(text: str) -> list:
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


def normalize_results(parsed, batch_items) -> list[dict]:
    """Trust the batch, not the model, for identity. Keep only results whose
    page_id is a real batch item (drops fabricated ids), de-dupe on first
    occurrence, and overwrite source_id from the matching batch item. The
    model's title/summary/externals/content_type/topics are left intact."""
    by_page_id = {item["page_id"]: item for item in batch_items}
    kept, seen = [], set()
    for result in parsed:
        page_id = result.get("page_id")
        if page_id not in by_page_id or page_id in seen:
            continue
        seen.add(page_id)
        result["source_id"] = by_page_id[page_id].get("source_id")
        kept.append(result)
    return kept


def parse_results(path) -> list[dict]:
    """Read results.json (a JSON array). Raises ValueError if not an array."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"results.json must be a JSON array, got {type(data).__name__}")
    return data


def get_backend(name: str):
    """Map run_cfg.enrich.backend -> the backend module. Imports lazily so a missing
    optional dep only fails when that backend is actually selected."""
    if name == "claude-code":
        from insta_save.backends import claude_code as m
    elif name == "cowork":
        from insta_save.backends import cowork as m
    elif name == "local":
        from insta_save.backends import local_ollama as m
    elif name == "api":
        from insta_save.backends import api_anthropic as m
    else:
        raise ValueError(f"unknown enrich backend {name!r}")
    return m
