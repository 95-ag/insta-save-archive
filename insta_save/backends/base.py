# insta_save/backends/base.py
"""Backend protocol + registry + shared results parsing (D5).

Backends are MODULES (consistent with the existing claude_code style) exposing:
  NAME: str · AUTOMATED: bool · VISION_CAPABLE: bool
  batch_budgets(run_cfg) -> Budgets
  fill(env, run_cfg, enrich_dir) -> FillResult

AUTOMATED True  -> fill() produces results.json in-process (local/api).
AUTOMATED False -> fill() is agent-filled; returns FillResult(external=True) (claude-code/cowork)."""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from insta_save.enrich_schema import RESULT_FIELDS

log = logging.getLogger(__name__)

_NO_DOCUMENT = object()   # sentinel: the file is not a single JSON document (None is valid JSON)


class TerminalBackendError(Exception):
    """A backend failure that retrying within this run cannot fix (Claude-Max usage
    limit, not-logged-in, invalid key). Propagates out of the drain to stop the run
    gracefully with a resume hint — as opposed to a transient malformed-JSON reply,
    which the drain retries."""


# Substrings (case-insensitive) that mark a backend error as TERMINAL for this run.
# Kept deliberately narrow: anything not matched is treated as transient and retried.
# A 429/overloaded/timeout is transient on purpose (retry usually succeeds); only a
# usage-limit or auth failure is hopeless until the user acts.
_TERMINAL_ERROR_MARKERS = (
    "usage limit",
    "not logged in",
    "please run /login",
    "invalid api key",
    "authentication",
    "authenticate",             # "failed to authenticate" / "re-authenticate" (OAuth expiry)
    "oauth",
    "access token has expired",
    "credit balance",
)


def is_terminal_error(exc: BaseException) -> bool:
    """True when exc's message marks a terminal (non-retryable-this-run) backend failure."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _TERMINAL_ERROR_MARKERS)


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
    Otherwise filled/failed count what fill() wrote. unparsed counts reply lines the
    JSON-Lines parser could not read — the drain retries exactly those items instead of
    condemning the whole batch."""
    external: bool = False
    filled: int = 0
    failed: int = 0
    unparsed: int = 0


class Backend(Protocol):
    NAME: str
    AUTOMATED: bool
    VISION_CAPABLE: bool
    def batch_budgets(self, run_cfg) -> Budgets: ...
    def fill(self, env, run_cfg, enrich_dir) -> FillResult: ...


