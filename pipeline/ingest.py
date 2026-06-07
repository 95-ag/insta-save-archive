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
import os
import random
import time
from collections import defaultdict
from pathlib import Path

from playwright.sync_api import BrowserContext

from pipeline.config import Config
from pipeline.crawler import ALL_POSTS_SLUG, crawl_collection
from pipeline.discovery import discover_collections
from pipeline.extractor import extract_metadata_ytdlp, extract_post, minimal_metadata
from pipeline.extractor_deep import _netscape_cookies
from pipeline.notion import bulk_load_state, create_page, set_collections, update_metadata
from pipeline.reconcile import reconcile
from pipeline.snapshots import is_reusable, read_snapshot, write_snapshot

log = logging.getLogger(__name__)

_COLLECTIONS_FILE = Path(__file__).parent.parent / "config" / "collections.json"

# Metadata extraction throttle (yt-dlp). Stops calling yt-dlp after WALL_AFTER
# consecutive failures — guards against a rate-limit wall burning the whole run.
_THROTTLE_MIN, _THROTTLE_MAX = 2.0, 4.0
_WALL_AFTER = 5


def _extract_meta(url: str, cookies_txt: str, wall: dict, context) -> dict:
    """
    Metadata with throttle + wall guard. yt-dlp first (fast, image-capable, no
    browser); browser fallback when yt-dlp can't extract (e.g. image posts that
    error "There is no video in this post"). `wall` is a shared mutable
    {"consec": int, "hit": bool}; once hit, returns URL-only metadata without
    extracting (work deferred to the next run).
    """
    if wall["hit"]:
        return minimal_metadata(url)

    meta = extract_metadata_ytdlp(url, cookies_txt)
    if not meta.get("author") and context is not None:
        browser_meta = extract_post(context, url)
        if browser_meta.get("author"):
            log.info("ingest: browser fallback recovered %s", meta.get("source_id"))
            meta = browser_meta

    if meta.get("author"):
        wall["consec"] = 0
    else:
        wall["consec"] += 1
        if wall["consec"] >= _WALL_AFTER:
            wall["hit"] = True
            log.warning("ingest: %d consecutive metadata failures — extraction wall hit; "
                        "deferring remaining metadata to next run", _WALL_AFTER)
    time.sleep(random.uniform(_THROTTLE_MIN, _THROTTLE_MAX))
    return meta


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


def _apply_plan(context, config, plan, refresh_targets, cookies_txt, progress, dry_run: bool) -> None:
    """
    Stage 4 — create new pages (metadata via yt-dlp), retag changed pages, and
    backfill metadata on existing pages that are missing it (self-healing).
    """
    total = len(plan.creates) + len(plan.retags) + len(refresh_targets)
    bar = progress.add_bar("Apply", total=total)
    wall = {"consec": 0, "hit": False}

    # Retags first — pure Notion writes, no yt-dlp, fast.
    for action in plan.retags:
        progress.set_current("retag", action.source_id)
        if dry_run:
            progress.bump("would_update")
        else:
            try:
                set_collections(config, action.page_id, action.final)
                progress.bump("updated")
            except Exception as exc:
                log.error("apply: retag failed for %s — %s", action.source_id, exc)
                progress.bump("failed")
            time.sleep(config.notion_write_delay)
        progress.advance(bar)

    # Creates — always create the page (collections); attach metadata when yt-dlp succeeds.
    # Dedup relies on bulk_load_state being current (loaded this run); within one sync()
    # that holds. A crash + immediate re-run before Notion's query index catches up could
    # in theory double-create — acceptable for a single-user local tool.
    for action in plan.creates:
        progress.set_current("create", action.source_id)
        if dry_run:
            progress.bump("would_create")
            progress.advance(bar)
            continue
        try:
            metadata = _extract_meta(action.url, cookies_txt, wall, context)
            metadata["collections"] = sorted(action.final)
            create_page(config, metadata)
            progress.bump("created" if metadata.get("author") else "created_bare")
        except Exception as exc:
            log.error("apply: create failed for %s — %s", action.source_id, exc)
            progress.bump("failed")
        time.sleep(config.notion_write_delay)
        progress.advance(bar)

    # Refresh — backfill metadata on existing pages with missing author.
    for page_id, url, sid in refresh_targets:
        progress.set_current("refresh", sid)
        if dry_run:
            progress.bump("would_refresh")
            progress.advance(bar)
            continue
        meta = _extract_meta(url, cookies_txt, wall, context)
        if meta.get("author"):
            try:
                update_metadata(config, page_id, meta)
                progress.bump("refreshed")
            except Exception as exc:
                log.error("apply: refresh failed for %s — %s", sid, exc)
                progress.bump("failed")
            time.sleep(config.notion_write_delay)
        else:
            progress.bump("refresh_deferred")
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
        result = discover_collections(context, config, persist=not dry_run)
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

    # Self-healing: existing pages missing metadata (author empty) that we have a URL for.
    refresh_targets = [
        (st["page_id"], post_urls[sid], sid)
        for sid, st in notion_state.items()
        if st.get("needs_metadata") and sid in post_urls
    ]
    if refresh_targets:
        progress.log_line(f"{len(refresh_targets)} existing pages need metadata backfill")

    # Convert cookies once for yt-dlp (skip in dry-run — no extraction happens).
    cookies_txt = str(Path(config.tmp_dir) / "cookies.txt")
    if not dry_run and (plan.creates or refresh_targets):
        _netscape_cookies("session_cookies.json", cookies_txt)

    try:
        _apply_plan(context, config, plan, refresh_targets, cookies_txt, progress, dry_run)
    finally:
        try:
            os.unlink(cookies_txt)
        except FileNotFoundError:
            pass

    return {
        "collections": len(cols),
        "creates": len(plan.creates),
        "retags": len(plan.retags),
        "unchanged": plan.unchanged,
        "refresh": len(refresh_targets),
        "skipped_unsafe": len(plan.skipped_unsafe),
        "dry_run": dry_run,
    }
