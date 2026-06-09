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
                    "ocr": {"mode": "escalate", "escalate_threshold": 0.6}},
        "batch": {"max_items": 15, "max_char_budget": 80000},
        "guardrails": {"max_items_per_run": 500, "max_spend_usd": 5.0},
    }))
    assert cfg.mode == "first-time"
    assert cfg.enrich.backend == "cowork"
    assert cfg.extract.transcript_model == "base"
    assert cfg.extract.ocr_mode == "escalate"
    assert cfg.max_items == 15
    assert cfg.guardrails_max_spend_usd == 5.0


def test_defaults_when_minimal(tmp_path):
    cfg = runcfg.load_run_config(_write(tmp_path, {}))
    assert cfg.mode == "incremental"
    assert cfg.enrich.backend == "local"
    assert cfg.extract.transcript_model == "base"
    assert cfg.extract.transcript_vad is True
    assert cfg.extract.ocr_mode == "escalate"
    assert cfg.max_items is None


@pytest.mark.parametrize("bad", [
    {"mode": "nope"},
    {"enrich": {"backend": "telepathy"}},
    {"extract": {"ocr": {"mode": "magic"}}},
])
def test_invalid_enum_raises(tmp_path, bad):
    with pytest.raises(ValueError):
        runcfg.load_run_config(_write(tmp_path, bad))
