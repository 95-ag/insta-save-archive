"""Transcript engine — yt-dlp audio + faster-whisper base int8 (CPU) with tuned params (D14).

CARRYOVER: Netscape cookie conversion at runtime; venv-local yt-dlp; cleanup in finally;
transcript gate nulls music-only/low-confidence output."""

import logging
import os
import subprocess
import sys
from pathlib import Path

from insta_save.adapters.instagram.cookies import json_cookies_to_netscape

log = logging.getLogger(__name__)

_MIN_WORDS = 3
_MIN_LANG_PROB = 0.5


def _gate(transcript: str, language_probability: float) -> bool:
    words = transcript.split()
    return bool(transcript and len(words) >= _MIN_WORDS and language_probability >= _MIN_LANG_PROB)


def _prepare_cookies(json_path: str, txt_path: str) -> None:
    """Convert JSON session cookies to Netscape format for yt-dlp."""
    json_cookies_to_netscape(json_path, txt_path)


def transcribe(audio_path: str, model_size: str = "base", vad: bool = True) -> tuple[str, bool, str]:
    """faster-whisper base int8 on CPU with tuned params (D14). Returns (text, available, language)."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        audio_path,
        beam_size=5,
        vad_filter=vad,
        condition_on_previous_text=False,
        temperature=0.0,
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
    )
    words = []
    for segment in segments:
        words.extend(segment.text.split())
    transcript = " ".join(words).strip()
    return transcript, _gate(transcript, info.language_probability), info.language


def extract_transcript(ig_link: str, shortcode: str, tmp_dir: str, cookies_json: str,
                       model_size: str = "base", vad: bool = True) -> dict:
    """Download reel audio via yt-dlp and transcribe. Audio + cookies cleaned in finally.
    Returns {"transcript": str|None, "transcript_available": bool, "transcript_language": str|None}."""
    tmp = Path(tmp_dir)
    tmp.mkdir(exist_ok=True)
    cookies_txt = str(tmp / "cookies.txt")
    audio_path = str(tmp / f"{shortcode}.mp3")
    _prepare_cookies(cookies_json, cookies_txt)
    try:
        yt = str(Path(sys.executable).parent / "yt-dlp")
        result = subprocess.run(
            [yt, "--cookies", cookies_txt, "--extract-audio", "--audio-format", "mp3",
             "--output", audio_path, "--quiet", "--no-warnings", ig_link],
            capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            # A reel with no audio stream (music stripped for copyright / silent upload)
            # downloads fine but fails at audio postprocessing — yt-dlp can't read an audio
            # codec that isn't there. That's a content property, not a failure: return no
            # transcript so the reel advances to caption-only/OCR Extracted instead of being
            # marked Failed (and re-failing identically on every retry, since "no audio" is
            # deterministic). Transient download errors (HTTP 4xx/5xx, network) still raise →
            # Failed → genuinely retryable via `isa status --retry-failed`.
            if "unable to obtain file audio codec" in stderr:
                log.info("transcript %s — no audio stream; caption/OCR only", shortcode)
                return {"transcript": None, "transcript_available": False,
                        "transcript_language": None}
            raise RuntimeError(f"yt-dlp failed: {stderr}")
        transcript, available, language = transcribe(audio_path, model_size=model_size, vad=vad)
        log.info("transcript %s — available=%s words=%d", shortcode, available,
                 len(transcript.split()) if transcript else 0)
        return {"transcript": transcript if available else None, "transcript_available": available,
                "transcript_language": language if available else None}
    finally:
        for path in (audio_path, cookies_txt):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
