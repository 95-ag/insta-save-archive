"""
Ingest sync — discover collections, crawl them, and reconcile membership into Notion.

Default run discovers collections, crawls all, and syncs (create new posts; add/remove
collection tags for posts that moved). Safe by design: a tag is only removed when its
collection's crawl completed; whole-collection removal needs --confirm-removed.

Usage:
    python scripts/ingest_batch.py                         # full sync (discover + crawl + apply)
    python scripts/ingest_batch.py --dry-run               # compute plan, no Notion writes
    python scripts/ingest_batch.py --discover-only         # just refresh collections.json
    python scripts/ingest_batch.py --fresh                 # ignore snapshots, re-crawl all
    python scripts/ingest_batch.py --max-snapshot-age 60   # reuse snapshots younger than 60 min
    python scripts/ingest_batch.py --confirm-removed "Old Collection"   # allow stripping its tag
    python scripts/ingest_batch.py --headed                # visible browser (first login)
"""

import argparse

from playwright.sync_api import sync_playwright

from pipeline.config import load_config, validate_notion_config
from pipeline.discovery import discover_collections
from pipeline.ingest import sync
from pipeline.observability import StageProgress, setup_logging
from pipeline.session import ensure_authenticated


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest sync — collection-aware Instagram → Notion.")
    parser.add_argument("--headed", action="store_true", help="Visible browser (needed for first login).")
    parser.add_argument("--dry-run", action="store_true", help="Compute the plan; write nothing to Notion.")
    parser.add_argument("--discover-only", action="store_true", help="Refresh collections.json and exit.")
    parser.add_argument("--fresh", action="store_true", help="Ignore existing snapshots; re-crawl every collection.")
    parser.add_argument("--max-snapshot-age", type=int, default=360, metavar="MIN",
                        help="Reuse complete snapshots younger than this many minutes (default 360).")
    parser.add_argument("--confirm-removed", action="append", default=[], metavar="NAME",
                        help="Collection safe to strip from posts even without a complete crawl (repeatable).")
    args = parser.parse_args()

    log_path = setup_logging("ingest")
    config = load_config()
    validate_notion_config(config)

    with sync_playwright() as pw:
        browser, context = ensure_authenticated(pw, headless=not args.headed)
        try:
            if args.discover_only:
                with StageProgress("Discover") as progress:
                    result = discover_collections(context, config)
                    progress.log_line(
                        f"discovered {len(result.discovered)} "
                        f"({len(result.new_names)} new, {len(result.missing_names)} missing) "
                        f"· complete={result.complete}"
                    )
                    for n in result.new_names:
                        progress.bump("new")
                    progress.bump("discovered", len(result.discovered))
            else:
                title = "Ingest (dry-run)" if args.dry_run else "Ingest"
                with StageProgress(title) as progress:
                    summary = sync(
                        context, config,
                        progress=progress,
                        confirmed_removed=set(args.confirm_removed),
                        max_snapshot_age=args.max_snapshot_age,
                        fresh=args.fresh,
                        dry_run=args.dry_run,
                        discover=True,
                    )
                    progress.log_line(
                        f"collections={summary['collections']} · creates={summary['creates']} · "
                        f"retags={summary['retags']} · unchanged={summary['unchanged']} · "
                        f"skipped_unsafe={summary['skipped_unsafe']}"
                    )
        finally:
            browser.close()
            if args.headed:
                from pipeline.display import close_display
                close_display()

    print(f"\nlog: {log_path}")


if __name__ == "__main__":
    main()
