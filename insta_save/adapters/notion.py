"""Notion adapter (v2). API 2025-09-03 / notion-client 3.x: data_sources.*, not databases.*.

Carries v1's UTF-16-aware truncation/chunking and append-only raw_extraction, retargeted
to EnvConfig and extract_version. ensure_schema is additive + idempotent (never removes)."""

import json
import logging

from notion_client import Client
from notion_client.errors import APIResponseError

from insta_save.config.env import EnvConfig, validate_notion
from insta_save.engines.ocr_clean import clean_ocr_text

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


def _title(text):
    return {"title": [{"text": {"content": _notion_truncate(text or "")}}]}


def _multi_select(values):
    return {"multi_select": [{"name": v} for v in values]}


def _select(value):
    if not value:
        return None
    return {"select": {"name": value}}


def _date(iso):
    if not iso:
        return None
    return {"date": {"start": iso}}


def _url(value):
    if not value:
        return None
    return {"url": value}


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


def _transcript_language_from_raw(raw: dict, extract_version: str):
    """Pull transcript_language out of the raw_extraction payload — prefer the
    slot matching extract_version, else any version that recorded one."""
    slot = raw.get(extract_version) or {}
    if slot.get("transcript_language"):
        return slot["transcript_language"]
    for payload in raw.values():
        if isinstance(payload, dict) and payload.get("transcript_language"):
            return payload["transcript_language"]
    return None


def _enrich_props(title, summary, externals, tags, version) -> dict:
    """Build the Notion property payload for one enriched item. summary/externals
    use chunked rich_text (full length); title is truncated to one block."""
    props = {
        "status": _select("Tagged"),
        "enrich_version": _rich_text(version),
        "title": _title(title),
        "summary": _rich_text_chunked(summary or ""),
    }
    if externals:
        props["externals"] = _rich_text_chunked(externals)
    if tags:
        props["tags"] = _multi_select(tags)
    return props


def _build_ingest_properties(metadata: dict) -> dict:
    """Map extractor metadata → Notion props for a new/updated page. Nulls omitted.
    Phase-1 title is `{handle} — {shortcode}` (author is the handle; replaced when
    enrich writes a real title)."""
    author = metadata.get("author")
    source_id = metadata.get("source_id", "")
    props = {
        "title": _title(f"{author} — {source_id}" if author else source_id),
        "source_id": _rich_text(source_id),
        "status": _select("Imported"),
    }
    if metadata.get("collections"):
        props["collection"] = _multi_select(list(metadata["collections"]))
    for key, builder in [("ig_link", _url), ("author", _rich_text),
                         ("type", _select), ("caption", _rich_text_chunked),
                         ("posted_date", _date)]:
        val = metadata.get(key)
        if val is not None:
            built = builder(val)
            if built is not None:
                props[key] = built
    return props


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
    author_blocks = props.get("author", {}).get("rich_text", [])
    type_select = props.get("type", {}).get("select") or {}
    collections = [c["name"] for c in props.get("collection", {}).get("multi_select", [])]
    return {
        "page_id": page["id"],
        "source_id": sid_blocks[0]["text"]["content"] if sid_blocks else None,
        "author": author_blocks[0]["text"]["content"] if author_blocks else None,
        "ig_link": props.get("ig_link", {}).get("url"),
        "type": type_select.get("name"),
        "collections": collections,
    }


def query_by_status_and_priority(env: EnvConfig, status: str, priority) -> list[dict]:
    """Pages where status==status AND priority bucket matches (None = is_empty). Paginates.
    Each row: {page_id, source_id, author, ig_link, type, collections}."""
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
        "transcript_language": results.get("transcript_language"),
        "ocr_text": results.get("ocr_text"),
        "carousel_slides": results.get("carousel_slides"),
        "ocr_frames": results.get("ocr_frames"),
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


