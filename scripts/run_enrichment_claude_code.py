"""
Phase 3b — Claude Code enrichment CLI.

Generates expanded_summary and key_insights for priority collections.
Runs as a manual, collection-by-collection Claude Code session.
Title and extracted_externals are NOT touched — written by the local Ollama pass.

Workflow (repeat for each priority collection):
  1. python scripts/run_enrichment_claude_code.py --list-priority
  2. python scripts/run_enrichment_claude_code.py --prepare --collection "<NAME>"
  3. In Claude Code: "Read tmp/enrichment_prompt.txt and write results to tmp/enrichment_results.json"
  4. python scripts/run_enrichment_claude_code.py --upload

tmp/ is gitignored — all intermediate files are local only.

Usage:
    python scripts/run_enrichment_claude_code.py --list-priority
    python scripts/run_enrichment_claude_code.py --prepare --collection "<NAME>"
    python scripts/run_enrichment_claude_code.py --upload
"""

import json
import logging
import sys
from pathlib import Path

from pipeline.collections import pilot_collections_by_enrichment_priority
from pipeline.config import load_config
from pipeline.notion import get_page_content, query_by_collection_and_status, write_enrichment
from pipeline.observability import StageProgress, setup_logging

log = logging.getLogger(__name__)

_TMP = Path("tmp")
_BATCH_FILE = _TMP / "enrichment_batch.json"
_PROMPT_FILE = _TMP / "enrichment_prompt.txt"
_RESULTS_FILE = _TMP / "enrichment_results.json"


def list_priority() -> None:
    """Print priority collections in ascending enrichment_order."""
    collections = pilot_collections_by_enrichment_priority()
    if not collections:
        print("No collections have enrichment_order set in config/collections.json.")
        print("Add \"enrichment_order\": N (integer, 1 = first) to each priority entry.")
        return
    print("Collections in Claude enrichment order:")
    for i, c in enumerate(collections, 1):
        print(f"  {i}. {c.name}  (group: {c.group})")


def prepare(collection_name: str) -> None:
    """
    Query Enriched items for one collection; write enrichment_batch.json and
    enrichment_prompt.txt ready for a Claude Code session.

    Fails if the collection is not in the priority list or has no Enriched items.
    """
    config = load_config()

    priority_names = [c.name for c in pilot_collections_by_enrichment_priority()]
    if collection_name not in priority_names:
        print(f"ERROR: {collection_name!r} not in priority list — "
              "add enrichment_order to its entry in config/collections.json.")
        sys.exit(1)

    log.info("prepare: querying Enriched items for %r", collection_name)
    stubs = query_by_collection_and_status(config, collection_name, "Enriched")
    if not stubs:
        print(f"No Enriched items found for {collection_name!r}.")
        print("Run the local pass first: python scripts/run_enrichment_local.py")
        return

    items = []
    with StageProgress(f"Prepare · {collection_name}") as progress:
        bar = progress.add_bar("Fetch", total=len(stubs))
        for stub in stubs:
            sid = stub.get("source_id", stub["page_id"])
            progress.set_current("fetch", sid)
            try:
                items.append(get_page_content(config, stub["page_id"]))
                progress.bump("fetched")
            except Exception as exc:
                log.error("could not fetch %s — %s", sid, exc)
                progress.bump("failed")
            progress.advance(bar)

    if not items:
        print("ERROR: no items could be fetched — aborting")
        sys.exit(1)

    _TMP.mkdir(exist_ok=True)
    _BATCH_FILE.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("prepare: wrote batch to %s", _BATCH_FILE)

    _PROMPT_FILE.write_text(_build_prompt(collection_name, items), encoding="utf-8")
    log.info("prepare: wrote prompt to %s", _PROMPT_FILE)

    print(f"\nPrepared {len(items)} Enriched items for {collection_name!r}.")
    print(f"  Batch:  {_BATCH_FILE}")
    print(f"  Prompt: {_PROMPT_FILE}")
    print(f"\nNext: In a Claude Code session, say:")
    print(f'  "Read {_PROMPT_FILE} and write the results JSON to {_RESULTS_FILE}"')
    print(f"Then run: python scripts/run_enrichment_claude_code.py --upload")


