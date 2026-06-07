"""
Phase 3 enrichment CLI (Anthropic API).

Queries Enriched items from Notion (post-local Ollama pass), calls Claude via API
to generate expanded_summary, writes it back.

Title and extracted_externals are NOT touched — written by the local Ollama pass.
By default skips items that already have expanded_summary populated (idempotent).
Use --force to overwrite existing enrichment.

For Claude Code session-based enrichment (no API key needed), use:
  python scripts/run_enrichment_claude_code.py

Usage:
    python scripts/run_enrichment.py                              # all Enriched items
    python scripts/run_enrichment.py --collection "<NAME>"        # one collection in priority order
    python scripts/run_enrichment.py --limit 5                    # first 5 items
    python scripts/run_enrichment.py --source_id DYet7HfCwpj     # single item
    python scripts/run_enrichment.py --dry-run                    # print output, no writes
    python scripts/run_enrichment.py --force                      # overwrite existing enrichment
"""

import logging
import sys

from pipeline.collections import pilot_collections_by_enrichment_priority
from pipeline.config import load_config
from pipeline.enrich_claude import enrich_item, validate_enrichment_config
from pipeline.notion import (
    get_page_content,
    query_by_collection_and_status,
    query_by_source_id,
    query_by_status,
    write_enrichment,
)
from pipeline.observability import StageProgress, setup_logging

log = logging.getLogger(__name__)


def _collect_items(config, collection: str | None) -> list[dict]:
    """
    Return Enriched item stubs to process.

    If collection is given, queries that collection only (must be in priority list).
    Otherwise returns all Enriched items across the database.
    """
    if collection:
        priority_names = [c.name for c in pilot_collections_by_enrichment_priority()]
        if collection not in priority_names:
            log.warning(
                "_collect_items: %r not in enrichment priority list — querying anyway",
                collection,
            )
        return query_by_collection_and_status(config, collection, "Enriched")
    return query_by_status(config, "Enriched")


def run(
    limit: int | None = None,
    source_id: str | None = None,
    collection: str | None = None,
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
        items = _collect_items(config, collection)
        if limit:
            items = items[:limit]

    log.info("run_enrichment: %d items to process", len(items))

    title = "Claude enrichment (dry-run)" if dry_run else "Claude enrichment"
    with StageProgress(title) as progress:
        bar = progress.add_bar("Items", total=len(items))
        for item_stub in items:
            page_id = item_stub["page_id"]
            sid = item_stub.get("source_id", page_id)
            progress.set_current("enrich", sid)
            progress.bump(_enrich_one(config, page_id, sid, dry_run, force, progress))
            progress.advance(bar)


def _enrich_one(config, page_id, sid, dry_run, force, progress) -> str:
    """Process one item. Returns a counter name for the summary."""
    try:
        content = get_page_content(config, page_id)
    except Exception as exc:
        log.error("could not fetch content for %s — %s", sid, exc)
        return "failed"

    if not force and content.get("expanded_summary"):
        log.info("%s already has summary — skipping (use --force)", sid)
        return "skipped"

    try:
        result = enrich_item(config, content)
    except Exception as exc:
        log.error("enrichment call failed for %s — %s", sid, exc)
        return "failed"

    if dry_run:
        log.info("dry-run %s: summary=%r", sid, (result.get("expanded_summary") or "")[:300])
        progress.log_line(f"[{sid}] {(result.get('expanded_summary') or '')[:80]}…")
        return "would_enrich"

    try:
        write_enrichment(config, page_id, result, config.enrichment_version)
        log.info("wrote enrichment for %s", sid)
        return "enriched"
    except Exception as exc:
        log.error("failed to write enrichment for %s — %s", sid, exc)
        return "failed"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run Phase 3 AI enrichment (Anthropic API) on Enriched items."
    )
    parser.add_argument("--limit", type=int, metavar="N", help="Process at most N items.")
    parser.add_argument("--source_id", metavar="SHORTCODE", help="Enrich a single item by shortcode.")
    parser.add_argument("--collection", metavar="NAME", help="Enrich only items from this collection.")
    parser.add_argument("--dry-run", action="store_true", help="Print enrichment output without writing.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing enrichment fields.")
    args = parser.parse_args()

    log_path = setup_logging("enrichment")
    run(
        limit=args.limit,
        source_id=args.source_id,
        collection=args.collection,
        dry_run=args.dry_run,
        force=args.force,
    )
    print(f"\nlog: {log_path}")
