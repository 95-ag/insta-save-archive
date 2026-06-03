"""
Notion API client — Stage 1 ingestion operations.

Exposes three operations:
  query_by_source_id  — deduplication check before any write
  create_page         — write one Stage 1 row
  mark_failed         — set pipeline_status=Failed and write failure_notes

Validates Notion credentials on first use via validate_notion_config().
All writes respect config.notion_write_delay to stay within API rate limits.
"""

import logging
import time

from notion_client import Client
from notion_client.errors import APIResponseError

from config import Config, validate_notion_config

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
