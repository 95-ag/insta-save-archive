"""OCR engine — RapidOCR + carousel/post/frames. NEW: per-slide confidence + needs_vision flag.

CARRYOVER: carousel scope 'ul img'; post scope 'img'; CDN markers t51.82787-15/t51.71878-15;
IG CDN instagram.fblr22-*.fna.fbcdn.net; cookie-header image download; yt-dlp mp4 + ffmpeg fps=1;
cleanup in finally. Sub-threshold slides are FLAGGED only (needs_vision) for a later vision pass —
no API call here."""

import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

CAROUSEL_NEXT_SEL = "button[aria-label='Next']"
_CONTENT_PATH_MARKERS = ("/t51.82787-15/", "/t51.71878-15/")
_PAGE_LOAD_PAUSE = 2.5


# --- pure, unit-tested ------------------------------------------------------
def ocr_score(rapid_result) -> tuple[str, float | None]:
    """From a RapidOCR result ([[box, text, score], ...]) -> (joined_text, mean_confidence|None)."""
    if not rapid_result:
        return "", None
    texts = [r[1] for r in rapid_result if len(r) > 1 and r[1]]
    scores = [r[2] for r in rapid_result if len(r) > 2 and r[2] is not None]
    return "\n".join(texts), (sum(scores) / len(scores) if scores else None)


def needs_vision(text: str, confidence: float | None, threshold: float) -> bool:
    if not text.strip():
        return True
    if confidence is None:
        return True
    return confidence < threshold


def slide_record(slide: int, text: str, confidence: float | None, threshold: float) -> dict:
    return {
        "slide": slide,
        "text": text or None,
        "ocr_confidence": confidence,
        "needs_vision": needs_vision(text, confidence, threshold),
    }


# --- RapidOCR wrapper -------------------------------------------------------
def ocr_image(image_path: str) -> tuple[str, float | None]:
    from rapidocr_onnxruntime import RapidOCR
    engine = RapidOCR()
    result, _ = engine(image_path)
    return ocr_score(result)