def get_page_content(env: EnvConfig, page_id: str) -> dict:
    """Full content for enrich. Returns: page_id, source_id, title, author, type,
    collections (list), caption, transcript, ocr_text, transcript_language."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    page = client.pages.retrieve(page_id=page_id)
    props = page.get("properties", {})

    def _text(name):
        blocks = props.get(name, {}).get("rich_text", [])
        return "".join(b["text"]["content"] for b in blocks) or None

    title_blocks = props.get("title", {}).get("title", [])
    title = "".join(b["text"]["content"] for b in title_blocks) or None
    type_select = props.get("type", {}).get("select") or {}
    collections = [c["name"] for c in props.get("collection", {}).get("multi_select", [])]

    raw_text = _text("raw_extraction") or ""
    try:
        raw = json.loads(raw_text) if raw_text.strip() else {}
    except json.JSONDecodeError:
        raw = {}
    extract_version = _text("extract_version") or ""
    transcript_language = _transcript_language_from_raw(raw, extract_version)

    # Collapse near-duplicate frame-OCR at enrich-read so enrich/calibrate read (and budget on)
    # the cleaned text, while raw_extraction keeps the full OCR (D13: durable/reprocessable).
    ocr_raw = _text("ocr_text")
    ocr_text = clean_ocr_text(ocr_raw) if ocr_raw else None

    return {
        "page_id": page_id,
        "source_id": _text("source_id"),
        "title": title,
        "author": _text("author"),
        "type": type_select.get("name"),
        "collections": collections,
        "caption": _text("caption"),
        "transcript": _text("transcript"),
        "ocr_text": ocr_text,
        "transcript_language": transcript_language,
    }


def write_enrichment(env: EnvConfig, page_id: str, fields: dict, version: str) -> None:
    """Write one enriched item -> Tagged. fields: title, summary, externals, tags(list)."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    props = _enrich_props(
        title=fields.get("title"), summary=fields.get("summary"),
        externals=fields.get("externals"), tags=fields.get("tags") or [], version=version)
    try:
        client.pages.update(page_id=page_id, properties=props)
        log.info("notion: wrote enrichment %s for page %s", version, page_id)
    except APIResponseError as e:
        raise RuntimeError(f"notion: failed to write enrichment for {page_id}: {e}") from e


def create_page(env: EnvConfig, metadata: dict) -> str:
    """Create a new page from metadata (Imported). Caller dedups via bulk_load_state."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    try:
        resp = client.pages.create(
            parent={"database_id": env.notion_database_id},
            properties=_build_ingest_properties(metadata))
        log.info("notion: created page %s for %s", resp["id"], metadata.get("source_id"))
        return resp["id"]
    except APIResponseError as e:
        raise RuntimeError(f"notion: create failed for {metadata.get('source_id')}: {e}") from e


def bulk_load_state(env: EnvConfig) -> dict:
    """Load the whole DB once → {source_id: {page_id, collections:set, needs_metadata}}.

    needs_metadata = author OR posted_date missing (a clean extract always has both;
    caption is genuinely optional and deliberately not a trigger)."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    ds_id = _get_data_source_id(client, env.notion_database_id)
    state, cursor = {}, None
    while True:
        kwargs = {"start_cursor": cursor} if cursor else {}
        resp = client.data_sources.query(ds_id, **kwargs)
        for page in resp.get("results", []):
            props = page.get("properties", {})
            sid_blocks = props.get("source_id", {}).get("rich_text", [])
            sid = sid_blocks[0]["text"]["content"] if sid_blocks else None
            if not sid:
                continue
            collections = {c["name"] for c in props.get("collection", {}).get("multi_select", [])}
            author_blocks = props.get("author", {}).get("rich_text", [])
            has_author = bool(author_blocks and author_blocks[0]["text"]["content"].strip())
            has_date = bool(props.get("posted_date", {}).get("date"))
            state[sid] = {"page_id": page["id"], "collections": collections,
                          "needs_metadata": (not has_author) or (not has_date)}
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    log.info("notion: bulk-loaded %d pages", len(state))
    return state


def set_collections(env: EnvConfig, page_id: str, collections: set) -> None:
    """Overwrite the collection multi-select (absolute set → idempotent retag)."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    try:
        client.pages.update(page_id=page_id,
                            properties={"collection": _multi_select(sorted(collections))})
        log.info("notion: set collections on %s → %s", page_id, sorted(collections))
    except APIResponseError as e:
        raise RuntimeError(f"notion: set_collections failed on {page_id}: {e}") from e


def update_metadata(env: EnvConfig, page_id: str, metadata: dict) -> None:
    """Backfill metadata on an existing page (self-healing). Keeps current status."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    props = _build_ingest_properties(metadata)
    props.pop("status", None)   # never reset status on a backfill
    try:
        client.pages.update(page_id=page_id, properties=props)
        log.info("notion: backfilled metadata on %s", page_id)
    except APIResponseError as e:
        raise RuntimeError(f"notion: update_metadata failed on {page_id}: {e}") from e


def mark_queued(env: EnvConfig, page_id: str) -> None:
    validate_notion(env)
    client = Client(auth=env.notion_token)
    try:
        client.pages.update(page_id=page_id, properties={"status": _select("Queued")})
        log.info("notion: marked %s Queued", page_id)
    except APIResponseError as e:
        raise RuntimeError(f"notion: mark_queued failed on {page_id}: {e}") from e
