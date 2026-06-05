"""
Notion API client — Stage 1 ingestion and Stage 2 extraction operations.

Exposes:
  query_by_source_id  — deduplication check before any write
  create_page         — write one Stage 1 row
  mark_failed         — set pipeline_status=Failed and write failure_notes
  query_by_status     — return all pages matching a pipeline_status value
  write_extraction    — write Stage 2 results; appends raw_extraction under
                        a version key, never overwrites prior versions

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

def _title(text: str) -> dict:
    return {"title": [{"text": {"content": text[:2000]}}]}

def _rich_text(text: str | None) -> dict | None:
    if text is None:
        return None
    return {"rich_text": [{"text": {"content": text[:2000]}}]}

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
    Splits text into ≤2000-char rich-text objects (Notion's per-object cap).
    An array of up to 100 objects supports ~200KB per property — sufficient
    for any expected transcript or OCR output.
    """
    chunks = [text[i:i + 2000] for i in range(0, len(text), 2000)]
    return {"rich_text": [{"text": {"content": chunk}} for chunk in chunks[:100]]}


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
    props["pipeline_status"] = _select("Imported")

    collection = metadata.get("collection")
    if collection:
        props["collection"] = _multi_select([collection])

    for key, builder in [
        ("ig_link",     lambda v: _url(v)),
        ("author",      lambda v: _rich_text(v)),
        ("type",        lambda v: _select(v)),
        ("caption",     lambda v: _rich_text(v)),
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
    Sets pipeline_status=Failed and writes failure_notes on an existing page.
    Used when a pipeline stage fails after the row has already been created.
    """
    validate_notion_config(config)
    client = Client(auth=config.notion_token)

    try:
        client.pages.update(
            page_id=page_id,
            properties={
                "pipeline_status": _select("Failed"),
                "failure_notes": _rich_text(notes[:2000]),
            },
        )
        log.info("notion: marked %s as Failed", page_id)
    except APIResponseError as e:
        log.error("notion: could not mark %s as Failed: %s", page_id, e)


def query_by_status(config: Config, status: str) -> list[dict]:
    """
    Returns all pages whose pipeline_status matches the given value.
    Each item: {"page_id": str, "source_id": str, "ig_link": str}.
    Paginates automatically.
    """
    validate_notion_config(config)
    client = Client(auth=config.notion_token)
    ds_id = _get_data_source_id(client, config.notion_database_id)

    results = []
    cursor = None
    while True:
        kwargs = {
            "filter": {"property": "pipeline_status", "select": {"equals": status}},
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.data_sources.query(ds_id, **kwargs)
        for page in response.get("results", []):
            props = page.get("properties", {})
            source_id_blocks = props.get("source_id", {}).get("rich_text", [])
            ig_link = props.get("ig_link", {}).get("url")
            results.append({
                "page_id": page["id"],
                "source_id": source_id_blocks[0]["text"]["content"] if source_id_blocks else None,
                "ig_link": ig_link,
            })
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    return results


def mark_queued(config: Config, page_id: str) -> None:
    """Set pipeline_status to Queued on an existing page."""
    validate_notion_config(config)
    client = Client(auth=config.notion_token)
    try:
        client.pages.update(
            page_id=page_id,
            properties={"pipeline_status": _select("Queued")},
        )
        log.info("notion: marked %s as Queued", page_id)
    except APIResponseError as e:
        log.error("notion: could not mark %s as Queued: %s", page_id, e)


def query_by_collection_and_status(
    config: Config, collection_name: str, status: str
) -> list[dict]:
    """
    Returns pages where collection contains collection_name AND
    pipeline_status equals status.
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
                    {"property": "pipeline_status", "select": {"equals": status}},
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


def write_extraction(config: Config, page_id: str, results: dict) -> None:
    """
    Writes Stage 2 extraction results back to an existing Notion page.

    results keys (all optional except processing_version):
      transcript        str — full spoken transcript (chunked if >2000 chars)
      transcript_available  bool
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
        "transcript_available": results.get("transcript_available", False),
        "ocr_text": results.get("ocr_text"),
        "carousel_slides": results.get("carousel_slides"),
        "last_processed_at": results.get("last_processed_at"),
    }

    props: dict = {
        "pipeline_status": _select("Expanded"),
        "processing_version": _rich_text(processing_version),
        "raw_extraction": _rich_text_chunked(json.dumps(raw, ensure_ascii=False)),
    }

    if results.get("last_processed_at"):
        props["last_processed_at"] = _date(results["last_processed_at"])

    transcript_available = results.get("transcript_available", False)
    props["transcript_available"] = {"checkbox": transcript_available}

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
      caption, transcript, ocr_text, expanded_summary (None = not yet enriched).
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
        "expanded_summary": _text("expanded_summary"),  # None = not yet enriched
    }


def write_enrichment(config: Config, page_id: str, enrichment: dict, version: str) -> None:
    """
    Write Phase 3 Claude enrichment fields to a Notion page.

    enrichment keys: expanded_summary (str), key_insights (list[str]).

    Sets pipeline_status to Summarised. Does NOT touch title, extracted_externals,
    or raw_extraction — those are written by the local Ollama pass.
    Updates expanded_summary, key_insights, processing_version, last_processed_at.
    """
    import datetime

    validate_notion_config(config)
    client = Client(auth=config.notion_token)

    key_insights_text = "\n".join(
        f"• {insight}" for insight in (enrichment.get("key_insights") or [])
    )

    props: dict = {
        "pipeline_status": _select("Summarised"),
        "processing_version": _rich_text(version),
        "last_processed_at": _date(datetime.datetime.utcnow().date().isoformat()),
    }

    if enrichment.get("expanded_summary"):
        props["expanded_summary"] = _rich_text_chunked(enrichment["expanded_summary"])

    if key_insights_text:
        props["key_insights"] = _rich_text_chunked(key_insights_text)

    try:
        client.pages.update(page_id=page_id, properties=props)
        log.info("notion: wrote enrichment %s for page %s", version, page_id)
    except APIResponseError as e:
        raise RuntimeError(
            f"notion: failed to write enrichment for {page_id}: {e}"
        ) from e


def write_local_enrichment(
    config: Config, page_id: str, title: str, extracted_externals: str
) -> None:
    """
    Write local enrichment fields to a Notion page.
    Only writes title and extracted_externals.
    Does NOT touch expanded_summary, key_insights, pipeline_status, or raw_extraction.
    """
    import datetime

    validate_notion_config(config)
    client = Client(auth=config.notion_token)

    props: dict = {
        "pipeline_status": _select("Enriched"),
        "last_processed_at": _date(datetime.datetime.utcnow().date().isoformat()),
    }
    if title:
        props["title"] = _title(title)
    if extracted_externals:
        props["extracted_externals"] = _rich_text_chunked(extracted_externals)

    try:
        client.pages.update(page_id=page_id, properties=props)
        log.info("notion: wrote local enrichment for page %s", page_id)
    except APIResponseError as e:
        raise RuntimeError(
            f"notion: failed to write local enrichment for {page_id}: {e}"
        ) from e
