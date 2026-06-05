"""
Phase 3 enrichment CLI.

Queries Expanded items from Notion, calls Claude to generate enrichment fields,
writes title / expanded_summary / key_insights / extracted_externals back.

By default skips items that already have expanded_summary populated (idempotent).
Use --force to overwrite existing enrichment.

Usage:
    python run_enrichment.py                          # all Expanded, skip enriched
    python run_enrichment.py --limit 5               # first 5 items
    python run_enrichment.py --source_id DYet7HfCwpj # single item
    python run_enrichment.py --dry-run               # print output, no writes
    python run_enrichment.py --force                 # overwrite existing enrichment
"""

import logging
import sys

from pipeline.config import load_config
from pipeline.enrich_claude import enrich_item, validate_enrichment_config
from pipeline.notion import get_page_content, query_by_source_id, query_by_status, write_enrichment

log = logging.getLogger(__name__)


def run(
    limit: int | None = None,
    source_id: str | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> None:
    config = load_config()
    validate_enrichment_config(config)

    if source_id:
        page_id = query_by_source_id(config, source_id)
        if not page_id:
            log.error("run_enrichment: source_id %r not found in Notion", source_id)
            sys.exit(1)
        items = [{"page_id": page_id, "source_id": source_id}]
    else:
        items = query_by_status(config, "Expanded")
        if limit:
            items = items[:limit]

    log.info("run_enrichment: %d items to process", len(items))
    enriched = skipped = failed = 0

    for i, item_stub in enumerate(items, 1):
        page_id = item_stub["page_id"]
        sid = item_stub.get("source_id", page_id)
        log.info("run_enrichment: [%d/%d] %s", i, len(items), sid)

        try:
            content = get_page_content(config, page_id)
        except Exception as exc:
            log.error("run_enrichment: could not fetch content for %s — %s", sid, exc)
            failed += 1
            continue

        if not force and content.get("expanded_summary"):
            log.info(
                "run_enrichment: %s already enriched — skipping (use --force to overwrite)", sid
            )
            skipped += 1
            continue

        try:
            result = enrich_item(config, content)
        except Exception as exc:
            log.error("run_enrichment: enrichment call failed for %s — %s", sid, exc)
            failed += 1
            continue

        if dry_run:
            print(f"\n--- {sid} ---")
            print(f"Title:     {result.get('title')}")
            print(f"Summary:   {(result.get('expanded_summary') or '')[:300]}...")
            print(f"Insights:  {result.get('key_insights')}")
            print(f"Externals: {(result.get('extracted_externals') or '')[:200]}...")
            enriched += 1
            continue

        try:
            write_enrichment(config, page_id, result, config.enrichment_version)
            log.info("run_enrichment: enriched %s", sid)
            enriched += 1
        except Exception as exc:
            log.error("run_enrichment: failed to write enrichment for %s — %s", sid, exc)
            failed += 1

    action = "dry-run enriched" if dry_run else "enriched"
    log.info(
        "run_enrichment: done — %s=%d skipped=%d failed=%d",
        action, enriched, skipped, failed,
    )


if __name__ == "__main__":
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Run Phase 3 AI enrichment on Expanded items.")
    parser.add_argument("--limit", type=int, metavar="N", help="Process at most N items.")
    parser.add_argument("--source_id", metavar="SHORTCODE", help="Enrich a single item by shortcode.")
    parser.add_argument("--dry-run", action="store_true", help="Print enrichment output without writing.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing enrichment fields.")
    args = parser.parse_args()
    run(limit=args.limit, source_id=args.source_id, dry_run=args.dry_run, force=args.force)
