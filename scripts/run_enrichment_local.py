"""
Local enrichment CLI — title and extracted_externals via Ollama.

Per-item processing: Notion READ → Ollama → Notion WRITE.
Interrupt-safe: re-run skips items whose title is no longer a placeholder.

A placeholder title matches: "{author} — {shortcode}" (set in Phase 1).

Clean terminal = live progress bar; full detail → logs/enrichment-local_<ts>.log.

Usage:
    python scripts/run_enrichment_local.py              # all Expanded without real title
    python scripts/run_enrichment_local.py --limit 5
    python scripts/run_enrichment_local.py --source_id DYet7HfCwpj
    python scripts/run_enrichment_local.py --dry-run
    python scripts/run_enrichment_local.py --force      # overwrite existing title/externals
"""

import logging
import re
import sys

from pipeline.config import load_config
from pipeline.enrich_local import enrich_local, validate_local_enrichment_config
from pipeline.notion import get_page_content, query_by_source_id, query_by_status, write_local_enrichment
from pipeline.observability import StageProgress, setup_logging

log = logging.getLogger(__name__)

# Phase 1 placeholder format: "{author} — {shortcode}"
_PLACEHOLDER_RE = re.compile(r"^.+ — [A-Za-z0-9_-]+$")


def _is_placeholder(title: str | None) -> bool:
    if not title:
        return True
    return bool(_PLACEHOLDER_RE.match(title))


def _enrich_one(config, stub, dry_run, force, progress) -> str:
    """Process one item. Returns a counter name for the summary."""
    page_id = stub["page_id"]
    sid = stub.get("source_id", page_id)

    try:
        content = get_page_content(config, page_id)
    except Exception as exc:
        log.error("fetch failed for %s — %s", sid, exc)
        return "failed"

    if not force and not _is_placeholder(content.get("title")):
        log.info("%s already has real title — skipping", sid)
        return "skipped"

    try:
        result = enrich_local(config, content)
    except Exception as exc:
        log.error("Ollama failed for %s — %s", sid, exc)
        return "failed"

    if dry_run:
        log.info("dry-run %s: title=%r externals=%r",
                 sid, result.get("title"), result.get("extracted_externals"))
        progress.log_line(f"[{sid}] {result.get('title')!r}")
        return "would_enrich"

    try:
        write_local_enrichment(
            config, page_id,
            title=result["title"],
            extracted_externals=result["extracted_externals"],
        )
        log.info("enriched %s", sid)
        return "enriched"
    except Exception as exc:
        log.error("write failed for %s — %s", sid, exc)
        return "failed"


def run(limit=None, source_id=None, dry_run=False, force=False) -> None:
    config = load_config()
    validate_local_enrichment_config(config)

    if source_id:
        page_id = query_by_source_id(config, source_id)
        if not page_id:
            log.error("source_id %r not found", source_id)
            sys.exit(1)
        items = [{"page_id": page_id, "source_id": source_id}]
    else:
        items = query_by_status(config, "Expanded")
        if limit:
            items = items[:limit]

    log.info("run_enrichment_local: %d items to process", len(items))

    title = "Local enrichment (dry-run)" if dry_run else "Local enrichment"
    with StageProgress(title) as progress:
        bar = progress.add_bar("Items", total=len(items))
        for stub in items:
            progress.set_current("enrich", stub.get("source_id", stub["page_id"]))
            progress.bump(_enrich_one(config, stub, dry_run, force, progress))
            progress.advance(bar)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Local Ollama enrichment: title + extracted_externals.")
    parser.add_argument("--limit", type=int, metavar="N")
    parser.add_argument("--source_id", metavar="SHORTCODE")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Overwrite existing title/externals.")
    args = parser.parse_args()

    log_path = setup_logging("enrichment-local")
    run(limit=args.limit, source_id=args.source_id, dry_run=args.dry_run, force=args.force)
    print(f"\nlog: {log_path}")
