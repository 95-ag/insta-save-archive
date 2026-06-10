"""Notion adapter (v2). API 2025-09-03 / notion-client 3.x: data_sources.*, not databases.*.

Carries v1's UTF-16-aware truncation/chunking and append-only raw_extraction, retargeted
to EnvConfig and extract_version. ensure_schema is additive + idempotent (never removes)."""

import json
import logging

from notion_client import Client
from notion_client.errors import APIResponseError

from insta_save.config.env import EnvConfig, validate_notion

log = logging.getLogger(__name__)

# v2 schema additions (additive only — removals/migration are out of scope, see plan).
_V2_PROPERTIES = {
    "tags": {"multi_select": {}},
    "route_target": {"select": {}},
    "extract_version": {"rich_text": {}},
    "enrich_version": {"rich_text": {}},
}
_V2_STATUS_OPTIONS = ("Tagged", "Routed")


# --- property builders (PORT verbatim from legacy/pipeline/notion.py) -------
def _notion_truncate(text: str, limit: int = 2000) -> str:
    units = 0
    for i, ch in enumerate(text):
        units += 2 if ord(ch) > 0xFFFF else 1
        if units > limit:
            return text[:i]
    return text


def _rich_text(text):
    if text is None:
        return None
    return {"rich_text": [{"text": {"content": _notion_truncate(text)}}]}


def _select(value):
    if not value:
        return None
    return {"select": {"name": value}}


def _date(iso):
    if not iso:
        return None
    return {"date": {"start": iso}}


def _rich_text_chunked(text: str) -> dict:
    chunks, remaining = [], text
    while remaining and len(chunks) < 100:
        chunk = _notion_truncate(remaining, limit=2000)
        chunks.append({"text": {"content": chunk}})
        remaining = remaining[len(chunk):]
    if remaining:
        log.warning("notion: text exceeded 100 chunks (~200k units) — %d chars dropped", len(remaining))
    return {"rich_text": chunks}


def _get_data_source_id(client: Client, database_id: str) -> str:
    db = client.databases.retrieve(database_id=database_id)
    return db["data_sources"][0]["id"]


# --- pure helpers (unit-tested) --------------------------------------------
def _merge_raw(existing: dict, version: str, payload: dict) -> dict:
    """Append payload under version key. Prior (different) version keys are preserved;
    re-running the same version replaces its own slot."""
    merged = dict(existing)
    merged[version] = payload
    return merged


def _synth_ocr_text(slides: list[dict]) -> str:
    return "\n\n".join(f"[Slide {s['slide']}]\n{s['text']}" for s in slides if s.get("text"))


def _schema_property_additions(existing_props: dict) -> dict:
    return {name: spec for name, spec in _V2_PROPERTIES.items() if name not in existing_props}


def _status_option_additions(existing_option_names: list[str]):
    """Return the FULL option list (existing + missing v2 statuses) or None if nothing to add.
    Notion replaces the whole option set on update, so existing names must be preserved."""
    missing = [s for s in _V2_STATUS_OPTIONS if s not in existing_option_names]
    if not missing:
        return None
    return [{"name": n} for n in existing_option_names] + [{"name": n} for n in missing]


# --- API surface -----------------------------------------------------------
def ensure_schema(env: EnvConfig) -> None:
    """Idempotently add v2 properties + Tagged/Routed status options. Additive only."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    ds_id = _get_data_source_id(client, env.notion_database_id)
    ds = client.data_sources.retrieve(data_source_id=ds_id)
    props = ds.get("properties", {})

    to_add = _schema_property_additions(props)
    # DB uses select-type for status (not Notion's native "status" type) — v1-consistent.
    status_prop = props.get("status", {}).get("select", {})
    existing_status = [o["name"] for o in status_prop.get("options", [])]
    new_status_opts = _status_option_additions(existing_status)
    if new_status_opts is not None:
        to_add = {**to_add, "status": {"select": {"options": new_status_opts}}}

    if not to_add:
        log.info("notion: schema already up to date")
        return
    client.data_sources.update(data_source_id=ds_id, properties=to_add)
    log.info("notion: added schema elements: %s", sorted(to_add))


def _row(page: dict) -> dict:
    props = page.get("properties", {})
    sid_blocks = props.get("source_id", {}).get("rich_text", [])
    type_select = props.get("type", {}).get("select") or {}
    collections = [c["name"] for c in props.get("collection", {}).get("multi_select", [])]
    return {
        "page_id": page["id"],
        "source_id": sid_blocks[0]["text"]["content"] if sid_blocks else None,
        "ig_link": props.get("ig_link", {}).get("url"),
        "type": type_select.get("name"),
        "collections": collections,
    }


def query_by_status_and_priority(env: EnvConfig, status: str, priority) -> list[dict]:
    """Pages where status==status AND priority bucket matches (None = is_empty). Paginates.
    Each row: {page_id, source_id, ig_link, type, collections}."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    ds_id = _get_data_source_id(client, env.notion_database_id)
    if priority is None:
        pfilter = {"property": "priority", "select": {"is_empty": True}}
    else:
        pfilter = {"property": "priority", "select": {"equals": priority}}

    results, cursor = [], None
    while True:
        kwargs = {"filter": {"and": [{"property": "status", "select": {"equals": status}}, pfilter]}}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.data_sources.query(ds_id, **kwargs)
        results.extend(_row(p) for p in resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return results


def mark_failed(env: EnvConfig, page_id: str, notes: str) -> None:
    validate_notion(env)
    client = Client(auth=env.notion_token)
    try:
        client.pages.update(page_id=page_id, properties={
            "status": _select("Failed"),
            "failure_notes": _rich_text(notes),
        })
        log.info("notion: marked %s as Failed", page_id)
    except APIResponseError as e:
        log.error("notion: could not mark %s as Failed: %s", page_id, e)


def write_extraction(env: EnvConfig, page_id: str, results: dict) -> None:
    """Write Extracted-stage results. Appends raw_extraction under extract_version.

    results keys: extract_version, last_processed_at, transcript, ocr_text, carousel_slides."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    version = results.get("extract_version", "v2.0-base-tuned")

    existing_page = client.pages.retrieve(page_id=page_id)
    blocks = existing_page.get("properties", {}).get("raw_extraction", {}).get("rich_text", [])
    existing_text = "".join(b["text"]["content"] for b in blocks)
    try:
        raw = json.loads(existing_text) if existing_text.strip() else {}
    except json.JSONDecodeError:
        raw = {}

    raw = _merge_raw(raw, version, {
        "transcript": results.get("transcript"),
        "ocr_text": results.get("ocr_text"),
        "carousel_slides": results.get("carousel_slides"),
        "last_processed_at": results.get("last_processed_at"),
    })

    props = {
        "status": _select("Extracted"),
        "extract_version": _rich_text(version),
        "raw_extraction": _rich_text_chunked(json.dumps(raw, ensure_ascii=False)),
    }
    if results.get("last_processed_at"):
        props["last_processed_at"] = _date(results["last_processed_at"])
    if results.get("transcript") is not None:
        props["transcript"] = _rich_text_chunked(results["transcript"])

    ocr_text = results.get("ocr_text")
    if ocr_text is None and results.get("carousel_slides"):
        ocr_text = _synth_ocr_text(results["carousel_slides"])
    if ocr_text:
        props["ocr_text"] = _rich_text_chunked(ocr_text)

    try:
        client.pages.update(page_id=page_id, properties=props)
        log.info("notion: wrote extraction %s for page %s", version, page_id)
    except APIResponseError as e:
        raise RuntimeError(f"notion: failed to write extraction for {page_id}: {e}") from e