def _build_prompt(collection_name: str, items: list[dict]) -> str:
    """Build the Claude-ready prompt text from item content."""
    lines = [
        f"You are enriching {len(items)} Instagram posts from the collection '{collection_name}'.",
        "",
        "For each post, write:",
        "  expanded_summary — 2-4 paragraphs. Enough to replace rewatching.",
        "    Capture the method, tools used, step-by-step reasoning, and specific details.",
        "  key_insights — 3-7 transferable, actionable principles.",
        "    Reusable ideas, not a recap. Each insight must stand alone.",
        "",
        "Return a JSON array written to tmp/enrichment_results.json. Each element:",
        '  {',
        '    "page_id": "<exact page_id from below>",',
        '    "source_id": "<exact source_id from below>",',
        '    "expanded_summary": "<2-4 paragraphs>",',
        '    "key_insights": ["<insight 1>", "<insight 2>", ...]',
        '  }',
        "",
        "Include ALL items below — one result object per item.",
        "",
        "=" * 60,
        "",
    ]

    for item in items:
        sid = item.get("source_id", item["page_id"])
        lines.append(f"--- {sid} ---")
        lines.append(f"page_id:    {item['page_id']}")
        lines.append(f"source_id:  {sid}")
        lines.append(f"Title:      {item.get('title') or '[none]'}")
        lines.append(f"Author:     {item.get('author') or '[none]'}")
        lines.append(f"Type:       {item.get('type') or '[none]'}")
        caption = (item.get("caption") or "[none]")[:600]
        lines.append(f"Caption:    {caption}")
        if item.get("transcript"):
            lines.append(f"Transcript: {item['transcript'][:3000]}")
        if item.get("ocr_text"):
            lines.append(f"OCR text:   {item['ocr_text'][:1500]}")
        lines.append("")

    return "\n".join(lines)


def upload() -> None:
    """
    Read tmp/enrichment_results.json (written by Claude) and write
    expanded_summary + key_insights to Notion for each item.

    Sets pipeline_status → Summarised. Does NOT touch title or extracted_externals.
    Cleans up tmp files on full success.
    """
    if not _RESULTS_FILE.exists():
        print(f"ERROR: {_RESULTS_FILE} not found. In Claude Code say: "
              "'Read tmp/enrichment_prompt.txt and write results to tmp/enrichment_results.json'")
        sys.exit(1)

    try:
        results = json.loads(_RESULTS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: could not parse {_RESULTS_FILE} — {exc}")
        sys.exit(1)

    if not isinstance(results, list):
        print(f"ERROR: expected a JSON array in {_RESULTS_FILE}, got {type(results).__name__}")
        sys.exit(1)

    config = load_config()
    written = failed = 0

    with StageProgress("Upload enrichment") as progress:
        bar = progress.add_bar("Items", total=len(results))
        for item in results:
            page_id = item.get("page_id")
            sid = item.get("source_id", page_id)
            progress.set_current("upload", sid or "?")

            if not page_id:
                log.error("item missing page_id — %s", item)
                progress.bump("failed")
                failed += 1
            elif not item.get("expanded_summary"):
                log.warning("%s has no expanded_summary — skipping", sid)
                progress.bump("failed")
                failed += 1
            else:
                enrichment = {
                    "expanded_summary": item["expanded_summary"],
                    "key_insights": item.get("key_insights") or [],
                }
                try:
                    write_enrichment(config, page_id, enrichment, config.enrichment_version)
                    log.info("wrote enrichment for %s", sid)
                    progress.bump("written")
                    written += 1
                except Exception as exc:
                    log.error("failed for %s — %s", sid, exc)
                    progress.bump("failed")
                    failed += 1
            progress.advance(bar)

    if failed == 0 and written > 0:
        for path in [_BATCH_FILE, _PROMPT_FILE, _RESULTS_FILE]:
            if path.exists():
                path.unlink()
        print("Cleaned up tmp files.")


if __name__ == "__main__":
    import argparse

    setup_logging("enrichment-claude")
    parser = argparse.ArgumentParser(
        description="Claude Code enrichment — prepare batches and upload results for priority collections."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list-priority",
        action="store_true",
        help="List collections in enrichment_order.",
    )
    group.add_argument(
        "--prepare",
        action="store_true",
        help="Query Enriched items for --collection and write prompt + batch files.",
    )
    group.add_argument(
        "--upload",
        action="store_true",
        help="Read tmp/enrichment_results.json and write to Notion.",
    )
    parser.add_argument(
        "--collection",
        metavar="NAME",
        help="Collection name (required for --prepare).",
    )
    args = parser.parse_args()

    if args.list_priority:
        list_priority()
    elif args.prepare:
        if not args.collection:
            parser.error("--prepare requires --collection NAME")
        prepare(args.collection)
    elif args.upload:
        upload()
