import pytest
from insta_save.stages import extract


def test_shortcode_from_link():
    assert extract._shortcode("https://www.instagram.com/reel/AbC-1/") == "AbC-1"
    assert extract._shortcode("https://www.instagram.com/p/XyZ/") == "XyZ"
    assert extract._shortcode("nope") is None


def test_run_item_reel_dispatch(monkeypatch):
    monkeypatch.setattr(extract, "extract_transcript",
                        lambda **k: {"transcript": "hello world here", "transcript_available": True,
                                     "transcript_language": "ta"})
    monkeypatch.setattr(extract, "extract_ocr_frames",
                        lambda **k: {"text": "ON SCREEN", "confidence": 0.9})
    written = {}
    monkeypatch.setattr(extract, "write_extraction", lambda env, pid, res: written.update(res))
    env = _env()
    item = {"page_id": "p", "source_id": "S", "ig_link": "https://www.instagram.com/reel/ab/",
            "type": "Reel", "collections": []}
    result = extract.run_extract_item(env, _run(), _holder_raises(), item)
    assert result == "extracted"
    assert written["transcript"] == "hello world here"
    assert written["ocr_text"] == "ON SCREEN"
    assert written["extract_version"] == "v2.0-base-tuned"
    assert written["ocr_frames"] == {"text": "ON SCREEN", "confidence": 0.9}
    assert written["transcript_language"] == "ta"


def test_run_item_content_guard_stays_queued(monkeypatch):
    # Reel with no transcript/OCR now advances to Extracted for caption-only enrich
    monkeypatch.setattr(extract, "extract_transcript",
                        lambda **k: {"transcript": None, "transcript_available": False})
    monkeypatch.setattr(extract, "extract_ocr_frames",
                        lambda **k: {"text": "", "confidence": None})
    writes = []
    monkeypatch.setattr(extract, "write_extraction", lambda env, pid, res: writes.append(pid))
    item = {"page_id": "p", "source_id": "S", "ig_link": "https://www.instagram.com/reel/ab/",
            "type": "Reel", "collections": []}
    assert extract.run_extract_item(_env(), _run(), _holder_raises(), item) == "caption_only"
    assert writes == ["p"]


def test_no_content_reel_writes_extracted_for_caption_enrich(monkeypatch, tmp_path):
    """A Reel with no transcript/OCR advances to Extracted (enriches on caption), not stuck Queued."""
    writes, fails = [], []
    monkeypatch.setattr(extract, "extract_transcript",
                        lambda **k: {"transcript": None, "transcript_language": None})
    monkeypatch.setattr(extract, "extract_ocr_frames", lambda **k: {"text": "", "ocr_frames": {}})
    monkeypatch.setattr(extract, "write_extraction", lambda env, pid, results: writes.append(pid))
    monkeypatch.setattr(extract, "mark_failed", lambda env, pid, notes: fails.append(pid))

    item = {"ig_link": "https://x/reel/AB/", "page_id": "p1", "type": "Reel", "source_id": "AB"}
    result = extract.run_extract_item(_env(), _run(), _holder_raises(), item)
    assert result == "caption_only"
    assert writes == ["p1"]
    assert fails == []


def test_no_content_carousel_marks_failed(monkeypatch, tmp_path):
    """A Carousel with no slides is marked Failed (recoverable), not left Queued."""
    writes, fails = [], []
    monkeypatch.setattr(extract, "extract_carousel", lambda **k: [])
    monkeypatch.setattr(extract, "write_extraction", lambda env, pid, results: writes.append(pid))
    monkeypatch.setattr(extract, "mark_failed", lambda env, pid, notes: fails.append((pid, notes)))

    class _Browser:
        def context(self): return None

    item = {"ig_link": "https://x/p/CD/", "page_id": "p2", "type": "Carousel", "source_id": "CD"}
    result = extract.run_extract_item(_env(), _run(), _Browser(), item)
    assert result == "no_slides"
    assert fails and fails[0][0] == "p2"
    assert writes == []


def test_run_item_carousel_uses_browser(monkeypatch):
    slides = [{"slide": 1, "text": "A", "ocr_confidence": 0.9, "image": "slides/ab/slide1.jpg"}]
    monkeypatch.setattr(extract, "extract_carousel", lambda **k: slides)
    written = {}
    monkeypatch.setattr(extract, "write_extraction", lambda env, pid, res: written.update(res))
    item = {"page_id": "p", "source_id": "S", "ig_link": "https://www.instagram.com/p/ab/",
            "type": "Carousel", "collections": []}
    holder = _holder_returns("CTX")
    assert extract.run_extract_item(_env(), _run(), holder, item) == "extracted"
    assert written["carousel_slides"] == slides
    assert holder.opened is True


def test_run_item_post_uses_browser(monkeypatch):
    slides = [{"slide": 1, "text": "P", "ocr_confidence": 0.8, "image": "slides/ab/slide1.jpg"}]
    monkeypatch.setattr(extract, "extract_post", lambda **k: slides)
    written = {}
    monkeypatch.setattr(extract, "write_extraction", lambda env, pid, res: written.update(res))
    item = {"page_id": "p", "source_id": "S", "ig_link": "https://www.instagram.com/p/ab/",
            "type": "Post", "collections": []}
    holder = _holder_returns("CTX")
    assert extract.run_extract_item(_env(), _run(), holder, item) == "extracted"
    assert written["carousel_slides"] == slides
    assert holder.opened is True


def test_run_item_unknown_type_no_content(monkeypatch):
    monkeypatch.setattr(extract, "write_extraction",
                        lambda *a: pytest.fail("must not write on unknown type"))
    fails = []
    monkeypatch.setattr(extract, "mark_failed", lambda env, pid, notes: fails.append(pid))
    item = {"page_id": "p", "source_id": "S", "ig_link": "https://www.instagram.com/p/ab/",
            "type": "Unknown", "collections": []}
    # _holder_raises() asserts the browser is never opened for an unknown type.
    # Unknown type produces no content → falls through to the Carousel/Post no-slides branch.
    assert extract.run_extract_item(_env(), _run(), _holder_raises(), item) == "no_slides"
    assert fails == ["p"]


# --- tiny fakes ---
def _env():
    from insta_save.config.env import EnvConfig
    return EnvConfig(notion_token="t", notion_database_id="d", tmp_dir="tmp",
                     extract_version="v2.0-base-tuned", enrich_version="v2.0-enrich",
                     notion_write_delay=0.0,
                     extract_delay_min=0.0, extract_delay_max=0.0,
                     display_mode="none", cookies_file="session_cookies.json")

def _run():
    from insta_save.config.run import ExtractConfig
    return ExtractConfig(transcript_model="base", transcript_vad=True, ocr_mode="rapidocr")

class _Holder:
    def __init__(self, ctx=None): self._ctx = ctx; self.opened = False
    def context(self):
        self.opened = True
        if self._ctx is None: raise AssertionError("browser opened for a non-browser type")
        return self._ctx

def _holder_raises(): return _Holder(None)
def _holder_returns(ctx): return _Holder(ctx)


def test_lazy_browser_close_swallows_teardown_error():
    """A browser.close() failure during interrupt teardown must not propagate (it masked
    the clean RunStopped exit with a raw traceback)."""
    from insta_save.stages.extract import _LazyBrowser

    lb = _LazyBrowser(playwright=None, env=None, headless=True)

    class _B:
        def close(self): raise RuntimeError("Connection closed while reading from the driver")

    lb._browser = _B()
    lb.close()  # must NOT raise
