from insta_save.engines import ocr


def test_ocr_score_averages_box_confidences():
    rapid_result = [[None, "hello", 0.9], [None, "world", 0.7]]
    text, conf = ocr.ocr_score(rapid_result)
    assert text == "hello\nworld"
    assert abs(conf - 0.8) < 1e-9


def test_ocr_score_empty():
    assert ocr.ocr_score(None) == ("", None)
    assert ocr.ocr_score([]) == ("", None)


def test_slide_record_shape():
    rec = ocr.slide_record(2, "txt", 0.5, image="slides/ab/slide2.jpg")
    assert rec == {"slide": 2, "text": "txt", "ocr_confidence": 0.5,
                   "image": "slides/ab/slide2.jpg"}


def test_slide_record_empty_text_is_none():
    assert ocr.slide_record(1, "", None)["text"] is None


def test_ocr_score_text_without_scores():
    # boxes have text but no confidence score -> text returned, confidence None
    assert ocr.ocr_score([[None, "hello", None]]) == ("hello", None)


def test_extract_ocr_frames_subprocesses_use_devnull(monkeypatch, tmp_path):
    """Both yt-dlp and ffmpeg must get stdin=DEVNULL; ffmpeg also -nostdin."""
    import subprocess
    from insta_save.engines import ocr

    calls = []

    class _Result:
        returncode = 0
        stderr = ""

    def _fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _Result()

    monkeypatch.setattr(ocr.subprocess, "run", _fake_run)
    monkeypatch.setattr(ocr, "json_cookies_to_netscape", lambda *a, **k: None)
    ocr.extract_ocr_frames(ig_link="https://x/reel/AB/", shortcode="AB",
                           tmp_dir=str(tmp_path), cookies_json="c.json")
    assert len(calls) == 2, f"Expected 2 subprocess.run calls, got {len(calls)}"
    yt_cmd, yt_kw = calls[0]
    ff_cmd, ff_kw = calls[1]
    assert yt_kw.get("stdin") is subprocess.DEVNULL
    assert ff_kw.get("stdin") is subprocess.DEVNULL
    assert "-nostdin" in ff_cmd


def test_ocr_cookies_reads_expires_not_expirationDate(tmp_path):
    """extract_ocr_frames must produce a Netscape cookies file that carries the
    real `expires` timestamp, not 0.  The old private _netscape_cookies read
    `expirationDate` which is absent in session_cookies.json and always yielded 0.
    After the refactor, ocr.py calls the canonical json_cookies_to_netscape which
    reads `expires` — so the produced file must contain 1893456000."""
    import json

    cookies = [
        {
            "name": "sessionid",
            "value": "abc123",
            "domain": ".instagram.com",
            "path": "/",
            "secure": True,
            "expires": 1893456000,
        }
    ]
    json_path = tmp_path / "session_cookies.json"
    json_path.write_text(json.dumps(cookies))

    out_path = tmp_path / "cookies.txt"

    # ocr.py must no longer have a private _netscape_cookies helper — it must
    # delegate to the canonical converter from adapters.instagram.cookies.
    assert not hasattr(ocr, "_netscape_cookies"), (
        "_netscape_cookies should have been removed from ocr.py; "
        "use json_cookies_to_netscape from adapters.instagram.cookies instead"
    )

    # Verify the canonical converter (which ocr.py now calls) reads `expires`
    from insta_save.adapters.instagram.cookies import json_cookies_to_netscape
    json_cookies_to_netscape(str(json_path), str(out_path))
    content = out_path.read_text()
    assert "1893456000" in content, (
        "Canonical converter must preserve the `expires` timestamp; got:\n" + content
    )
    # Confirm the old bug is absent: expirationDate-reading code produced 0
    lines = [ln for ln in content.splitlines() if "sessionid" in ln]
    assert lines, "Expected a sessionid cookie line"
    assert "\t0\t" not in lines[0], (
        "Expires field is 0 — converter is still reading `expirationDate`; "
        "should read `expires`"
    )
