# insta_save/backends/local_ollama.py
"""local enrich backend — automated, in-process via Ollama constrained decoding.
Full-enrich-capable (title+summary+externals+tags); on qwen2.5:7b realistic quality
is title-grade (D8) but no field is structurally withheld. Batching = single
(per-item); results.json is checkpointed every CHECKPOINT items so a crash resumes."""

import json
import logging
from pathlib import Path

from insta_save.backends.base import Budgets, FillResult

log = logging.getLogger(__name__)

NAME = "local"; AUTOMATED = True; VISION_CAPABLE = False
CHECKPOINT = 25

# JSON schema for constrained decoding — mirrors enrich_schema.RESULT_FIELDS.
_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "page_id": {"type": "string"}, "source_id": {"type": "string"},
        "title": {"type": "string"}, "summary": {"type": "string"},
        "externals": {"type": "string"},
        "content_type": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["page_id", "title", "summary", "content_type", "topics"],
}


def _client():
    import ollama
    return ollama


def batch_budgets(run_cfg) -> Budgets:
    # single strategy: no char cap (per-item calls), no image budget (text-only).
    return Budgets(char_budget=10**9, max_items=None, image_token_budget=None)


def _item_prompt(item) -> str:
    lines = [f"page_id: {item['page_id']}", f"source_id: {item.get('source_id') or ''}"]
    for k in ("author", "collections", "caption", "transcript", "ocr_text"):
        v = item.get(k)
        if v:
            lines.append(f"{k}: {v}")
    return "\n".join(lines)


def fill(env, run_cfg, enrich_dir) -> FillResult:
    d = Path(enrich_dir)
    batch = json.loads((d / "batch.json").read_text(encoding="utf-8"))
    results_file = d / "results.json"
    done = {}
    if results_file.exists():
        for r in json.loads(results_file.read_text(encoding="utf-8")):
            done[r.get("page_id")] = r
    model = run_cfg.enrich.model
    client = _client()
    header = (d / "prompt.txt").read_text(encoding="utf-8") if (d / "prompt.txt").exists() else ""
    filled = failed = 0
    out = list(done.values())
    for item in batch["items"]:
        if item["page_id"] in done:
            continue
        try:
            resp = client.chat(model=model,
                               messages=[{"role": "user",
                                          "content": header + "\n\n" + _item_prompt(item)}],
                               format=_RESULT_SCHEMA)
            obj = json.loads(resp["message"]["content"])
            obj["page_id"] = item["page_id"]            # never trust the model for identity
            obj["source_id"] = item.get("source_id")
            out.append(obj); filled += 1
        except Exception as exc:
            log.error("local fill failed %s — %s", item.get("source_id") or item["page_id"], exc)
            failed += 1
        if filled and filled % CHECKPOINT == 0:
            results_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    results_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("local fill: %d filled, %d failed", filled, failed)
    return FillResult(filled=filled, failed=failed)
