"""
Deep extraction — Stage 2.

Each callable is independent; they share a tmp_dir for downloaded media.
Temp files are always cleaned up in a finally block — callers must not
depend on them surviving past the call.

Selectors reused from extractor.py where applicable.
"""

import json
import logging
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import BrowserContext

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selectors (carousel — validated in V3)
# ---------------------------------------------------------------------------

CAROUSEL_NEXT_SEL = "button[aria-label='Next']"

# Content image path markers — distinguishes post images from profile pics.
# Profile pics use t51.2885-19 / t51.89012-19.
_CONTENT_PATH_MARKERS = ("/t51.82787-15/", "/t51.71878-15/")

# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------

_TRANSCRIPT_MIN_WORDS = 3
_TRANSCRIPT_MIN_LANG_PROB = 0.5


def transcribe(audio_path: str, model_size: str = "base") -> tuple[str, bool]:
    """
    Transcribe an audio file using faster-whisper int8.

    Returns (transcript_text, transcript_available).
    transcript_available is False when output is empty, junk, or the detected
    language probability is too low (e.g. music-only reels).
    """
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(audio_path, beam_size=5, vad_filter=True)

    words = []
    for segment in segments:
        words.extend(segment.text.split())

    transcript = " ".join(words).strip()
    available = bool(
        transcript
        and len(words) >= _TRANSCRIPT_MIN_WORDS
        and info.language_probability >= _TRANSCRIPT_MIN_LANG_PROB
    )
    return transcript, available


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


