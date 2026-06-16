"""Tests for fail-fast preflight checks."""
import importlib
import urllib.request
from dataclasses import dataclass

import pytest

from insta_save.orchestrator.preflight import preflight, validate_effort


# ---------------------------------------------------------------------------
# Minimal fakes for env / run_cfg
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _FakeEnrich:
    backend: str = "claude-code"
    model: str = "claude-sonnet"
    effort: str = "medium"


@dataclass(frozen=True)
class _FakeEnv:
    notion_token: str = "tok"
    notion_database_id: str = "db_id"
    anthropic_api_key: str = ""


@dataclass(frozen=True)
class _FakeRunCfg:
    enrich: _FakeEnrich = None

    def __post_init__(self):
        if self.enrich is None:
            object.__setattr__(self, "enrich", _FakeEnrich())


def _run_cfg(backend="claude-code", effort="medium"):
    return _FakeRunCfg(enrich=_FakeEnrich(backend=backend, effort=effort))


# ---------------------------------------------------------------------------
# validate_effort
# ---------------------------------------------------------------------------

def test_validate_effort_valid():
    assert validate_effort("medium") is None
    assert validate_effort("low") is None
    assert validate_effort("high") is None


def test_validate_effort_invalid():
    with pytest.raises(SystemExit) as exc_info:
        validate_effort("turbo")
    msg = str(exc_info.value)
    assert "turbo" in msg
    # must list allowed values
    assert "low" in msg
    assert "medium" in msg
    assert "high" in msg


# ---------------------------------------------------------------------------
# preflight: happy path (all checks pass)
# ---------------------------------------------------------------------------

def test_preflight_all_pass(monkeypatch):
    """preflight returns None when every check is stubbed to pass."""
    monkeypatch.setattr(
        "insta_save.orchestrator.preflight.validate_notion",
        lambda env: None,
    )
    env = _FakeEnv()
    cfg = _run_cfg(backend="claude-code", effort="medium")
    assert preflight(env, cfg, stages={"extract"}) is None


# ---------------------------------------------------------------------------
# Notion failure
# ---------------------------------------------------------------------------

def test_preflight_notion_unreachable(monkeypatch):
    monkeypatch.setattr(
        "insta_save.orchestrator.preflight.validate_notion",
        lambda env: (_ for _ in ()).throw(RuntimeError("Notion creds missing")),
    )
    env = _FakeEnv(notion_token="")
    cfg = _run_cfg()
    with pytest.raises(SystemExit) as exc_info:
        preflight(env, cfg, stages=set())
    assert "Notion" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Backend: local (Ollama)
# ---------------------------------------------------------------------------

def test_preflight_local_ollama_down(monkeypatch):
    monkeypatch.setattr(
        "insta_save.orchestrator.preflight.validate_notion",
        lambda env: None,
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout=None: (_ for _ in ()).throw(OSError("connection refused")),
    )
    env = _FakeEnv()
    cfg = _run_cfg(backend="local")
    with pytest.raises(SystemExit) as exc_info:
        preflight(env, cfg, stages=set())
    msg = str(exc_info.value)
    assert "Ollama" in msg
    assert "local" in msg


def test_preflight_local_ollama_ok(monkeypatch):
    monkeypatch.setattr(
        "insta_save.orchestrator.preflight.validate_notion",
        lambda env: None,
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout=None: None,
    )
    env = _FakeEnv()
    cfg = _run_cfg(backend="local")
    assert preflight(env, cfg, stages=set()) is None


# ---------------------------------------------------------------------------
# Backend: api (missing key)
# ---------------------------------------------------------------------------

def test_preflight_api_missing_key(monkeypatch):
    monkeypatch.setattr(
        "insta_save.orchestrator.preflight.validate_notion",
        lambda env: None,
    )
    env = _FakeEnv(anthropic_api_key="")
    cfg = _run_cfg(backend="api")
    with pytest.raises(SystemExit) as exc_info:
        preflight(env, cfg, stages=set())
    msg = str(exc_info.value)
    assert "ANTHROPIC_API_KEY" in msg
    assert "api" in msg


def test_preflight_api_with_key(monkeypatch):
    monkeypatch.setattr(
        "insta_save.orchestrator.preflight.validate_notion",
        lambda env: None,
    )
    env = _FakeEnv(anthropic_api_key="sk-ant-key")
    cfg = _run_cfg(backend="api")
    assert preflight(env, cfg, stages=set()) is None


# ---------------------------------------------------------------------------
# Engine check: extract engines
# ---------------------------------------------------------------------------

def test_preflight_engine_not_importable(monkeypatch):
    monkeypatch.setattr(
        "insta_save.orchestrator.preflight.validate_notion",
        lambda env: None,
    )
    original_import = importlib.import_module

    def _fake_import(name):
        if name == "faster_whisper":
            raise ImportError("no module named faster_whisper")
        return original_import(name)

    monkeypatch.setattr("importlib.import_module", _fake_import)
    env = _FakeEnv()
    cfg = _run_cfg()
    with pytest.raises(SystemExit) as exc_info:
        preflight(env, cfg, stages={"extract"})
    msg = str(exc_info.value)
    assert "faster_whisper" in msg


def test_preflight_engine_check_skipped_without_extract(monkeypatch):
    """Broken engine import must NOT raise when 'extract' is not in stages."""
    monkeypatch.setattr(
        "insta_save.orchestrator.preflight.validate_notion",
        lambda env: None,
    )
    original_import = importlib.import_module

    def _fake_import(name):
        if name in ("faster_whisper", "rapidocr_onnxruntime"):
            raise ImportError(f"no module named {name}")
        return original_import(name)

    monkeypatch.setattr("importlib.import_module", _fake_import)
    env = _FakeEnv()
    cfg = _run_cfg()
    # Must NOT raise even though both extract engines are broken
    assert preflight(env, cfg, stages=set()) is None


# ---------------------------------------------------------------------------
# Invalid effort
# ---------------------------------------------------------------------------

def test_preflight_invalid_effort(monkeypatch):
    monkeypatch.setattr(
        "insta_save.orchestrator.preflight.validate_notion",
        lambda env: None,
    )
    env = _FakeEnv()
    cfg = _run_cfg(effort="turbo")
    with pytest.raises(SystemExit) as exc_info:
        preflight(env, cfg, stages=set())
    assert "turbo" in str(exc_info.value)
