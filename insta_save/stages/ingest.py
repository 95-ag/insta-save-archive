"""Ingest stage (st.1) — consumer. Read discover's snapshots, reconcile against Notion,
extract metadata for new posts (yt-dlp→browser, wall guard), apply creates/retags/
backfills. Everything lands Imported. Reconcile's safety invariants are honored."""

import logging
import os
import random
import time
from pathlib import Path

from insta_save.adapters import notion
from insta_save.adapters.instagram.cookies import json_cookies_to_netscape
from insta_save.adapters.instagram.extractor import extract_metadata_ytdlp, extract_post, minimal_metadata
from insta_save.reconcile import reconcile
from insta_save.snapshots import read_snapshot

log = logging.getLogger(__name__)

_WALL_AFTER = 5
_THROTTLE_MIN, _THROTTLE_MAX = 2.0, 4.0


def build_reconcile_inputs(tmp_dir, collections_cfg, names=None):
    """Assemble (desired{sid:set}, post_urls{sid:url}, complete_map{collection:bool})
    from the per-collection snapshots."""
    desired, urls, complete = {}, {}, {}
    targets = names or list(collections_cfg.collections)
    for name in targets:
        meta = collections_cfg.collections.get(name, {})
        slug = meta.get("slug")
        snap = read_snapshot(tmp_dir, slug) if slug else None
        if snap is None:
            continue
        complete[name] = snap.get("complete", False)
        for post in snap.get("posts", []):
            sid = post["shortcode"]
            desired.setdefault(sid, set()).add(name)
            urls.setdefault(sid, post["url"])
    return desired, urls, complete


def _meta_for(env, url, wall, context) -> dict:
    """Metadata with throttle + wall guard. yt-dlp first; browser fallback on no-author."""
    if wall["hit"]:
        return minimal_metadata(url)
    meta = extract_metadata_ytdlp(url, wall["cookies_txt"])
    if not meta.get("author") and context is not None:
        bm = extract_post(context, url)
        if bm.get("author"):
            meta = bm
    if meta.get("author"):
        wall["consec"] = 0
    else:
        wall["consec"] += 1
        if wall["consec"] >= _WALL_AFTER:
            wall["hit"] = True
            log.warning("ingest: %d consecutive metadata failures — wall hit; deferring", _WALL_AFTER)
    time.sleep(random.uniform(_THROTTLE_MIN, _THROTTLE_MAX))
    return meta


def apply_plan(*, env, plan, context, cookies_txt, refresh_targets, dry_run, progress=None):
    """Execute the reconcile Plan: create new pages (with metadata), retag existing,
    backfill metadata. wall guard shared across all extractions this run."""
    wall = {"consec": 0, "hit": False, "cookies_txt": cookies_txt}
    created = retagged = backfilled = degraded = 0

    bar = None
    if progress is not None:
        total = len(plan.creates) + len(plan.retags) + len(refresh_targets)
        bar = progress.add_bar("Ingesting", total=total)

    for action in plan.creates:
        if dry_run:
            created += 1
            if progress is not None:
                progress.advance(bar)
            continue
        meta = _meta_for(env, action.url, wall, context)
        meta["source_id"] = action.source_id
        meta["ig_link"] = action.url
        meta["collections"] = sorted(action.final)
        if not meta.get("author"):
            degraded += 1
            log.warning("ingest: created %s with no author (extraction wall/failure) — "
                        "will backfill next run", action.source_id)
        notion.create_page(env, meta)
        created += 1
        if progress is not None:
            progress.advance(bar)
    for action in plan.retags:
        if not dry_run:
            notion.set_collections(env, action.page_id, action.final)
        retagged += 1
        if progress is not None:
            progress.advance(bar)
    for page_id, url, sid in refresh_targets:
        if dry_run:
            backfilled += 1
            if progress is not None:
                progress.advance(bar)
            continue
        meta = _meta_for(env, url, wall, context)
        if meta.get("author"):
            meta["source_id"] = sid
            meta["ig_link"] = url
            notion.update_metadata(env, page_id, meta)
            backfilled += 1
        else:
            log.warning("ingest: backfill skipped for %s — no author extracted", sid)
        if progress is not None:
            progress.advance(bar)
    return {"created": created, "retagged": retagged, "backfilled": backfilled,
            "degraded": degraded, "unchanged": plan.unchanged,
            "skipped_unsafe": len(plan.skipped_unsafe)}


def run_ingest(env, *, collections_cfg, names=None, confirmed_removed=None,
               headed=False, dry_run=False, progress=None) -> dict:
    """Full ingest: snapshots → reconcile → apply. Opens a browser only if there are
    creates/backfills needing the browser fallback."""
    from playwright.sync_api import sync_playwright
    from insta_save.adapters.instagram.session import ensure_authenticated

    desired, urls, complete = build_reconcile_inputs(env.tmp_dir, collections_cfg, names)
    state = notion.bulk_load_state(env)
    plan = reconcile(desired, urls, state, complete, confirmed_removed or set())
    refresh_targets = [(st["page_id"], urls[sid], sid)
                       for sid, st in state.items()
                       if st.get("needs_metadata") and sid in urls]

    if plan.skipped_unsafe:
        log.info("ingest: %d unsafe removals skipped (incomplete crawl / not confirmed)",
                 len(plan.skipped_unsafe))

    if dry_run:
        return apply_plan(env=env, plan=plan, context=None, cookies_txt="",
                          refresh_targets=refresh_targets, dry_run=True, progress=progress)
    if not (plan.creates or refresh_targets):
        # Retag-only: no metadata extraction → no browser/cookies needed, but these ARE
        # real writes (set_collections), so dry_run stays False.
        return apply_plan(env=env, plan=plan, context=None, cookies_txt="",
                          refresh_targets=[], dry_run=False, progress=progress)

    cookies_txt = json_cookies_to_netscape(env.cookies_file, Path(env.tmp_dir) / "cookies.txt")
    with sync_playwright() as pw:
        browser, context = ensure_authenticated(pw, env, headless=not headed)
        try:
            result = apply_plan(env=env, plan=plan, context=context, cookies_txt=cookies_txt,
                                refresh_targets=refresh_targets, dry_run=False, progress=progress)
        finally:
            browser.close()
            try:
                os.unlink(cookies_txt)
            except OSError:
                pass
    return result
