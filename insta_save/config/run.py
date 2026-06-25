"""Run configuration — how a single invocation behaves."""

import json
from dataclasses import dataclass
from pathlib import Path

VALID_MODES = {"first-time", "incremental"}
VALID_BACKENDS = {"local", "api", "claude-code", "cowork", "claude-p"}
VALID_OCR_MODES = {"none", "rapidocr"}
VALID_TITLE_MODES = {"template", "llm"}
VALID_API_MODES = {"sync", "batches"}

_DEFAULT_RUN = Path("config") / "run.json"


@dataclass(frozen=True)
class EnrichConfig:
    backend: str
    model: str
    effort: str
    api_mode: str = "sync"


@dataclass(frozen=True)
class ExtractConfig:
    transcript_model: str
    transcript_vad: bool
    ocr_mode: str


@dataclass(frozen=True)
class RunConfig:
    mode: str
    enrich: EnrichConfig
    extract: ExtractConfig
    max_items: int | None
    char_budget: int
    image_token_budget: int
    guardrails_max_items_per_run: int | None
    guardrails_max_spend_usd: float | None
    deterministic_title_mode: str = "template"
    output_language: str = "english"


def _require(value, valid, label):
    if value not in valid:
        raise ValueError(f"run config: invalid {label} {value!r}; expected one of {sorted(valid)}")
    return value


def load_run_config(path=_DEFAULT_RUN) -> RunConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"Run config not found: {p} — run `isa run --mode first-time` to seed it")
    data = json.loads(p.read_text(encoding="utf-8"))

    mode = _require(data.get("mode", "incremental"), VALID_MODES, "mode")
    enrich_raw = data.get("enrich", {})
    enrich = EnrichConfig(
        backend=_require(enrich_raw.get("backend", "local"), VALID_BACKENDS, "enrich.backend"),
        model=enrich_raw.get("model", "qwen2.5:7b"),
        effort=enrich_raw.get("effort", "medium"),
        api_mode=_require(enrich_raw.get("api_mode", "sync"), VALID_API_MODES, "enrich.api_mode"),
    )
    extract_raw = data.get("extract", {})
    transcript_raw = extract_raw.get("transcript", {})
    ocr_raw = extract_raw.get("ocr", {})
    extract = ExtractConfig(
        transcript_model=transcript_raw.get("model", "base"),
        transcript_vad=bool(transcript_raw.get("vad", True)),
        ocr_mode=_require(ocr_raw.get("mode", "rapidocr"), VALID_OCR_MODES, "extract.ocr.mode"),
    )
    batch = data.get("batch", {})
    guard = data.get("guardrails", {})
    det_raw = data.get("deterministic", {})
    title_mode = _require(det_raw.get("title_mode", "template"), VALID_TITLE_MODES,
                          "deterministic.title_mode")
    output_language = data.get("output_language", "english")
    return RunConfig(
        mode=mode,
        enrich=enrich,
        extract=extract,
        max_items=batch.get("max_items"),
        char_budget=int(batch.get("max_char_budget", 80000)),
        image_token_budget=int(batch.get("max_image_tokens", 120000)),
        guardrails_max_items_per_run=guard.get("max_items_per_run"),
        guardrails_max_spend_usd=guard.get("max_spend_usd"),
        deterministic_title_mode=title_mode,
        output_language=output_language,
    )
