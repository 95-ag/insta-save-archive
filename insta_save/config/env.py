"""Secrets + machine-specific runtime knobs from environment / .env.

run.json carries run *behavior* (mode, backend, ocr.mode); this carries
*secrets and machine knobs* (Notion creds, tmp dir, delays, display strategy,
extract version). Notion creds are loaded but validated lazily via validate_notion()."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

VALID_DISPLAY_MODES = {"auto", "native", "wsl-vcxsrv", "none"}


@dataclass(frozen=True)
class EnvConfig:
    notion_token: str
    notion_database_id: str
    tmp_dir: str
    extract_version: str
    notion_write_delay: float
    extract_delay_min: float
    extract_delay_max: float
    display_mode: str
    cookies_file: str


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key, str(default)).strip()
    try:
        return float(raw)
    except ValueError as e:
        raise ValueError(f"env: {key} must be a float, got {raw!r}") from e


def load_env() -> EnvConfig:
    display_mode = os.getenv("DISPLAY_MODE", "auto").strip()
    if display_mode not in VALID_DISPLAY_MODES:
        raise ValueError(
            f"env: invalid DISPLAY_MODE {display_mode!r}; expected one of {sorted(VALID_DISPLAY_MODES)}"
        )
    extract_delay_min = _env_float("EXTRACT_DELAY_MIN", 3.0)
    extract_delay_max = _env_float("EXTRACT_DELAY_MAX", 7.0)
    if extract_delay_min > extract_delay_max:
        raise ValueError(
            f"env: EXTRACT_DELAY_MIN ({extract_delay_min}) must not exceed "
            f"EXTRACT_DELAY_MAX ({extract_delay_max})"
        )
    return EnvConfig(
        notion_token=os.getenv("NOTION_TOKEN", "").strip(),
        notion_database_id=os.getenv("NOTION_DATABASE_ID", "").strip(),
        tmp_dir=os.getenv("TMP_DIR", "tmp").strip(),
        extract_version=os.getenv("EXTRACT_VERSION", "v2.0-base-tuned").strip(),
        notion_write_delay=_env_float("NOTION_WRITE_DELAY", 0.4),
        extract_delay_min=extract_delay_min,
        extract_delay_max=extract_delay_max,
        display_mode=display_mode,
        cookies_file=os.getenv("COOKIES_FILE", "session_cookies.json").strip(),
    )


def validate_notion(cfg: EnvConfig) -> None:
    """Raise RuntimeError if Notion credentials are missing. Call at client init, not startup."""
    missing = [k for k, v in [("NOTION_TOKEN", cfg.notion_token),
                               ("NOTION_DATABASE_ID", cfg.notion_database_id)] if not v]
    if missing:
        raise RuntimeError("Missing Notion configuration:\n" + "\n".join(f"  - {k}" for k in missing))
