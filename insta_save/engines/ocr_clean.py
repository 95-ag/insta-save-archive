"""Pure OCR-text cleaner — collapse near-duplicate frame-OCR + trim junk lines.

Reels are OCR'd at ~1 fps, so the same on-screen text recurs across frames with per-frame
variation (jitter) plus OCR errors — inflating `ocr_text` with fuzzy repeats that waste the
enrich budget and pollute summaries. This collapses those near-duplicates (content-preserving:
the FIRST occurrence is kept) and drops single-char / pure-symbol noise.

Kept separate from `engines/ocr.py` (which imports RapidOCR) so it can be used anywhere — the
extract path or the enrich-read path — without pulling the OCR runtime. Pure, no I/O.
"""
import re
from difflib import SequenceMatcher

THRESHOLD = 0.8      # similarity at/above which a line is treated as a repeat
WINDOW = 30          # compare only against the last N kept lines (frame locality)
MIN_FUZZY_LEN = 4    # shorter normalized lines use EXACT match only (don't fuzz '01' vs '02')

_SLIDE_MARKER = re.compile(r"^\[Slide \d+\]$")
_NON_ALNUM = re.compile(r"[^a-z0-9]")


def _normalize(line: str) -> str:
    return _NON_ALNUM.sub("", line.lower())


def _is_garbage(line: str) -> bool:
    """Single-char or zero-alphanumeric fragments are OCR noise (e.g. '目', '★', 'M', '::')."""
    return len(line) <= 1 or not any(c.isalnum() for c in line)


def _is_repeat(norm: str, recent_norms: list[str]) -> bool:
    for prev in recent_norms:
        if len(norm) < MIN_FUZZY_LEN or len(prev) < MIN_FUZZY_LEN:
            if norm == prev:
                return True
        elif SequenceMatcher(None, norm, prev).ratio() >= THRESHOLD:
            return True
    return False


def clean_ocr_text(text: str) -> str:
    """Collapse near-duplicate frame-OCR lines and drop junk. Order-preserving; keeps the first
    occurrence of each distinct line. `[Slide N]` markers are always preserved (carousel structure)."""
    if not text:
        return ""
    kept: list[str] = []
    kept_norms: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _SLIDE_MARKER.match(line):
            kept.append(line)          # structural — never trimmed or collapsed
            continue
        if _is_garbage(line):
            continue
        norm = _normalize(line)
        if not norm:
            continue
        if _is_repeat(norm, kept_norms[-WINDOW:]):
            continue
        kept.append(line)
        kept_norms.append(norm)
    return "\n".join(kept)
