"""
Summarize CLI — Claude Code pass.

Generates summary + externals for Extracted items: all content from transcript,
OCR, and caption rendered as clean prose — filler stripped, information preserved.
Highest priority first (High → Medium → Low → unprioritised).

--prepare fetches the highest-priority non-empty Extracted bucket up to a content
budget (total chars of transcript + OCR + caption) and a max item count. Both caps
are checked — whichever is hit first stops the batch. This keeps the prompt file
small enough for a single-pass Claude Code session without context compaction.

Workflow (repeat until no Extracted items remain):
  1. python scripts/summarize.py --prepare
  2. In Claude Code: "Read tmp/enrichment_prompt.txt and write results to tmp/enrichment_results.json"
  3. python scripts/summarize.py --upload

Each --prepare advances to the next bucket as items become Summarized.
tmp/ is gitignored — all intermediate files are local only.

Usage:
    python scripts/summarize.py --prepare
    python scripts/summarize.py --upload
"""

import json
import logging
import sys
from pathlib import Path

from pipeline.config import load_config
from pipeline.notion import get_page_content, query_by_status_and_priority, write_summary
from pipeline.observability import StageProgress, setup_logging
from pipeline.runner import PRIORITY_BUCKETS

log = logging.getLogger(__name__)

_TMP = Path("tmp")
_BATCH_FILE = _TMP / "enrichment_batch.json"
_PROMPT_FILE = _TMP / "enrichment_prompt.txt"
_RESULTS_FILE = _TMP / "enrichment_results.json"

# Maximum total chars of content (transcript + OCR + caption) per batch.
# Controls batch size for long items — many short ones hit _MAX_ITEMS first.
_CONTENT_BUDGET = 100_000

# Hard ceiling on item count per batch. Prevents many short items from accumulating
# enough per-item prompt overhead to blow out the Claude Code session context.
# Whichever limit is reached first stops the batch.
_MAX_ITEMS = 30


def _next_nonempty_bucket(config) -> tuple[str | None, list[dict]]:
    """
    Return (bucket_label, stubs) for the highest-priority Extracted bucket that has
    items: High → Medium → Low → unprioritised. Returns (None, []) if none remain.
    """
    for bucket in PRIORITY_BUCKETS:
        stubs = query_by_status_and_priority(config, "Extracted", bucket)
        if stubs:
            return bucket, stubs
    return None, []


def prepare() -> None:
    """
    Fetch the highest-priority non-empty Extracted bucket (High → Medium → Low →
    unprioritised) and write enrichment_batch.json + enrichment_prompt.txt for a
    Claude Code session. Each run advances to the next bucket as items become
    Summarized.
    """
    config = load_config()

    bucket, stubs = _next_nonempty_bucket(config)
    if not stubs:
        print("No Extracted items remain — nothing to prepare.")
        print("Run the title pass first if expected: python scripts/title.py")
        return

    label = bucket if bucket is not None else "Unprioritised"
    log.info("prepare: %d Extracted items in the %s bucket", len(stubs), label)

    items = []
    with StageProgress(f"Prepare · {label}") as progress:
        bar = progress.add_bar("Fetch", total=len(stubs))
        total_content = 0
        for stub in stubs:
            sid = stub.get("source_id", stub["page_id"])
            progress.set_current("fetch", sid)
            try:
                item = get_page_content(config, stub["page_id"])
                size = (
                    len(item.get("caption") or "") +
                    len(item.get("transcript") or "") +
                    len(item.get("ocr_text") or "")
                )
                if (total_content + size > _CONTENT_BUDGET or len(items) >= _MAX_ITEMS) and items:
                    # Budget reached — leave remaining for the next --prepare run
                    log.info("prepare: content budget reached at %d items (%d chars)", len(items), total_content)
                    progress.advance(bar)
                    break
                items.append(item)
                total_content += size
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

    _PROMPT_FILE.write_text(_build_prompt(label, items), encoding="utf-8")
    log.info("prepare: wrote prompt to %s", _PROMPT_FILE)

    total_chars = sum(
        len(i.get("caption") or "") + len(i.get("transcript") or "") + len(i.get("ocr_text") or "")
        for i in items
    )
    print(f"\nPrepared {len(items)} Extracted items from the {label} priority bucket ({total_chars:,} chars of content).")
    print(f"  Batch:  {_BATCH_FILE}")
    print(f"  Prompt: {_PROMPT_FILE}")
    print(f"\nNext: In a Claude Code session, say:")
    print(f'  "Read {_PROMPT_FILE} and write the results JSON to {_RESULTS_FILE}"')
    print(f"Then run: python scripts/summarize.py --upload")


