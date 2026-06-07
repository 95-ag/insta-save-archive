"""
Notion API client for all pipeline stages.

Exposes:
  query_by_source_id       — deduplication check before any write
  create_page              — write one Imported row
  mark_failed              — set status=Failed and write failure_notes
  query_by_status          — return all pages matching a status value
  query_by_status_and_priority — paginated query with priority bucket filter
  write_extraction         — write Extracted-stage results; appends raw_extraction
                             under a version key, never overwrites prior versions
  write_title              — write generated title (does not change status)
  write_summary            — write summary + externals; sets status=Summarized

Validates Notion credentials on first use via validate_notion_config().
All writes respect config.notion_write_delay to stay within API rate limits.
"""

import json
import logging
import time

from notion_client import Client
from notion_client.errors import APIResponseError

from pipeline.config import Config, validate_notion_config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Property builders — Notion API payload helpers
# ---------------------------------------------------------------------------

def _notion_truncate(text: str, limit: int = 2000) -> str:
    """
    Truncate text to Notion's 2000 UTF-16 code unit limit.

    Notion (and JavaScript) count characters as UTF-16 code units.
    Characters above U+FFFF (emoji, some symbols) consume 2 units each.
    Python's len() / slicing counts code points — so text[:2000] can exceed
    2000 Notion units if the text contains non-BMP characters (e.g. emoji).
    """
    units = 0
    for i, ch in enumerate(text):
        units += 2 if ord(ch) > 0xFFFF else 1
        if units > limit:
            return text[:i]
    return text


def _title(text: str) -> dict:
    return {"title": [{"text": {"content": _notion_truncate(text)}}]}

def _rich_text(text: str | None) -> dict | None:
    if text is None:
        return None
    return {"rich_text": [{"text": {"content": _notion_truncate(text)}}]}

def _url(value: str | None) -> dict | None:
    if not value:
        return None
    return {"url": value}

def _select(value: str | None) -> dict | None:
    if not value:
        return None
    return {"select": {"name": value}}

def _multi_select(values: list[str]) -> dict:
    return {"multi_select": [{"name": v} for v in values if v]}

def _date(iso: str | None) -> dict | None:
    if not iso:
        return None
    return {"date": {"start": iso}}


def _rich_text_chunked(text: str) -> dict:
    """
    Splits text into ≤2000 UTF-16 code unit rich-text objects (Notion's per-object cap).
    An array of up to 100 objects supports large transcripts and OCR outputs.
    Uses UTF-16 unit counting so emoji and non-BMP characters don't cause 400 errors.
    """
    chunks = []
    remaining = text
    while remaining and len(chunks) < 100:
        chunk = _notion_truncate(remaining, limit=2000)
        chunks.append({"text": {"content": chunk}})
        remaining = remaining[len(chunk):]
    if remaining:
        log.warning("notion: text exceeded 100 chunks (~200k units) — %d chars dropped",
                    len(remaining))
    return {"rich_text": chunks}


def _build_properties(metadata: dict) -> dict:
    """
    Map an extractor metadata dict to Notion API property payloads.
    Omits null fields entirely — never sends null to Notion.
    """
    author = metadata.get("author")
    source_id = metadata.get("source_id", "")

    # Phase 1 title: "{author} — {shortcode}" or just shortcode if no author
    title_text = f"{author} — {source_id}" if author else source_id

    props: dict = {}
    props["title"] = _title(title_text)
    props["source_id"] = _rich_text(source_id)
    props["status"] = _select("Imported")

    collections = metadata.get("collections")
    if collections:
        props["collection"] = _multi_select(list(collections))

    for key, builder in [
        ("ig_link",     lambda v: _url(v)),
        ("author",      lambda v: _rich_text(v)),
        ("type",        lambda v: _select(v)),
        ("caption",     lambda v: _rich_text_chunked(v)),
        ("posted_date", lambda v: _date(v)),
    ]:
        val = metadata.get(key)
        if val is not None:
            built = builder(val)
            if built is not None:
                props[key] = built

    return props


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _get_data_source_id(client: Client, database_id: str) -> str:
    """Resolve the data_source_id for a database (notion-client 3.x / API 2025-09-03)."""
    db = client.databases.retrieve(database_id=database_id)
    return db["data_sources"][0]["id"]