def extract_transcript(
    ig_link: str,
    shortcode: str,
    tmp_dir: str,
    model_size: str = "base",
    cookies_json: str = "session_cookies.json",
) -> dict:
    """
    Download reel audio via yt-dlp and transcribe with faster-whisper.

    Returns:
        {
            "transcript": str | None,
            "transcript_available": bool,
        }

    Audio file is deleted in a finally block regardless of success or failure.
    """
    tmp = Path(tmp_dir)
    tmp.mkdir(exist_ok=True)

    cookies_txt = str(tmp / "cookies.txt")
    audio_path = str(tmp / f"{shortcode}.mp3")

    _netscape_cookies(cookies_json, cookies_txt)

    try:
        _yt_dlp = str(Path(sys.executable).parent / "yt-dlp")
        result = subprocess.run(
            [
                _yt_dlp,
                "--cookies", cookies_txt,
                "--extract-audio",
                "--audio-format", "mp3",
                "--output", audio_path,
                "--quiet",
                "--no-warnings",
                ig_link,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()}")

        transcript, available = transcribe(audio_path, model_size=model_size)
        log.info(
            "extractor_deep: transcript %s — available=%s words=%d",
            shortcode,
            available,
            len(transcript.split()) if transcript else 0,
        )
        return {"transcript": transcript if available else None, "transcript_available": available}

    finally:
        for path in [audio_path, cookies_txt]:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

def ocr_image(image_path: str) -> str:
    """
    Run RapidOCR on a single image file.
    Returns merged text from all detected boxes, or empty string if none.
    """
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    result, _ = engine(image_path)
    if not result:
        return ""
    return "\n".join(box[1] for box in result if box[1])


def _load_session_cookies(json_path: str) -> dict:
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


def extract_ocr_frames(
    shortcode: str,
    tmp_dir: str,
    cookies_json: str = "session_cookies.json",
) -> str:
    """
    Sample frames from a yt-dlp-downloaded video file and OCR each frame.

    Expects the video file at tmp_dir/{shortcode}.mp4 — callers that already
    invoked extract_transcript should pass the same tmp_dir; the video is
    downloaded separately here because extract_transcript only keeps the mp3.

    Returns merged OCR text across all sampled frames (deduplicated lines).
    Temp files are cleaned up in a finally block.
    """
    tmp = Path(tmp_dir)
    tmp.mkdir(exist_ok=True)

    cookies_txt = str(tmp / "cookies.txt")
    _netscape_cookies(cookies_json, cookies_txt)

    ig_link = f"https://www.instagram.com/reel/{shortcode}/"
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
        _ffmpeg = "ffmpeg"
        subprocess.run(
            [_ffmpeg, "-i", video_path, "-vf", "fps=1", str(frames_dir / "frame_%04d.jpg"),
             "-loglevel", "error"],
            check=True,
            timeout=120,
        )

        seen_lines: set[str] = set()
        all_text: list[str] = []
        for frame in sorted(frames_dir.iterdir()):
            text = ocr_image(str(frame))
            for line in text.splitlines():
                line = line.strip()
                if line and line not in seen_lines:
                    seen_lines.add(line)
                    all_text.append(line)

        return "\n".join(all_text)

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


def extract_post(
    context: BrowserContext,
    ig_link: str,
    shortcode: str,
    tmp_dir: str,
    cookies_json: str = "session_cookies.json",
) -> list[dict]:
    """
    Download and OCR the single image of a Post.

    Uses scope="img" (not "ul img") because Post images are not wrapped in a <ul>.
    Returns [{"slide": 1, "text": "..."}] — same format as extract_carousel so
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
        import time as _time
        _time.sleep(2.5)

        urls = _content_image_urls(page, scope="img")
        if not urls:
            log.warning("extractor_deep: post %s — no content image found", shortcode)
            return []

        dest = str(tmp / f"{shortcode}_post.jpg")
        _download_image(urls[0], dest, cookie_header)
        downloaded.append(dest)
        text = ocr_image(dest)
        log.info("extractor_deep: post %s — %d chars OCR", shortcode, len(text))
        return [{"slide": 1, "text": text or None}]

    finally:
        page.close()
        for path in downloaded:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


def extract_carousel(
    context: BrowserContext,
    ig_link: str,
    shortcode: str,
    tmp_dir: str,
    cookies_json: str = "session_cookies.json",
) -> list[dict]:
    """
    Step through carousel slides via DOM navigation, download each slide image,
    and OCR it.

    Returns a list of dicts ordered by slide position:
        [{"slide": 1, "text": "..."}, {"slide": 2, "text": "..."}, ...]

    Images are downloaded to tmp_dir and cleaned up in a finally block.
    """
    from pipeline.extractor import CAROUSEL_NEXT_SEL as _NEXT  # same selector, single source

    tmp = Path(tmp_dir)
    tmp.mkdir(exist_ok=True)
    cookie_header = _load_session_cookies(cookies_json)

    downloaded: list[str] = []
    page = context.new_page()
    try:
        page.goto(ig_link, wait_until="domcontentloaded", timeout=20_000)
        import time as _time
        _time.sleep(2.5)

        slide_urls: list[str] = []
        seen_urls: set[str] = set()

        def _collect_current():
            for url in _content_image_urls(page, scope="ul img"):
                if url not in seen_urls:
                    seen_urls.add(url)
                    slide_urls.append(url)

        _collect_current()
        log.info("extractor_deep: carousel %s — initial load: %d slide URLs, next_btn=%s",
                 shortcode, len(slide_urls), page.locator(CAROUSEL_NEXT_SEL).count() > 0)

        clicks = 0
        while page.locator(CAROUSEL_NEXT_SEL).count() > 0:
            page.locator(CAROUSEL_NEXT_SEL).first.click()
            clicks += 1
            _time.sleep(1.0)
            _collect_current()

        log.info("extractor_deep: carousel %s — %d slides collected after %d clicks",
                 shortcode, len(slide_urls), clicks)

        if not slide_urls:
            log.warning("extractor_deep: carousel %s — no slide URLs found (ul img matched nothing)",
                        shortcode)
            return []

        results = []
        for i, url in enumerate(slide_urls, start=1):
            dest = str(tmp / f"{shortcode}_slide{i}.jpg")
            try:
                _download_image(url, dest, cookie_header)
                downloaded.append(dest)
                text = ocr_image(dest)
                results.append({"slide": i, "text": text or None})
                log.info("extractor_deep: carousel %s slide %d — %d chars", shortcode, i, len(text))
            except Exception as e:
                log.warning("extractor_deep: carousel %s slide %d failed — %s", shortcode, i, e)
                results.append({"slide": i, "text": None})

        return results

    finally:
        page.close()
        for path in downloaded:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
