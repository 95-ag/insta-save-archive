"""
Instagram session management.

Provides ensure_authenticated() — the single entry point for all pipeline
scripts. Loads cookies, validates the session, and runs a headful re-auth
flow if needed. Callers receive a ready BrowserContext.

Usage:
    python session.py          # standalone health check
"""

import json
import logging
import time
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Playwright, TimeoutError as PlaywrightTimeout

COOKIES_FILE = Path("session_cookies.json")
INSTAGRAM_HOME = "https://www.instagram.com/"

# Present only when authenticated
AUTH_SELECTOR = "svg[aria-label='Home']"
# Present on the login page
LOGIN_SELECTOR = "input[name='username']"
# Present when Instagram shows a 2FA / verification challenge
CHALLENGE_SELECTOR = "input[name='verificationCode'], input[name='security_code']"

AUTH_CHECK_TIMEOUT = 8_000   # ms — fast check for already-authed sessions
LOGIN_WAIT_TIMEOUT = 300_000 # ms — 5 min for manual login + 2FA

log = logging.getLogger(__name__)


def _launch_browser(playwright: Playwright, headless: bool = True) -> Browser:
    if not headless:
        from pipeline.display import ensure_display
        ensure_display()
    return playwright.chromium.launch(
        headless=headless,
        slow_mo=80 if not headless else 0,
        args=[
            "--no-sandbox",
            "--disable-gpu",
            "--window-position=100,100",
            "--window-size=1280,900",
        ],
    )


def _new_context(browser: Browser) -> BrowserContext:
    return browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )


def _load_cookies(context: BrowserContext) -> bool:
    if not COOKIES_FILE.exists():
        log.info("session: no cookie file found")
        return False
    cookies = json.loads(COOKIES_FILE.read_text())
    context.add_cookies(cookies)
    log.info("session: loaded %d cookies from %s", len(cookies), COOKIES_FILE)
    return True


def _save_cookies(context: BrowserContext) -> None:
    cookies = context.cookies()
    COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
    log.info("session: saved %d cookies to %s", len(cookies), COOKIES_FILE)


def _check_auth(page) -> bool:
    try:
        page.wait_for_selector(AUTH_SELECTOR, timeout=AUTH_CHECK_TIMEOUT)
        return True
    except PlaywrightTimeout:
        return False


def _run_login(page, context: BrowserContext) -> None:
    """
    Blocks until the user completes manual login (including any 2FA).
    Saves cookies on success. Raises RuntimeError on timeout.
    """
    log.info("session: opening login page — log in manually in the browser window")
    log.info("session: waiting up to 5 minutes for login + 2FA")

    page.goto(INSTAGRAM_HOME, wait_until="domcontentloaded", timeout=20_000)
    time.sleep(1)

    # If not already on login page, navigate there
    if not page.locator(LOGIN_SELECTOR).count():
        page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=20_000)
        time.sleep(1)

    try:
        page.wait_for_selector(AUTH_SELECTOR, timeout=LOGIN_WAIT_TIMEOUT)
    except PlaywrightTimeout:
        raise RuntimeError(
            "session: timed out waiting for manual login — re-run to try again"
        )

    log.info("session: login detected")
    _save_cookies(context)


def ensure_authenticated(playwright: Playwright, headless: bool = True) -> tuple[Browser, BrowserContext]:
    """
    Returns an authenticated (Browser, BrowserContext) pair.

    On first run: opens a browser window for manual login, saves cookies.
    On subsequent runs: loads cookies and validates — re-auths only if needed.
    Re-auth always runs headed (requires visible browser for manual login).
    If called headless and cookies are expired, relaunches headed automatically.

    The caller is responsible for closing the browser when done.
    """
    browser = _launch_browser(playwright, headless=headless)
    context = _new_context(browser)

    had_cookies = _load_cookies(context)

    page = context.new_page()
    page.goto(INSTAGRAM_HOME, wait_until="domcontentloaded", timeout=20_000)
    time.sleep(1)

    authenticated = _check_auth(page)

    if authenticated:
        status = "valid" if had_cookies else "valid (no prior cookies)"
        log.info("session: status=%s", status)
        if not had_cookies:
            _save_cookies(context)
    else:
        if headless:
            log.warning(
                "session: cookies expired — re-auth requires a headed browser; relaunching headed"
            )
            page.close()
            browser.close()
            browser = _launch_browser(playwright, headless=False)
            context = _new_context(browser)
            _load_cookies(context)
            page = context.new_page()
            page.goto(INSTAGRAM_HOME, wait_until="domcontentloaded", timeout=20_000)
            time.sleep(1)
        log.info("session: status=expired — starting re-auth")
        _run_login(page, context)

    page.close()
    return browser, context