def parse_results_object(text: str) -> dict:
    """Extract a single JSON object from model output, tolerating a ```json fence and/or
    surrounding prose (e.g. a `claude -p` reply that explains before/after the object).
    Used by propose_vocab. Raises ValueError if no JSON object is found or it isn't a dict."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        data = json.loads(stripped, strict=False)  # tolerate raw control chars in model strings
    except json.JSONDecodeError:
        # model wrapped the object in prose — pull out the outermost {...}
        start, end = stripped.find("{"), stripped.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"no JSON object found in model output: {stripped[:200]!r}")
        data = json.loads(stripped[start:end + 1], strict=False)
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
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


def _scan_json_depth(line: str, depth: int, in_string: bool, escaped: bool) -> tuple:
    """Advance a brace/bracket depth scan across one line, ignoring delimiters inside
    strings. Returns the state after the line: (depth, in_string, escaped)."""
    for char in line:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
    return depth, in_string, escaped


_BARE_QUOTE_RE = re.compile(r'(\\.)|"')
_LIST_FIELDS = frozenset({"topics"})


def _repair_unescaped_quotes(chunk: str) -> dict | None:
    """Recover one result object whose string value holds an unescaped double quote.

    Quote boundaries are what the model got wrong, so they are not trusted: each known key
    (RESULT_FIELDS, in contract order) is located and the text between two keys is the value.
    The repair is deliberately all-or-nothing and returns None — leaving the chunk bad, so
    the item is retried — unless every field is found EXACTLY ONCE and in order. A key name
    that also appears inside a value makes the boundary ambiguous, and a wrong value written
    to Notion costs more than a retry. String values must be quote-delimited; `topics` must
    parse as a JSON list on its own."""
    text = chunk.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    markers = []
    for field in RESULT_FIELDS:
        found = list(re.finditer(r'(?:^\{|,)\s*"%s"\s*:\s*' % re.escape(field), text))
        if len(found) != 1:
            return None
        markers.append(found[0])
    if any(a.end() > b.start() for a, b in zip(markers, markers[1:])):
        return None
    ends = [m.start() for m in markers[1:]] + [len(text) - 1]
    result = {}
    for field, marker, end in zip(RESULT_FIELDS, markers, ends):
        raw = text[marker.end() : end].strip()
        try:
            if field in _LIST_FIELDS:
                value = json.loads(raw)
                if not isinstance(value, list):
                    return None
            else:
                if len(raw) < 2 or raw[0] != '"' or raw[-1] != '"':
                    return None
                inner = _BARE_QUOTE_RE.sub(lambda m: m.group(1) or '\\"', raw[1:-1])
                value = json.loads('"' + inner + '"', strict=False)
        except json.JSONDecodeError:
            return None
        result[field] = value
    return result


def _split_swallowed_objects(chunk: str) -> list[str]:
    """An odd quote count flips the depth scan's string state, so the failed chunk may also
    hold the lines that follow it. Cut it at every line that opens a result object."""
    return re.split(r'\n(?=\s*\{\s*"page_id"\s*:)', chunk)


def parse_results_jsonl(text: str) -> tuple[list[dict], list[str]]:
    """Parse JSON-Lines model output (enrich v2.1): one result object per reply line.

    Returns (items, bad_chunks). A value that will not parse is COLLECTED, never raised —
    one unescaped quote must cost its own item only, not the whole batch. Blank lines and
    stray ``` fence lines between values are ignored; a value holding a whole JSON array
    contributes its objects (a model that ignored the line-delimited contract is usable).

    Physical lines are NOT trusted as value boundaries. A summary containing a literal
    newline, or a pretty-printed object, spans several lines, and splitting on them cut the
    value into an unterminated string plus a meaningless remainder — BOTH counted bad, so
    one newline cost two failures and the item had to be retried (measured live: 27 bad
    lines from a 15-item batch). Lines are instead accumulated until the brace/bracket depth
    returns to zero outside a string, and that whole chunk is parsed."""
    items: list[dict] = []
    bad_chunks: list[str] = []
    buffer: list[str] = []
    depth, in_string, escaped = 0, False, False

    def _recover(chunk: str) -> None:
        for piece in _split_swallowed_objects(chunk):
            try:
                json.loads(piece, strict=False)
            except json.JSONDecodeError:
                repaired = _repair_unescaped_quotes(piece)
                if repaired is None:
                    bad_chunks.append(piece)
                else:
                    items.append(repaired)
            else:
                _flush(piece)

    def _flush(chunk: str) -> None:
        try:
            value = json.loads(chunk, strict=False)  # tolerate raw control chars
        except json.JSONDecodeError:
            _recover(chunk)
            return
        if isinstance(value, dict):
            items.append(value)
        elif isinstance(value, list) and all(isinstance(v, dict) for v in value):
            items.extend(value)
        else:
            bad_chunks.append(chunk)

    for raw in text.splitlines():
        if not buffer:
            stripped = raw.strip()
            # Only skip furniture BETWEEN values; inside one, every line is content.
            if not stripped or stripped.startswith("```"):
                continue
        buffer.append(raw)
        depth, in_string, escaped = _scan_json_depth(raw, depth, in_string, escaped)
        if depth <= 0 and not in_string:
            # Rejoin with the newline that split them, which strict=False accepts inside
            # a string and JSON ignores outside one.
            _flush("\n".join(buffer))
            buffer, depth, in_string, escaped = [], 0, False, False

    if buffer:  # unterminated at end of reply — still report it rather than dropping it
        _flush("\n".join(buffer))
    return items, bad_chunks


def parse_results(path) -> list[dict]:
    """Read results.json — a JSON array, a single result object, or JSON Lines (one
    object per line).

    A whole-file JSON document is decided first: that is what every automated backend
    writes, and a document that isn't a list or a single result object must keep raising
    as it always has. A one-object document carrying a page_id is a conformant batch-of-1
    (e.g. an agent-filled backend under the v2.1 contract) and is accepted as a one-item
    list; any other dict is not a result and still raises. JSON Lines is the fallback, for
    an agent-filled backend that followed the v2.1 line-delimited contract. Raises
    ValueError when no shape yields results."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        document = _NO_DOCUMENT
    if document is not _NO_DOCUMENT:
        if isinstance(document, dict) and "page_id" in document:
            return [document]
        if not isinstance(document, list):
            raise ValueError(f"results.json must be a JSON array, got {type(document).__name__}")
        return document
    items, bad_lines = parse_results_jsonl(text)
    if not items:
        raise ValueError(f"results.json is neither a JSON array nor JSON Lines: {text[:200]!r}")
    if bad_lines:
        log.warning("parse_results: %d unparsed line(s) in %s", len(bad_lines), path)
    return items


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
    elif name == "claude-p":
        from insta_save.backends import claude_p as m
    else:
        raise ValueError(f"unknown enrich backend {name!r}")
    return m
