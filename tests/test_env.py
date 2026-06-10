import pytest
from insta_save.config import env as envcfg


def _set(monkeypatch, **kw):
    for k in ["NOTION_TOKEN", "NOTION_DATABASE_ID", "TMP_DIR", "EXTRACT_VERSION",
              "NOTION_WRITE_DELAY", "EXTRACT_DELAY_MIN", "EXTRACT_DELAY_MAX",
              "DISPLAY_MODE", "COOKIES_FILE"]:
        monkeypatch.delenv(k, raising=False)
    for k, v in kw.items():
        monkeypatch.setenv(k, v)


def test_defaults(monkeypatch):
    _set(monkeypatch)
    cfg = envcfg.load_env()
    assert cfg.tmp_dir == "tmp"
    assert cfg.extract_version == "v2.0-base-tuned"
    assert cfg.notion_write_delay == 0.4
    assert cfg.extract_delay_min == 3.0 and cfg.extract_delay_max == 7.0
    assert cfg.display_mode == "auto"
    assert cfg.cookies_file == "session_cookies.json"


def test_overrides(monkeypatch):
    _set(monkeypatch, NOTION_TOKEN="t", NOTION_DATABASE_ID="d",
         EXTRACT_VERSION="v2.1", DISPLAY_MODE="native", NOTION_WRITE_DELAY="1.0")
    cfg = envcfg.load_env()
    assert cfg.notion_token == "t" and cfg.notion_database_id == "d"
    assert cfg.extract_version == "v2.1" and cfg.display_mode == "native"
    assert cfg.notion_write_delay == 1.0


def test_invalid_display_mode(monkeypatch):
    _set(monkeypatch, DISPLAY_MODE="hologram")
    with pytest.raises(ValueError):
        envcfg.load_env()


def test_validate_notion_raises_when_missing(monkeypatch):
    _set(monkeypatch)
    cfg = envcfg.load_env()
    with pytest.raises(RuntimeError):
        envcfg.validate_notion(cfg)


def test_invalid_float_raises(monkeypatch):
    _set(monkeypatch, NOTION_WRITE_DELAY="abc")
    with pytest.raises(ValueError):
        envcfg.load_env()


def test_delay_min_exceeding_max_raises(monkeypatch):
    _set(monkeypatch, EXTRACT_DELAY_MIN="9.0", EXTRACT_DELAY_MAX="2.0")
    with pytest.raises(ValueError):
        envcfg.load_env()
