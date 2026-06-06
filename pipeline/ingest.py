"""
Ingest sync orchestrator — stages 0→4.

  0 Discover   refresh collections.json from the /saved/ index (additive)
  1 Crawl      each collection → durable snapshot (reuse fresh+complete ones)
  2 Load       Notion state once (bulk)
  3 Reconcile  pure diff with presence/absence safety gate
  4 Apply      create new pages; retag changed pages (idempotent)

Recovery rests on idempotent writes + durable snapshots, not an in-memory queue:
a crash loses at most the in-flight crawl, and re-running converges.
"""

import dataclasses
import json
import logging
import time
from collections import defaultdict
from pathlib import Path

from playwright.sync_api import BrowserContext

from pipeline.config import Config
from pipeline.crawler import ALL_POSTS_SLUG, crawl_collection
from pipeline.discovery import discover_collections
from pipeline.extractor import extract_post
from pipeline.notion import bulk_load_state, create_page, set_collections
from pipeline.reconcile import reconcile
from pipeline.snapshots import is_reusable, read_snapshot, write_snapshot

log = logging.getLogger(__name__)

_COLLECTIONS_FILE = Path(__file__).parent.parent / "config" / "collections.json"


def _load_collection_meta() -> dict:
    """{name: {slug, numeric_id}} from collections.json, excluding all-posts."""
    if not _COLLECTIONS_FILE.exists():
        return {}
    data = json.loads(_COLLECTIONS_FILE.read_text(encoding="utf-8"))
    return {
        name: meta
        for name, meta in data.items()
        if meta.get("slug") and meta.get("numeric_id") and meta["slug"] != ALL_POSTS_SLUG
    }


def _crawl_all(context, config, cols, max_snapshot_age, fresh, progress) -> list[dict]:
    """Stage 1 — crawl each collection (or reuse a fresh+complete snapshot)."""
    snapshots = []
    bar = progress.add_bar("Collections", total=len(cols))
    for name, meta in cols.items():
        slug, numeric_id = meta["slug"], meta["numeric_id"]
        existing = read_snapshot(slug)
        if not fresh and is_reusable(existing, max_snapshot_age):
            progress.set_current("reuse", name)
            snapshots.append(existing)
        else:
            progress.set_current("crawl", name)
            col_cfg = dataclasses.replace(config, target_collection=name)
            posts, complete = crawl_collection(context, col_cfg)
            write_snapshot(name, slug, numeric_id, posts, complete)
            snapshots.append({
                "collection": name, "slug": slug, "numeric_id": numeric_id,
                "complete": complete, "posts": posts,
            })
            if not complete:
                progress.bump("incomplete_crawls")
        progress.advance(bar)
    return snapshots


def _build_desired(snapshots: list[dict]) -> tuple[dict, dict, dict]:
    """Aggregate snapshots → (desired, post_urls, complete_map)."""
    desired: dict = defaultdict(set)
    post_urls: dict = {}
    complete_map: dict = {}
    for snap in snapshots:
        complete_map[snap["collection"]] = snap["complete"]
        for post in snap["posts"]:
            sid = post["shortcode"]
            desired[sid].add(snap["collection"])
            post_urls[sid] = post["url"]
    return dict(desired), post_urls, complete_map


def _apply_plan(context, config, plan, progress, dry_run: bool) -> None:
    """Stage 4 — create new pages, retag changed pages."""
    total = len(plan.creates) + len(plan.retags)
    bar = progress.add_bar("Apply", total=total)

    for action in plan.creates:
        progress.set_current("create", action.source_id)
        if dry_run:
            progress.bump("would_create")
            progress.advance(bar)
            continue
        try:
            metadata = extract_post(context, action.url)
            if not metadata.get("source_id"):
                log.warning("apply: no source_id for %s — skipping", action.url)
                progress.bump("failed")
            else:
                metadata["collections"] = sorted(action.final)
                create_page(config, metadata)
                progress.bump("created")
        except Exception as exc:
            log.error("apply: create failed for %s — %s", action.source_id, exc)
            progress.bump("failed")
        time.sleep(config.notion_write_delay)
        progress.advance(bar)

    for action in plan.retags:
        progress.set_current("retag", action.source_id)
        if dry_run:
            progress.bump("would_update")
            progress.advance(bar)
            continue
        try:
            set_collections(config, action.page_id, action.final)
            progress.bump("updated")
        except Exception as exc:
            log.error("apply: retag failed for %s — %s", action.source_id, exc)
            progress.bump("failed")
        time.sleep(config.notion_write_delay)
        progress.advance(bar)


def sync(
    context: BrowserContext,
    config: Config,
    *,
    progress,
    collection_names: list | None = None,
    confirmed_removed: set | None = None,
    max_snapshot_age: int = 360,
    fresh: bool = False,
    dry_run: bool = False,
    discover: bool = True,
) -> dict:
    """
    Run the ingest sync. Returns a summary dict.

    collection_names : restrict to these collections (None = all in collections.json).
    confirmed_removed: collection names safe to strip even without a complete crawl.
    discover         : run Stage 0 (refresh collections.json) first.
    """
    if discover:
        result = discover_collections(context, config)
        progress.log_line(
            f"discovered {len(result.discovered)} collections "
            f"({len(result.new_names)} new, {len(result.missing_names)} missing) "
            f"· complete={result.complete}"
        )
        if result.missing_names:
            progress.log_line(f"[yellow]missing this crawl:[/yellow] {result.missing_names}")

    all_cols = _load_collection_meta()
    if collection_names is not None:
        cols = {n: all_cols[n] for n in collection_names if n in all_cols}
    else:
        cols = all_cols

    snapshots = _crawl_all(context, config, cols, max_snapshot_age, fresh, progress)
    desired, post_urls, complete_map = _build_desired(snapshots)

    notion_state = bulk_load_state(config)
    plan = reconcile(desired, post_urls, notion_state, complete_map, confirmed_removed)

    if plan.skipped_unsafe:
        progress.log_line(
            f"[yellow]{len(plan.skipped_unsafe)} unsafe removals skipped[/yellow] "
            "(incomplete crawl / not --confirm-removed) — see log"
        )
        for s in plan.skipped_unsafe:
            log.info("skipped unsafe removal: %s from %s (%s)",
                     s["collection"], s["source_id"], s["reason"])

    _apply_plan(context, config, plan, progress, dry_run)

    return {
        "collections": len(cols),
        "creates": len(plan.creates),
        "retags": len(plan.retags),
        "unchanged": plan.unchanged,
        "skipped_unsafe": len(plan.skipped_unsafe),
        "dry_run": dry_run,
    }
