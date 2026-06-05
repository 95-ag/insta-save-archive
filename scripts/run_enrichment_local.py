"""
Local enrichment CLI — title and extracted_externals via Ollama.

Per-item processing: Notion READ → Ollama → Notion WRITE.
Interrupt-safe: re-run skips items whose title is no longer a placeholder.

A placeholder title matches: "{author} — {shortcode}" (set in Phase 1).

Usage:
    python run_enrichment_local.py              # all Expanded without real title
    python run_enrichment_local.py --limit 5
    python run_enrichment_local.py --source_id DYet7HfCwpj
    python run_enrichment_local.py --dry-run
    python run_enrichment_local.py --force      # overwrite existing title/externals
"""

import logging
import re
import sys

from pipeline.config import load_config
from pipeline.enrich_local import enrich_local, validate_local_enrichment_config
from pipeline.notion import get_page_content, query_by_source_id, query_by_status, write_local_enrichment

log = logging.getLogger(__name__)

# Phase 1 placeholder format: "{author} — {shortcode}"
_PLACEHOLDER_RE = re.compile(r"^.+ — [A-Za-z0-9_-]+$")


def _is_placeholder(title: str | None) -> bool:
    if not title:
        return True
    return bool(_PLACEHOLDER_RE.match(title))


def run(
    limit: int | None = None,
    source_id: str | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> None:
    config = load_config()
    validate_local_enrichment_config(config)

    if source_id:
        page_id = query_by_source_id(config, source_id)
        if not page_id:
            log.error("run_enrichment_local: source_id %r not found", source_id)
            sys.exit(1)
        items = [{"page_id": page_id, "source_id": source_id}]
    else:
        items = query_by_status(config, "Expanded")
        if limit:
            items = items[:limit]

    log.info("run_enrichment_local: %d items to process", len(items))
    enriched = skipped = failed = 0

    for i, stub in enumerate(items, 1):
        page_id = stub["page_id"]
        sid = stub.get("source_id", page_id)
        log.info("run_enrichment_local: [%d/%d] %s", i, len(items), sid)

        try:
            content = get_page_content(config, page_id)
        except Exception as exc:
            log.error("run_enrichment_local: fetch failed for %s — %s", sid, exc)
            failed += 1
            continue

        if not force and not _is_placeholder(content.get("title")):
            log.info("run_enrichment_local: %s already has real title — skipping", sid)
            skipped += 1
            continue

        try:
            result = enrich_local(config, content)
        except Exception as exc:
            log.error("run_enrichment_local: Ollama failed for %s — %s", sid, exc)
            failed += 1
            continue

        if dry_run:
            print(f"\n--- {sid} ---")
            print(f"Status:    Expanded → Enriched")
            print(f"Title:     {result.get('title')}")
            print(f"Externals:\n{result.get('extracted_externals')}")
            enriched += 1
            continue

        try:
            write_local_enrichment(
                config, page_id,
                title=result["title"],
                extracted_externals=result["extracted_externals"],
            )
            log.info("run_enrichment_local: enriched %s", sid)
            enriched += 1
        except Exception as exc:
            log.error("run_enrichment_local: write failed for %s — %s", sid, exc)
            failed += 1

    action = "dry-run" if dry_run else "enriched"
    log.info("run_enrichment_local: done — %s=%d skipped=%d failed=%d", action, enriched, skipped, failed)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    parser = argparse.ArgumentParser(description="Local Ollama enrichment: title + extracted_externals.")
    parser.add_argument("--limit", type=int, metavar="N")
    parser.add_argument("--source_id", metavar="SHORTCODE")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Overwrite existing title/externals.")
    args = parser.parse_args()
    run(limit=args.limit, source_id=args.source_id, dry_run=args.dry_run, force=args.force)
