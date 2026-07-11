import json
import pytest
from insta_save.config import run as runcfg


def _write(tmp_path, data):
    p = tmp_path / "run.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_loads_full_config(tmp_path):
    cfg = runcfg.load_run_config(_write(tmp_path, {
        "mode": "first-time",
        "enrich": {"backend": "cowork", "model": "claude-sonnet", "effort": "medium"},
        "extract": {"transcript": {"model": "base", "vad": True},
                    "ocr": {"mode": "rapidocr"}},
        "batch": {"max_items": 15, "max_char_budget": 80000, "max_image_tokens": 150000},
        "guardrails": {"max_items_per_run": 500, "max_spend_usd": 5.0},
    }))
    assert cfg.mode == "first-time"
    assert cfg.enrich.backend == "cowork"
    assert cfg.extract.transcript_model == "base"
    assert cfg.extract.ocr_mode == "rapidocr"
    assert cfg.max_items == 15
    assert cfg.guardrails_max_spend_usd == 5.0
    assert cfg.image_token_budget == 150000


def test_defaults_when_minimal(tmp_path):
    cfg = runcfg.load_run_config(_write(tmp_path, {}))
    assert cfg.mode == "incremental"
    assert cfg.enrich.backend == "local"
    assert cfg.extract.transcript_model == "base"
    assert cfg.extract.transcript_vad is True
    assert cfg.extract.ocr_mode == "rapidocr"
    assert cfg.max_items is None
    assert cfg.image_token_budget == 120000


@pytest.mark.parametrize("bad", [
    {"mode": "nope"},
    {"enrich": {"backend": "telepathy"}},
    {"extract": {"ocr": {"mode": "magic"}}},
])
def test_invalid_enum_raises(tmp_path, bad):
    with pytest.raises(ValueError):
        runcfg.load_run_config(_write(tmp_path, bad))


def test_deterministic_and_language_defaults(tmp_path):
    cfg = runcfg.load_run_config(_write(tmp_path, {}))
    assert cfg.deterministic_title_mode == "template"
    assert cfg.output_language == "english"


def test_deterministic_title_mode_llm(tmp_path):
    cfg = runcfg.load_run_config(_write(tmp_path, {
        "deterministic": {"title_mode": "llm"}, "output_language": "english"}))
    assert cfg.deterministic_title_mode == "llm"


def test_invalid_title_mode_rejected(tmp_path):
    with pytest.raises(ValueError):
        runcfg.load_run_config(_write(tmp_path, {"deterministic": {"title_mode": "bogus"}}))


def test_api_mode_defaults_to_sync(tmp_path):
    cfg = runcfg.load_run_config(_write(tmp_path, {}))
    assert cfg.enrich.api_mode == "sync"


def test_invalid_api_mode_rejected(tmp_path):
    with pytest.raises(ValueError):
        runcfg.load_run_config(_write(tmp_path, {"enrich": {"api_mode": "telegram"}}))


def test_claude_p_is_a_valid_backend():
    from insta_save.config.run import VALID_BACKENDS
    assert "claude-p" in VALID_BACKENDS


def test_load_run_config_missing_file_has_actionable_message(tmp_path):
    with pytest.raises(FileNotFoundError) as ei:
        runcfg.load_run_config(path=str(tmp_path / "nope.json"))
    assert "isa run" in str(ei.value)
