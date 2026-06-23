# insta_save/backends/sanitize.py
"""Source-grounded scrub of fabricated specifics in enrich output (data integrity).

claude-p occasionally emits URLs or version numbers absent from the source content
(caption/transcript/OCR) — e.g. a guessed GitHub repo URL or an invented 'v2.1.73'.
scrub_fabricated removes those tokens (keeping surrounding words / tool names) so no
invented specific is written to Notion. A URL is judged by HOST presence in the source
(a deep path on an in-source host is kept — host-level check, intentional). @handles are
left intact: creator handles are legit metadata the source text does not always contain."""
import re

_URL_RE = re.compile(
    r"https?://[^\s)\]\"'>]+"
    r"|\b[\w.-]+\.(?:com|io|ai|gov|org|net|dev|co|app|xyz|video)\b[^\s)\]\"'>]*",
    re.IGNORECASE)
_VERSION_RE = re.compile(r"\bv\d+(?:\.\d+)+(?:\.x)?\+?\b|\b\d+\.\d+\.\d+\b", re.IGNORECASE)


def _host(url: str) -> str:
    return re.sub(r"^https?://", "", url).split("/")[0].lower()


def scrub_fabricated(text, source_text):
    """Return (cleaned_text, removed). Removes URLs whose host is absent from source_text
    and version tokens absent from it; deletes the token in place and tidies artifacts
    (empty parens, doubled spaces, space-before-punctuation). None/empty text passes through."""
    if not text:
        return text, []
    src = (source_text or "").lower()
    removed = []

    def _url_sub(m):
        tok = m.group(0).rstrip(".,);:*`\"'")
        if tok.lower() in src or _host(tok) in src:
            return m.group(0)
        removed.append(tok)
        return ""

    def _ver_sub(m):
        tok = m.group(0)
        norm = tok.lower().rstrip("+")
        norm = norm[:-2] if norm.endswith(".x") else norm   # v2.1.x -> v2.1 for source check
        if norm in src or tok.lower() in src:
            return m.group(0)
        removed.append(tok)
        return ""

    cleaned = _URL_RE.sub(_url_sub, text)
    cleaned = _VERSION_RE.sub(_ver_sub, cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)          # empty parens left by a removed URL
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)        # collapse doubled spaces
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)    # drop space before punctuation
    return cleaned, removed
