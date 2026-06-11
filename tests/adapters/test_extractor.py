from insta_save.adapters.instagram.extractor import _extract_author, _author_from_ytdlp_meta


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
