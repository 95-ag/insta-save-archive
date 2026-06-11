from insta_save.adapters.instagram.harvest import scroll_harvest


class _FakeLocator:
    def __init__(self, links): self._links = links
    def all(self): return list(self._links)


class _FakeLink:
    def __init__(self, href): self._href = href
    def get_attribute(self, _): return self._href


class _FakePage:
    """Reveals `reveal_per_scroll` new links each scroll; reports at_bottom once drained."""
    def __init__(self, hrefs, reveal_per_scroll=2):
        self._hrefs, self._reveal, self._shown = hrefs, reveal_per_scroll, 0
    def locator(self, _selector): return _FakeLocator(
        [_FakeLink(h) for h in self._hrefs[:self._shown]])
    def evaluate(self, js):
        if "scrollBy" in js:
            self._shown = min(len(self._hrefs), self._shown + self._reveal)
            return None
        return self._shown >= len(self._hrefs)  # _AT_BOTTOM_JS


def test_harvest_accumulates_all_and_reports_complete(monkeypatch):
    import insta_save.adapters.instagram.harvest as h
    monkeypatch.setattr(h.time, "sleep", lambda *_: None)
    page = _FakePage([f"/reel/c{i}/" for i in range(5)], reveal_per_scroll=2)

    def extract(link):
        href = link.get_attribute("href")
        return (href, href)

    items, complete = scroll_harvest(page, "sel", extract)
    assert len(items) == 5
    assert complete is True


def test_harvest_dedupes_by_key(monkeypatch):
    import insta_save.adapters.instagram.harvest as h
    monkeypatch.setattr(h.time, "sleep", lambda *_: None)
    page = _FakePage(["/reel/x/", "/reel/x/", "/reel/y/"], reveal_per_scroll=3)
    items, _ = scroll_harvest(page, "sel", lambda l: (l.get_attribute(None), 1))
    assert set(items) == {"/reel/x/", "/reel/y/"}
