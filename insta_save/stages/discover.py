"""Discover stage (st.0) — producer. Crawl the saved index, refresh collections.json
(batch-confirm new/removed), then crawl every collection grid into snapshots that the
ingest stage consumes. Crawl-everything-first (deliberate: avoids interleaved
crawl+process)."""

import json
import logging
import os
import subprocess
from pathlib import Path

from insta_save.adapters.instagram.crawl import crawl_collection, discover_collections
from insta_save.config.collections import (
    load_collections, merge_discovered, write_collections, UNCATEGORIZED,
)
from insta_save.snapshots import is_reusable, read_snapshot, write_snapshot

log = logging.getLogger(__name__)


def refresh_collections_config(context, ig_username, *, collections_path, persist=True):
    """Index crawl → merge into collections.json. Returns (merged, new_names, missing, complete)."""
    discovered, complete = discover_collections(context, ig_username)
    p = Path(collections_path)
    if p.exists():
        load_collections(collections_path)  # validate: raises on a v1 flat-shape file
        existing = json.loads(p.read_text(encoding="utf-8"))
    else:
        existing = {"groups": [UNCATEGORIZED], "collections": {}}
    merged, new_names, missing = merge_discovered(existing, discovered)
    if persist:
        write_collections(merged, collections_path)
    return merged, new_names, missing, complete


def batch_confirm(collections_path, new_names) -> None:
    """Open $EDITOR on collections.json so the user sets group/extract for new entries
    in one pass, then validate on return. No-op when there are no new names."""
    if not new_names:
        return
    print(f"{len(new_names)} new collection(s) need group + extract: {', '.join(new_names)}")
    print(f"Opening {collections_path} — set 'group' and 'extract' for each, then save & close.")
    editor = os.environ.get("EDITOR", "nano")
    try:
        subprocess.run([editor, str(collections_path)], check=False)
    except FileNotFoundError:
        print(f"Editor {editor!r} not found. Set $EDITOR, then edit {collections_path} "
              f"to set group/extract for: {', '.join(new_names)}")
        return
    load_collections(collections_path)  # raises loudly on a broken file
    print("collections.json validated.")


def run_inline_select(collections_path, new_names, *, prompt_input=input) -> None:
    """In-terminal group+extract picker for new collections (alternative to batch_confirm).
    Prompts group (existing or a new name) + extract (y/n) per collection, writes the file.
    No-op when new_names is empty."""
    if not new_names:
        return
    p = Path(collections_path)
    data = json.loads(p.read_text(encoding="utf-8"))
    groups = list(data.get("groups", []))
    for name in new_names:
        print(f"\nCollection {name!r}. Existing groups: {', '.join(groups)}")
        group = prompt_input(f"  group for {name} (type an existing or a new name): ").strip()
        if not group:
            group = UNCATEGORIZED
        if group not in groups:
            groups.append(group)
        extract = prompt_input(f"  extract {name}? [y/N]: ").strip().lower() in ("y", "yes")
        entry = dict(data["collections"].get(name, {}))
        entry["group"] = group
        entry["extract"] = extract
        data["collections"][name] = entry
    data["groups"] = groups
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    load_collections(collections_path)  # validate; raises loudly on a broken file
    print("collections.json updated.")


def crawl_all(*, context, ig_username, collections_cfg, tmp_dir, crawl_fn=crawl_collection,
              fresh=False, names=None, max_age_min=360, now=None) -> list:
    """Crawl each collection grid → snapshot. Reuses complete+fresh snapshots unless
    `fresh`. `names` limits the set (None = all). Returns names skipped for missing ids."""
    targets = names or list(collections_cfg.collections)
    skipped = []
    for name in targets:
        meta = collections_cfg.collections.get(name, {})
        slug, numeric_id = meta.get("slug"), meta.get("numeric_id")
        if not slug or not numeric_id:
            log.warning("discover: %s missing slug/numeric_id — skipping (run index first)", name)
            skipped.append(name)
            continue
        if not fresh and is_reusable(read_snapshot(tmp_dir, slug), max_age_min, now=now):
            log.info("discover: reusing snapshot for %s", slug)
            continue
        posts, complete = crawl_fn(context, ig_username, slug, numeric_id)
        write_snapshot(tmp_dir, name=name, slug=slug, numeric_id=numeric_id,
                       posts=posts, complete=complete, now=now)
    return skipped


def run_discover(env, *, ig_username, collections_path, tmp_dir, headed=False,
                 fresh=False, names=None, max_age_min=360, persist=True,
                 select_mode="inline"):
    """Full discover: auth → index refresh → inline-select or editor → crawl all grids.

    select_mode: "inline" (default) prompts group+extract interactively per new collection;
                 "editor" opens $EDITOR on the whole file (legacy batch_confirm path).
    """
    if not ig_username:
        raise RuntimeError("discover: IG_USERNAME is not set (in .env) and no --ig-username "
                           "was given — cannot build collection URLs.")
    from playwright.sync_api import sync_playwright
    from insta_save.adapters.instagram.session import ensure_authenticated, prepare_display
    prepare_display(env)  # set DISPLAY before the driver freezes the env (headed re-auth needs it)
    with sync_playwright() as pw:
        browser, context = ensure_authenticated(pw, env, headless=not headed)
        try:
            merged, new_names, missing, complete = refresh_collections_config(
                context, ig_username, collections_path=collections_path, persist=persist)
            if persist:
                if select_mode == "editor":
                    batch_confirm(collections_path, new_names)
                else:
                    run_inline_select(collections_path, new_names)
            cfg = load_collections(collections_path)
            skipped = crawl_all(context=context, ig_username=ig_username, collections_cfg=cfg,
                                tmp_dir=tmp_dir, fresh=fresh, names=names, max_age_min=max_age_min)
        finally:
            browser.close()
    return {"new": new_names, "missing": missing, "index_complete": complete, "skipped": skipped}