def _build_prompt(label: str, items: list[dict]) -> str:
    """Build the Claude-ready prompt text from item content."""
    lines = [
        f"You are extracting content from {len(items)} Instagram posts (priority bucket: {label}).",
        "",
        "For each post, produce two fields:",
        "",
        "summary — Extract ALL useful information conveyed by this post as clean prose.",
        "  • Include every specific detail: steps, tips, tools, names, numbers, instructions.",
        "  • For carousel/OCR slides: consolidate the slide text into flowing prose.",
        "  • For video/reels: treat the transcript as content; render information, not narration.",
        "  • Strip filler: greetings, signoffs, 'see you next time', hashtag promos, conversational padding.",
        "  • Result should replace watching the video or reading the slides —",
        "    all information value, no medium noise.",
        "  • Use blank lines (two newlines) between distinct topic sections.",
        "    Do not write one continuous block of text — paragraph breaks aid readability.",
        "",
        "externals — Extract every external reference mentioned in the post.",
        "  Categories: Tools, Brands, Creators, Links, Techniques, Locations.",
        "  Format each line as:  [Category]  name — one-line context",
        "  Group by category with a header on its own line:",
        "    [Tools]",
        "      Figma — design tool used for wireframing",
        "    [Creators]",
        "      @millmotion — animation reference",
        "    [Links]",
        "      https://example.com — landing page mentioned in caption",
        "  Omit a category entirely if there are no references for it.",
        "  Include full URLs exactly as they appear — do not paraphrase or shorten links.",
        "  If nothing qualifies for any category, write an empty string.",
        "",
        "Return a JSON array written to tmp/enrichment_results.json. Each element:",
        '  {',
        '    "page_id": "<exact page_id from below>",',
        '    "source_id": "<exact source_id from below>",',
        '    "summary": "<full content extraction>",',
        '    "externals": "<grouped externals or empty string>"',
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
        if item.get("caption"):
            lines.append(f"Caption:    {item['caption']}")
        if item.get("transcript"):
            lines.append(f"Transcript: {item['transcript']}")
        if item.get("ocr_text"):
            lines.append(f"OCR text:   {item['ocr_text']}")
        lines.append("")

    return "\n".join(lines)


def upload() -> None:
    """
    Read tmp/enrichment_results.json (written by Claude) and write
    summary and externals to Notion for each item.

    Sets status → Summarized. Does NOT touch title.
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
            elif not item.get("summary"):
                log.warning("%s has no summary — skipping", sid)
                progress.bump("failed")
                failed += 1
            else:
                enrichment = {
                    "summary": item["summary"],
                }
                if item.get("externals"):
                    enrichment["externals"] = item["externals"]
                try:
                    write_summary(config, page_id, enrichment, config.enrichment_version)
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
        description="Claude Code enrichment — prepare the next priority bucket and upload results."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--prepare",
        action="store_true",
        help="Fetch the highest-priority non-empty Extracted bucket and write prompt + batch files.",
    )
    group.add_argument(
        "--upload",
        action="store_true",
        help="Read tmp/enrichment_results.json and write to Notion.",
    )
    args = parser.parse_args()

    if args.prepare:
        prepare()
    elif args.upload:
        upload()
