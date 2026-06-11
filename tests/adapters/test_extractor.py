from insta_save.adapters.instagram.extractor import (
    _author_from_ytdlp_meta,
    _canonical_url,
    _extract_author,
    _extract_caption,
    _iso_date,
)


class _Link:
    def __init__(self, href, text): self._href, self._text = href, text
    def get_attribute(self, _): return self._href
    def inner_text(self): return self._text


class _Loc:
    def __init__(self, links): self._links = links
    def all(self): return list(self._links)


class _Page:
    def __init__(self, links): self._links = links
    def locator(self, _sel): return _Loc(self._links)


def test_browser_author_is_handle_from_href_not_display_name():
    page = _Page([_Link("/nav/", ""),
                  _Link("/natgeo/", "National Geographic")])
    assert _extract_author(page) == "natgeo"


def test_browser_author_skips_nav_hrefs():
    page = _Page([_Link("/explore/", "Explore"), _Link("/jane.doe/", "Jane")])
    assert _extract_author(page) == "jane.doe"


def test_ytdlp_author_prefers_handle_field():
    meta = {"uploader": "National Geographic", "uploader_id": "natgeo", "channel": "Nat Geo"}
    assert _author_from_ytdlp_meta(meta) == "natgeo"


def test_ytdlp_author_strips_at_and_handles_missing():
    assert _author_from_ytdlp_meta({"uploader_id": "@dev"}) == "dev"
    assert _author_from_ytdlp_meta({}) is None


def test_ytdlp_author_coerces_numeric_id():
    assert _author_from_ytdlp_meta({"uploader_id": 12345}) == "12345"


def test_canonical_url_strips_query_on_post():
    assert _canonical_url("/reel/CODE/?x=1") == "https://www.instagram.com/reel/CODE/"


def test_canonical_url_passthrough_for_non_post():
    assert _canonical_url("not-a-post") == "not-a-post"


def test_iso_date_from_epoch_timestamp():
    # 2026-01-01T00:00:00Z = 1767225600
    assert _iso_date({"timestamp": 1767225600}) == "2026-01-01T00:00:00.000Z"


def test_iso_date_from_upload_date_and_empty():
    assert _iso_date({"upload_date": "20260101"}) == "2026-01-01"
    assert _iso_date({}) is None


class _Span:
    def __init__(self, text): self._text = text
    def inner_text(self): return self._text


class _CapLoc:
    def __init__(self, spans): self._spans = spans
    def all(self): return list(self._spans)


class _CapPage:
    def __init__(self, texts): self._spans = [_Span(t) for t in texts]
    def locator(self, _sel): return _CapLoc(self._spans)


def test_caption_strips_xa0_structured_prefix():
    page = _CapPage(["author\n\xa0\n2d\nthe real caption"])
    assert _extract_caption(page, "author") == "the real caption"


def test_caption_strips_author_first_line_prefix():
    page = _CapPage(["author\nthe caption"])
    assert _extract_caption(page, "author") == "the caption"


def test_caption_passthrough_when_no_prefix():
    page = _CapPage(["just a plain caption"])
    assert _extract_caption(page, "author") == "just a plain caption"


def test_caption_longest_span_wins():
    page = _CapPage(["short", "this is the much longer caption span that should win"])
    assert _extract_caption(page, None) == "this is the much longer caption span that should win"