def query_by_source_id(config: Config, source_id: str) -> str | None:
    """
    Returns the Notion page ID if a page with this source_id already exists,
    otherwise None. Used for deduplication before any write.
    """
    validate_notion_config(config)
    client = Client(auth=config.notion_token)
    ds_id = _get_data_source_id(client, config.notion_database_id)

    response = client.data_sources.query(
        ds_id,
        filter={"property": "source_id", "rich_text": {"equals": source_id}},
    )
    results = response.get("results", [])
    if results:
        return results[0]["id"]
    return None


def create_page(config: Config, metadata: dict) -> str:
    """
    Creates a new Notion page for the given post metadata.
    Returns the new page ID.
    Caller is responsible for checking deduplication before calling this.
    """
    validate_notion_config(config)
    client = Client(auth=config.notion_token)

    props = _build_properties(metadata)

    try:
        response = client.pages.create(
            parent={"database_id": config.notion_database_id},
            properties=props,
        )
        page_id = response["id"]
        log.info("notion: created page %s for %s", page_id, metadata.get("source_id"))
        return page_id
    except APIResponseError as e:
        raise RuntimeError(
            f"notion: failed to create page for {metadata.get('source_id')}: {e}"
        ) from e


def mark_failed(config: Config, page_id: str, notes: str) -> None:
    """
    Sets status=Failed and writes failure_notes on an existing page.
    Used when a pipeline stage fails after the row has already been created.
    """
    validate_notion_config(config)
    client = Client(auth=config.notion_token)

    try:
        client.pages.update(
            page_id=page_id,
            properties={
                "status": _select("Failed"),
                "failure_notes": _rich_text(notes[:2000]),
            },
        )
        log.info("notion: marked %s as Failed", page_id)
    except APIResponseError as e:
        log.error("notion: could not mark %s as Failed: %s", page_id, e)


