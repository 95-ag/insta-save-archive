"""Notion -> JSON snapshot (safety net before the #7 capstone wipe). `ts` is passed in
(never generated here) so the writer is deterministic and unit-testable."""
import json
from pathlib import Path

from insta_save.adapters.notion import query_all_pages


def backup(env, *, out_dir, ts) -> Path:
    pages = query_all_pages(env)
    out = Path(out_dir) / f"notion-{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"snapshot_ts": ts, "count": len(pages), "pages": pages},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    return out