# --- helpers ----------------------------------------------------------------
def _netscape_cookies(json_path: str, txt_path: str) -> None:
    """Convert session_cookies.json to Netscape format for yt-dlp."""
    with open(json_path) as f:
        cookies = json.load(f)
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        domain = c.get("domain", "")
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        expiry = str(int(c.get("expirationDate", 0)))
        name = c.get("name", "")
        value = c.get("value", "")
        lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}")
    with open(txt_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def _load_session_cookies(json_path: str) -> str:
    """Return a Cookie header value built from session_cookies.json."""
    with open(json_path) as f:
        cookies = json.load(f)
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def _download_image(url: str, dest: str, cookie_header: str) -> None:
    req = urllib.request.Request(url, headers={"Cookie": cookie_header, "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        with open(dest, "wb") as f:
            f.write(resp.read())


def _content_image_urls(page, scope: str = "img") -> list[str]:
    """
    Return content image URLs from the page, scoped to a CSS selector.
    Filters on content path markers to exclude profile pics and UI assets.

    Use scope="ul img" for carousels — the carousel slides live in the single
    <ul> on the post page, scoping prevents picking up feed images below.
    """
    imgs = page.query_selector_all(scope)
    seen = set()
    urls = []
    for img in imgs:
        src = img.get_attribute("src") or ""
        if any(m in src for m in _CONTENT_PATH_MARKERS) and src not in seen:
            seen.add(src)
            urls.append(src)
    return urls


# --- extract functions ------------------------------------------------------
def extract_carousel(
    context,
    ig_link: str,
    shortcode: str,
    tmp_dir: str,
    cookies_json: str,
    threshold: float,
) -> list[dict]:
    """
    Step through carousel slides via DOM navigation, download each slide image,
    and OCR it.

    Returns a list of slide_record dicts ordered by slide position.
    Images are downloaded to tmp_dir and cleaned up in a finally block.
    """
    tmp = Path(tmp_dir)
    tmp.mkdir(exist_ok=True)
    cookie_header = _load_session_cookies(cookies_json)

    downloaded: list[str] = []
    page = context.new_page()
    try:
        page.goto(ig_link, wait_until="domcontentloaded", timeout=20_000)
        time.sleep(_PAGE_LOAD_PAUSE)

        slide_urls: list[str] = []
        seen_urls: set[str] = set()

        def _collect_current():
            for url in _content_image_urls(page, scope="ul img"):
                if url not in seen_urls:
                    seen_urls.add(url)
                    slide_urls.append(url)

        _collect_current()
        log.info("ocr: carousel %s — initial load: %d slide URLs, next_btn=%s",
                 shortcode, len(slide_urls), page.locator(CAROUSEL_NEXT_SEL).count() > 0)

        clicks = 0
        while page.locator(CAROUSEL_NEXT_SEL).count() > 0:
            page.locator(CAROUSEL_NEXT_SEL).first.click()
            clicks += 1
            time.sleep(1.0)
            _collect_current()

        log.info("ocr: carousel %s — %d slides collected after %d clicks",
                 shortcode, len(slide_urls), clicks)

        if not slide_urls:
            log.warning("ocr: carousel %s — no slide URLs found (ul img matched nothing)", shortcode)
            return []

        results = []
        for i, url in enumerate(slide_urls, start=1):
            dest = str(tmp / f"{shortcode}_slide{i}.jpg")
            try:
                _download_image(url, dest, cookie_header)
                downloaded.append(dest)
                text, conf = ocr_image(dest)
                results.append(slide_record(i, text, conf, threshold))
                log.info("ocr: carousel %s slide %d — %d chars conf=%.2f",
                         shortcode, i, len(text), conf if conf is not None else 0)
            except Exception as e:
                log.warning("ocr: carousel %s slide %d failed — %s", shortcode, i, e)
                results.append(slide_record(i, "", None, threshold))

        return results

    finally:
        page.close()
        for path in downloaded:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


def extract_post(
    context,
    ig_link: str,
    shortcode: str,
    tmp_dir: str,
    cookies_json: str,
    threshold: float,
) -> list[dict]:
    """
    Download and OCR the single image of a Post.

    Uses scope="img" (not "ul img") because Post images are not wrapped in a <ul>.
    Returns [slide_record(1, text, conf, threshold)] — same format as extract_carousel so
    results write to the carousel_slides field without any schema change.
    Returns [] if no content image is found on the page.
    """
    tmp = Path(tmp_dir)
    tmp.mkdir(exist_ok=True)
    cookie_header = _load_session_cookies(cookies_json)

    downloaded: list[str] = []
    page = context.new_page()
    try:
        page.goto(ig_link, wait_until="domcontentloaded", timeout=20_000)
        time.sleep(_PAGE_LOAD_PAUSE)

        urls = _content_image_urls(page, scope="img")
        if not urls:
            log.warning("ocr: post %s — no content image found", shortcode)
            return []

        dest = str(tmp / f"{shortcode}_post.jpg")
        _download_image(urls[0], dest, cookie_header)
        downloaded.append(dest)
        text, conf = ocr_image(dest)
        log.info("ocr: post %s — %d chars conf=%s", shortcode, len(text),
                 f"{conf:.2f}" if conf is not None else "None")
        return [slide_record(1, text, conf, threshold)]

    finally:
        page.close()
        for path in downloaded:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


def extract_ocr_frames(
    ig_link: str,
    shortcode: str,
    tmp_dir: str,
    cookies_json: str,
    threshold: float,
) -> dict:
    """
    Sample frames from a yt-dlp-downloaded video file and OCR each frame.

    Downloads the video separately (extract_transcript only keeps the mp3).
    Returns {"text": joined_text, "confidence": mean_conf, "needs_vision": bool}.
    Temp files (video, cookies.txt, frames dir) are cleaned up in a finally block.
    """
    tmp = Path(tmp_dir)
    tmp.mkdir(exist_ok=True)

    cookies_txt = str(tmp / "cookies.txt")
    _netscape_cookies(cookies_json, cookies_txt)

    video_path = str(tmp / f"{shortcode}_ocr.mp4")
    frames_dir = tmp / f"{shortcode}_frames"
    frames_dir.mkdir(exist_ok=True)

    try:
        _yt_dlp = str(Path(sys.executable).parent / "yt-dlp")
        result = subprocess.run(
            [
                _yt_dlp,
                "--cookies", cookies_txt,
                "--format", "mp4",
                "--output", video_path,
                "--quiet",
                "--no-warnings",
                ig_link,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"yt-dlp (video) failed: {result.stderr.strip()}")

        # Sample 1 frame per second via ffmpeg
        subprocess.run(
            ["ffmpeg", "-i", video_path, "-vf", "fps=1",
             str(frames_dir / "frame_%04d.jpg"), "-loglevel", "error"],
            check=True,
            timeout=120,
        )

        seen_lines: set[str] = set()
        all_text: list[str] = []
        confs: list[float] = []
        for frame in sorted(frames_dir.iterdir()):
            text, conf = ocr_image(str(frame))
            if conf is not None:
                confs.append(conf)
            for line in text.splitlines():
                line = line.strip()
                if line and line not in seen_lines:
                    seen_lines.add(line)
                    all_text.append(line)

        joined = "\n".join(all_text)
        mean_conf = sum(confs) / len(confs) if confs else None
        return {
            "text": joined,
            "confidence": mean_conf,
            "needs_vision": needs_vision(joined, mean_conf, threshold),
        }

    finally:
        for path in [video_path, cookies_txt]:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        for frame in frames_dir.iterdir():
            try:
                frame.unlink()
            except Exception:
                pass
        try:
            frames_dir.rmdir()
        except Exception:
            pass
