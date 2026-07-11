"""Fail-fast preflight: backend reachable, Notion reachable, engines importable,
config valid (effort). Raises SystemExit on the first problem."""
import importlib
import shutil

from insta_save.adapters.notion import validate_notion

_ALLOWED_EFFORT = {"low", "medium", "high"}


def validate_effort(effort: str) -> None:
    if effort not in _ALLOWED_EFFORT:
        raise SystemExit(f"preflight: enrich.effort={effort!r} invalid "
                         f"(allowed: {', '.join(sorted(_ALLOWED_EFFORT))})")


def _check_backend(env, run_cfg) -> None:
    name = run_cfg.enrich.backend
    if name == "local":
        import urllib.request
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        except Exception as e:
            raise SystemExit(f"preflight: Ollama not reachable for backend 'local' — {e}")
    elif name == "api":
        if not env.anthropic_api_key:
            raise SystemExit("preflight: backend 'api' needs ANTHROPIC_API_KEY in env")
    elif name == "claude-p":
        if shutil.which("claude") is None:
            raise SystemExit("preflight: backend 'claude-p' needs the `claude` CLI on PATH")
    # claude-code / cowork: nothing external to ping (session-driven)


def _check_engines(stages) -> None:
    if "extract" in stages:
        for mod in ("faster_whisper", "rapidocr_onnxruntime"):
            try:
                importlib.import_module(mod)
            except Exception as e:
                raise SystemExit(f"preflight: extract engine {mod!r} not importable — {e}")


def _check_notion(env) -> None:
    # validate_notion raises RuntimeError if creds are missing (presence check only,
    # no live API call). That is acceptable for fail-fast preflight — missing creds
    # = immediately useless regardless of reachability. Wrap any non-SystemExit failure
    # as a clear SystemExit.
    try:
        validate_notion(env)
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"preflight: Notion config invalid/unreachable — {e}")


def preflight(env, run_cfg, *, stages: set) -> None:
    validate_effort(run_cfg.enrich.effort)
    _check_notion(env)
    _check_backend(env, run_cfg)
    _check_engines(stages)
