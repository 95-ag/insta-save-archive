import json
import tempfile
from pathlib import Path

from insta_save.engines import transcript as tr


def _write_cookies_json(tmp_path: Path, expires: int) -> str:
    """Write a minimal session_cookies.json with the given expires value."""
    cookies = [
        {
            "name": "sessionid",
            "value": "abc123",
            "domain": ".instagram.com",
            "path": "/",
            "secure": True,
            "expires": expires,
        }
    ]
    p = tmp_path / "session_cookies.json"
    p.write_text(json.dumps(cookies))
    return str(p)


def test_cookies_prep_reads_expires_key(tmp_path):
    """Cookie prep must read the 'expires' key, not the absent 'expirationDate' key.

    The buggy _netscape_cookies read 'expirationDate' which is never present in our
    session_cookies.json — it always produced 0.  After the fix, extract_transcript
    delegates to json_cookies_to_netscape which correctly reads 'expires'.

    We verify this by patching json_cookies_to_netscape to observe whether it is called,
    AND by directly asserting the produced Netscape file carries the real expiry value."""
    from unittest.mock import patch, call
    from insta_save.adapters.instagram.cookies import json_cookies_to_netscape as real_fn

    cookies_json = _write_cookies_json(tmp_path, expires=1893456000)
    cookies_txt = str(tmp_path / "cookies.txt")

    # Wrap the real function so we can assert it was called while still producing output.
    with patch(
        "insta_save.engines.transcript.json_cookies_to_netscape",
        wraps=real_fn,
    ) as mock_convert:
        tr._prepare_cookies(cookies_json, cookies_txt)
        mock_convert.assert_called_once_with(cookies_json, cookies_txt)

    content = Path(cookies_txt).read_text()
    assert "1893456000" in content, (
        f"Expected expiry 1893456000 in Netscape cookie file, got:\n{content}"
    )
    assert "\t0\t" not in content, (
        "Found expiry=0, meaning 'expirationDate' (absent key) was read instead of 'expires'"
    )


def test_gate_rejects_empty():
    assert tr._gate("", 0.99) is False


def test_gate_rejects_too_few_words():
    assert tr._gate("hi there", 0.99) is False  # < 3 words


def test_gate_rejects_low_language_prob():
    assert tr._gate("one two three four", 0.40) is False


def test_gate_accepts_good():
    assert tr._gate("one two three four", 0.80) is True


def test_gate_accepts_exactly_three_words():
    assert tr._gate("one two three", 0.99) is True


def test_gate_accepts_language_prob_at_threshold():
    assert tr._gate("one two three", 0.5) is True


def test_extract_transcript_passes_stdin_devnull(monkeypatch, tmp_path):
    """yt-dlp must not inherit the controlling TTY stdin (else ffmpeg eats run-control keys)."""
    import subprocess
    from insta_save.engines import transcript

    captured = {}

    class _Result:
        returncode = 0
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured["kwargs"] = kwargs
        return _Result()

    monkeypatch.setattr(transcript.subprocess, "run", _fake_run)
    monkeypatch.setattr(transcript, "_prepare_cookies", lambda *a, **k: None)
    monkeypatch.setattr(transcript, "transcribe", lambda *a, **k: ("hi there now", True, "en"))

    transcript.extract_transcript(ig_link="https://x/reel/AB/", shortcode="AB",
                                  tmp_dir=str(tmp_path), cookies_json="c.json")
    assert captured["kwargs"].get("stdin") is subprocess.DEVNULL


def _stub_yt_dlp(monkeypatch, returncode, stderr):
    from insta_save.engines import transcript

    class _Result:
        pass
    _Result.returncode = returncode
    _Result.stderr = stderr
    monkeypatch.setattr(transcript.subprocess, "run", lambda cmd, **k: _Result())
    monkeypatch.setattr(transcript, "_prepare_cookies", lambda *a, **k: None)


def test_no_audio_stream_returns_unavailable_not_raise(monkeypatch, tmp_path):
    """A reel with no audio stream (music stripped / silent) fails yt-dlp audio
    postprocessing. That's a content property, not a failure — return no transcript so the
    item advances to caption-only/OCR Extracted instead of being marked Failed (and
    re-failing on every retry, since it is deterministic)."""
    from insta_save.engines import transcript

    _stub_yt_dlp(monkeypatch, 1,
                 "ERROR: Postprocessing: WARNING: unable to obtain file audio codec with ffprobe")
    # transcribe must NOT be reached (there is no audio file to read).
    monkeypatch.setattr(transcript, "transcribe",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("transcribe called")))

    out = transcript.extract_transcript(ig_link="https://x/reel/AB/", shortcode="AB",
                                        tmp_dir=str(tmp_path), cookies_json="c.json")
    assert out == {"transcript": None, "transcript_available": False, "transcript_language": None}


def test_transient_yt_dlp_error_still_raises(monkeypatch, tmp_path):
    """Transient download errors (HTTP 4xx/5xx) must still raise → Failed → retryable later
    (don't swallow them into a permanent no-transcript)."""
    import pytest
    from insta_save.engines import transcript

    _stub_yt_dlp(monkeypatch, 1,
                 "ERROR: unable to download video data: HTTP Error 429: Too Many Requests")
    with pytest.raises(RuntimeError, match="yt-dlp failed"):
        transcript.extract_transcript(ig_link="https://x/reel/AB/", shortcode="AB",
                                      tmp_dir=str(tmp_path), cookies_json="c.json")
