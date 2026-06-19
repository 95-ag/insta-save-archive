"""Tests for ensure_authenticated's browser lifecycle — especially the headed re-auth
relaunch and the switch back to headless once cookies are saved."""
import insta_save.adapters.instagram.session as session


class _Page:
    def goto(self, *a, **k):
        pass

    def close(self):
        pass


class _Ctx:
    def new_page(self):
        return _Page()

    def cookies(self):
        return []


class _Browser:
    def __init__(self, headless):
        self.headless = headless

    def close(self):
        pass


def _wire(monkeypatch, *, authed):
    """Stub the browser helpers. `authed` controls _check_auth (warm vs cold).
    Returns the `launches` list recording each _launch_browser headless value."""
    state = {"logged_in": False}
    launches = []

    def _fake_launch(pw, env, headless=True):
        launches.append(headless)
        return _Browser(headless)

    monkeypatch.setattr(session, "_launch_browser", _fake_launch)
    monkeypatch.setattr(session, "_new_context", lambda b: _Ctx())
    monkeypatch.setattr(session, "_load_cookies", lambda ctx, cf: state["logged_in"])
    monkeypatch.setattr(session, "_check_auth", lambda page: authed)
    monkeypatch.setattr(session, "_run_login",
                        lambda page, ctx, cf: state.update(logged_in=True))
    monkeypatch.setattr(session.time, "sleep", lambda *a: None)
    return launches


def _env(tmp_path):
    return type("E", (), {"cookies_file": str(tmp_path / "cookies.json")})()


def test_reauth_switches_back_to_headless_when_headless_requested(monkeypatch, tmp_path):
    """Cold start with headless requested: headed only for login, then back to headless."""
    launches = _wire(monkeypatch, authed=False)
    browser, ctx = session.ensure_authenticated(object(), _env(tmp_path), headless=True)
    assert launches == [True, False, True]   # headless -> headed (login) -> headless
    assert browser.headless is True          # final browser the caller uses is headless


def test_reauth_stays_headed_when_headed_requested(monkeypatch, tmp_path):
    """Explicit --headed: stay headed for the whole run (no switch-back)."""
    launches = _wire(monkeypatch, authed=False)
    browser, ctx = session.ensure_authenticated(object(), _env(tmp_path), headless=False)
    assert launches == [False]               # one launch, stays headed
    assert browser.headless is False


def test_valid_session_does_not_relaunch(monkeypatch, tmp_path):
    """Warm start (cookies valid): a single launch in the requested mode, no relaunch."""
    launches = _wire(monkeypatch, authed=True)
    browser, ctx = session.ensure_authenticated(object(), _env(tmp_path), headless=True)
    assert launches == [True]
    assert browser.headless is True