def query_by_status(config: Config, status: str) -> list[dict]:
    """
    Returns all pages whose status matches the given value.
    Each item: {"page_id": str, "source_id": str, "ig_link": str, "type": str | None}.
    Paginates automatically.
    """
    validate_notion_config(config)
    client = Client(auth=config.notion_token)
    ds_id = _get_data_source_id(client, config.notion_database_id)

    results = []
    cursor = None
    while True:
        kwargs = {
            "filter": {"property": "status", "select": {"equals": status}},
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.data_sources.query(ds_id, **kwargs)
        for page in response.get("results", []):
            props = page.get("properties", {})
            source_id_blocks = props.get("source_id", {}).get("rich_text", [])
            ig_link = props.get("ig_link", {}).get("url")
            type_select = props.get("type", {}).get("select") or {}
            results.append({
                "page_id": page["id"],
                "source_id": source_id_blocks[0]["text"]["content"] if source_id_blocks else None,
                "ig_link": ig_link,
                "type": type_select.get("name"),
            })
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    return results


def query_by_status_and_priority(
    config: Config, status: str, priority: str | None
) -> list[dict]:
    """
    Returns pages where status equals status AND priority
    matches the given bucket.

    priority is one of "High"/"Medium"/"Low" (exact select option), or None to
    match items with no priority set (the unprioritised bucket).
    Each item: {"page_id": str, "source_id": str, "ig_link": str, "type": str | None}. Paginates.
    """
    validate_notion_config(config)
    client = Client(auth=config.notion_token)
    ds_id = _get_data_source_id(client, config.notion_database_id)

    if priority is None:
        priority_filter = {"property": "priority", "select": {"is_empty": True}}
    else:
        priority_filter = {"property": "priority", "select": {"equals": priority}}

    results = []
    cursor = None
    while True:
        kwargs = {
            "filter": {
                "and": [
                    {"property": "status", "select": {"equals": status}},
                    priority_filter,
                ]
            }
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.data_sources.query(ds_id, **kwargs)
        for page in response.get("results", []):
            props = page.get("properties", {})
            source_id_blocks = props.get("source_id", {}).get("rich_text", [])
            ig_link = props.get("ig_link", {}).get("url")
            type_select = props.get("type", {}).get("select") or {}
            results.append({
                "page_id": page["id"],
                "source_id": source_id_blocks[0]["text"]["content"] if source_id_blocks else None,
                "ig_link": ig_link,
                "type": type_select.get("name"),
            })
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    return results


def mark_queued(config: Config, page_id: str) -> None:
    """Set status to Queued on an existing page."""
    validate_notion_config(config)
    client = Client(auth=config.notion_token)
    try:
        client.pages.update(
            page_id=page_id,
            properties={"status": _select("Queued")},
        )
        log.info("notion: marked %s as Queued", page_id)
    except APIResponseError as e:
        log.error("notion: could not mark %s as Queued: %s", page_id, e)


def query_by_collection_and_status(
    config: Config, collection_name: str, status: str
) -> list[dict]:
    """
    Returns pages where collection contains collection_name AND
    status equals status.
    Each item: {"page_id": str, "source_id": str}.
    Paginates automatically.
    """
    validate_notion_config(config)
    client = Client(auth=config.notion_token)
    ds_id = _get_data_source_id(client, config.notion_database_id)

    results = []
    cursor = None
    while True:
        kwargs = {
            "filter": {
                "and": [
                    {"property": "status", "select": {"equals": status}},
                    {"property": "collection", "multi_select": {"contains": collection_name}},
                ]
            }
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.data_sources.query(ds_id, **kwargs)
        for page in response.get("results", []):
            props = page.get("properties", {})
            source_id_blocks = props.get("source_id", {}).get("rich_text", [])
            results.append({
                "page_id": page["id"],
                "source_id": source_id_blocks[0]["text"]["content"] if source_id_blocks else None,
            })
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    return results


def bulk_load_state(config: Config) -> dict:
    """
    Load the entire database once into an in-memory map for the ingest sync:

        { source_id: { "page_id": str, "collections": set[str], "needs_metadata": bool } }

    Replaces per-post dedup queries — one paginated pass instead of 2 API calls
    per post. Pages without a source_id are skipped (they can't be reconciled).

    needs_metadata is True when author OR posted_date is missing — both are always
    present on a cleanly-extracted post, so their absence reliably marks a failed/
    blank extraction. Caption is deliberately NOT a trigger: it is genuinely optional,
    and triggering on it would re-refresh caption-less posts forever (never converging).
    """
    validate_notion_config(config)
    client = Client(auth=config.notion_token)
    ds_id = _get_data_source_id(client, config.notion_database_id)

    state: dict = {}
    cursor = None
    while True:
        kwargs = {}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.data_sources.query(ds_id, **kwargs)
        for page in response.get("results", []):
            props = page.get("properties", {})
            source_id_blocks = props.get("source_id", {}).get("rich_text", [])
            source_id = source_id_blocks[0]["text"]["content"] if source_id_blocks else None
            if not source_id:
                continue
            collections = {
                item["name"]
                for item in props.get("collection", {}).get("multi_select", [])
            }
            author_blocks = props.get("author", {}).get("rich_text", [])
            has_author = bool(author_blocks and author_blocks[0]["text"]["content"].strip())
            has_date = bool(props.get("posted_date", {}).get("date"))
            needs_metadata = (not has_author) or (not has_date)
            state[source_id] = {
                "page_id": page["id"],
                "collections": collections,
                "needs_metadata": needs_metadata,
            }
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    incomplete = sum(1 for v in state.values() if v["needs_metadata"])
    log.info("notion: bulk-loaded %d pages (%d need metadata)", len(state), incomplete)
    return state


def update_metadata(config: Config, page_id: str, metadata: dict) -> None:
    """
    Backfill Phase 1 metadata on an existing page: title, author, type, caption,
    posted_date. Only writes fields present in metadata. Never touches collection,
    status, or enrichment fields.
    """
    validate_notion_config(config)
    client = Client(auth=config.notion_token)

    author = metadata.get("author")
    source_id = metadata.get("source_id")
    props: dict = {}

    if author:
        props["author"] = _rich_text(author)
        # Phase 1 title convention: "{author} — {shortcode}"
        props["title"] = _title(f"{author} — {source_id}" if source_id else author)
    if metadata.get("type"):
        props["type"] = _select(metadata["type"])
    if metadata.get("caption"):
        props["caption"] = _rich_text_chunked(metadata["caption"])
    if metadata.get("posted_date"):
        props["posted_date"] = _date(metadata["posted_date"])

    if not props:
        return  # nothing to write

    try:
        client.pages.update(page_id=page_id, properties=props)
        log.info("notion: backfilled metadata for %s (author=%r)", page_id, author)
    except APIResponseError as e:
        raise RuntimeError(f"notion: failed to update metadata for {page_id}: {e}") from e


def set_collections(config: Config, page_id: str, collections: set) -> None:
    """
    Overwrite a page's collection multi-select with the given set.

    Absolute set, not a delta — makes the operation idempotent: re-applying the
    same desired set is a no-op, so interrupted syncs converge on re-run.
    """
    validate_notion_config(config)
    client = Client(auth=config.notion_token)
    try:
        client.pages.update(
            page_id=page_id,
            properties={"collection": _multi_select(sorted(collections))},
        )
        log.info("notion: set collections on %s → %s", page_id, sorted(collections))
    except APIResponseError as e:
        raise RuntimeError(
            f"notion: failed to set collections on {page_id}: {e}"
        ) from e


def write_extraction(config: Config, page_id: str, results: dict) -> None:
    """
    Writes Stage 2 extraction results back to an existing Notion page.

    results keys (all optional except processing_version):
      transcript        str — full spoken transcript (chunked if >2000 chars)
      ocr_text          str — merged OCR text (chunked if >2000 chars)
      carousel_slides   list[dict] — [{slide: N, text: str}, ...]
      processing_version  str — e.g. "v1.0-base"
      last_processed_at   str — ISO 8601 datetime

    raw_extraction is read from Notion first, then the new version key is
    appended. Existing version keys are never overwritten.
    """
    validate_notion_config(config)
    client = Client(auth=config.notion_token)
    processing_version = results.get("processing_version", "v1.0-base")

    # Read existing raw_extraction, parse as JSON dict, append new version key.
    existing_page = client.pages.retrieve(page_id=page_id)
    existing_blocks = (
        existing_page.get("properties", {})
        .get("raw_extraction", {})
        .get("rich_text", [])
    )
    existing_text = "".join(b["text"]["content"] for b in existing_blocks)
    try:
        raw = json.loads(existing_text) if existing_text.strip() else {}
    except json.JSONDecodeError:
        raw = {}

    raw[processing_version] = {
        "transcript": results.get("transcript"),
        "ocr_text": results.get("ocr_text"),
        "carousel_slides": results.get("carousel_slides"),
        "last_processed_at": results.get("last_processed_at"),
    }

    props: dict = {
        "status": _select("Extracted"),
        "processing_version": _rich_text(processing_version),
        "raw_extraction": _rich_text_chunked(json.dumps(raw, ensure_ascii=False)),
    }

    if results.get("last_processed_at"):
        props["last_processed_at"] = _date(results["last_processed_at"])

    if results.get("transcript") is not None:
        props["transcript"] = _rich_text_chunked(results["transcript"])

    ocr_text = results.get("ocr_text")
    if ocr_text is None and results.get("carousel_slides"):
        ocr_text = "\n\n".join(
            f"[Slide {s['slide']}]\n{s['text']}"
            for s in results["carousel_slides"]
            if s.get("text")
        )
    if ocr_text is not None:
        props["ocr_text"] = _rich_text_chunked(ocr_text)

    try:
        client.pages.update(page_id=page_id, properties=props)
        log.info("notion: wrote extraction %s for page %s", processing_version, page_id)
    except APIResponseError as e:
        raise RuntimeError(
            f"notion: failed to write extraction for {page_id}: {e}"
        ) from e


def get_page_content(config: Config, page_id: str) -> dict:
    """
    Retrieve all fields needed for Phase 3 enrichment from a Notion page.

    Returns dict with keys:
      page_id, source_id, title, author, type, collection (list[str]),
      caption, transcript, ocr_text, summary (None = not yet summarized).
    """
    validate_notion_config(config)
    client = Client(auth=config.notion_token)
    page = client.pages.retrieve(page_id=page_id)
    props = page.get("properties", {})

    def _text(prop_name: str) -> str | None:
        blocks = props.get(prop_name, {}).get("rich_text", [])
        return "".join(b["text"]["content"] for b in blocks) or None

    def _title_text() -> str | None:
        blocks = props.get("title", {}).get("title", [])
        return "".join(b["text"]["content"] for b in blocks) or None

    def _select_val(prop_name: str) -> str | None:
        sel = props.get(prop_name, {}).get("select")
        return sel["name"] if sel else None

    def _multi_select_vals(prop_name: str) -> list[str]:
        items = props.get(prop_name, {}).get("multi_select", [])
        return [item["name"] for item in items]

    return {
        "page_id": page_id,
        "source_id": _text("source_id"),
        "title": _title_text(),
        "author": _text("author"),
        "type": _select_val("type"),
        "collection": _multi_select_vals("collection"),
        "caption": _text("caption"),
        "transcript": _text("transcript"),
        "ocr_text": _text("ocr_text"),
        "summary": _text("summary"),  # None = not yet summarized
    }


def write_summary(config: Config, page_id: str, enrichment: dict, version: str) -> None:
    """
    Write Claude summary and externals fields to a Notion page.

    enrichment keys: summary (str), externals (str, optional).

    Sets status to Summarized. Does NOT touch title or raw_extraction.
    Updates summary, externals (if present), processing_version, last_processed_at.
    """
    import datetime

    validate_notion_config(config)
    client = Client(auth=config.notion_token)

    props: dict = {
        "status": _select("Summarized"),
        "processing_version": _rich_text(version),
        "last_processed_at": _date(datetime.datetime.utcnow().date().isoformat()),
    }

    if enrichment.get("summary"):
        props["summary"] = _rich_text_chunked(enrichment["summary"])
    if enrichment.get("externals"):
        props["externals"] = _rich_text_chunked(enrichment["externals"])

    try:
        client.pages.update(page_id=page_id, properties=props)
        log.info("notion: wrote summary %s for page %s", version, page_id)
    except APIResponseError as e:
        raise RuntimeError(
            f"notion: failed to write summary for {page_id}: {e}"
        ) from e


def write_title(config: Config, page_id: str, title: str) -> None:
    """
    Write a generated title to a Notion page. Does NOT change status.

    Title generation is decoupled from the pipeline status machine — items can
    be titled at Queued or Extracted status. The summarize pass reads Extracted
    regardless of whether the item has been titled.
    """
    import datetime

    validate_notion_config(config)
    client = Client(auth=config.notion_token)

    props: dict = {
        "last_processed_at": _date(datetime.datetime.utcnow().date().isoformat()),
    }
    if title:
        props["title"] = _title(title)

    try:
        client.pages.update(page_id=page_id, properties=props)
        log.info("notion: wrote title for page %s", page_id)
    except APIResponseError as e:
        raise RuntimeError(
            f"notion: failed to write title for {page_id}: {e}"
        ) from e
