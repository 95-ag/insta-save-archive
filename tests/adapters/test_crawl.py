from insta_save.adapters.instagram.crawl import resolve_collection_url, _index_extract, _grid_extract


def test_resolve_collection_url():
    assert resolve_collection_url("me", "dev", "123") == \
        "https://www.instagram.com/me/saved/dev/123/"


class _Link:
    def __init__(self, href, text=""): self._href, self._text = href, text
    def get_attribute(self, _): return self._href
    def inner_text(self): return self._text


def test_grid_extract_pulls_shortcode_and_canonical_url():
    kv = _grid_extract(_Link("/reel/ABC123/?x=1"))
    assert kv == ("ABC123", "https://www.instagram.com/reel/ABC123/")


def test_grid_extract_ignores_non_posts():
    assert _grid_extract(_Link("/explore/")) is None


def test_index_extract_keys_by_slug_and_skips_all_posts():
    kv = _index_extract(_Link("/me/saved/dev/123/", "Dev"))
    assert kv == ("dev", {"name": "Dev", "slug": "dev", "numeric_id": "123"})
    assert _index_extract(_Link("/me/saved/all-posts/", "All Posts")) is None
