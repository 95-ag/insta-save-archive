"""Notion adapter (v2). API 2025-09-03 / notion-client 3.x: data_sources.*, not databases.*.

Carries v1's UTF-16-aware truncation/chunking and append-only raw_extraction, retargeted
to EnvConfig and extract_version. ensure_schema is additive + idempotent (never removes)."""

import json
import logging
import os

import httpx
from notion_client import Client
from notion_client.errors import APIResponseError, HTTPResponseError, RequestTimeoutError

from insta_save.config.env import EnvConfig, validate_notion
from insta_save.engines.ocr_clean import clean_ocr_text
from insta_save.helpers import observability

log = logging.getLogger(__name__)

# v2 schema additions (additive only — removals/migration are out of scope, see plan).
_V2_PROPERTIES = {
    "tags": {"multi_select": {}},
    "route_target": {"select": {}},
    "extract_version": {"rich_text": {}},
    "enrich_version": {"rich_text": {}},
    "distribute_notes": {"rich_text": {}},
    "distribute_version": {"rich_text": {}},
}
_V2_STATUS_OPTIONS = ("Tagged", "Routed", "Distributed", "Misrouted")

OCR_MAX_CHARS = 12000  # enrich-read OCR safety cap: bounds pathological frame-OCR jitter
                       # (raw_extraction keeps the full OCR; D13). ~typical cleaned text is far below.


# --- property builders --------------------------------------------------------
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
    try:
        db = client.databases.retrieve(database_id=database_id)
        return db["data_sources"][0]["id"]
    except APIResponseError as exc:
        raise RuntimeError(
            f"Notion API error for database {database_id} (check NOTION_TOKEN / "
            f"NOTION_DATABASE_ID): {exc}") from exc
    except (KeyError, IndexError) as exc:
        raise RuntimeError(
            f"Notion database {database_id} has no data_sources — check the database ID") from exc


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


def _slide_images_from_raw(raw: dict, tmp_dir: str, extract_version: str) -> list[str]:
    """Resolve carousel/post slide image paths (recorded relative-to-tmp_dir at
    extract) to paths a vision enrich can Read. Prefer the slot matching
    extract_version, else any slot carrying carousel_slides."""
    slot = raw.get(extract_version) or {}
    slides = slot.get("carousel_slides")
    if not isinstance(slides, list):
        for payload in raw.values():
            if isinstance(payload, dict) and isinstance(payload.get("carousel_slides"), list):
                slides = payload["carousel_slides"]
                break
    if not isinstance(slides, list):
        return []
    return [os.path.abspath(os.path.join(tmp_dir, s["image"])) for s in slides
            if isinstance(s, dict) and s.get("image")]


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
    """Idempotently add v2 properties and status options. Additive only."""
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
    tags = [t["name"] for t in props.get("tags", {}).get("multi_select", [])]
    return {
        "page_id": page["id"],
        "source_id": sid_blocks[0]["text"]["content"] if sid_blocks else None,
        "author": author_blocks[0]["text"]["content"] if author_blocks else None,
        "ig_link": props.get("ig_link", {}).get("url"),
        "type": type_select.get("name"),
        "collections": collections,
        "tags": tags,
    }


def query_by_status(env: EnvConfig, status: str) -> list[dict]:
    """Pages where status==status. Paginates.
    Each row: {page_id, source_id, author, ig_link, type, collections}."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    ds_id = _get_data_source_id(client, env.notion_database_id)

    results, cursor = [], None
    while True:
        kwargs = {"filter": {"property": "status", "select": {"equals": status}}}
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
    slide_images = _slide_images_from_raw(raw, env.tmp_dir, extract_version)

    # Collapse near-duplicate frame-OCR at enrich-read so enrich/calibrate read (and budget on)
    # the cleaned text, while raw_extraction keeps the full OCR (D13: durable/reprocessable).
    ocr_raw = _text("ocr_text")
    ocr_text = clean_ocr_text(ocr_raw, max_chars=OCR_MAX_CHARS) if ocr_raw else None

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
        "slide_images": slide_images,
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


def write_deterministic(env: EnvConfig, page_id: str, title: str, tags: list, version: str) -> None:
    """Write a deterministic-branch item -> Tagged: title + tags + status only.
    No summary/externals (Data Integrity: those need an LLM and stay null)."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    props = {
        "status": _select("Tagged"),
        "enrich_version": _rich_text(version),
        "title": _title(title),
    }
    if tags:
        props["tags"] = _multi_select(tags)
    try:
        client.pages.update(page_id=page_id, properties=props)
        log.info("notion: wrote deterministic %s for page %s", version, page_id)
    except APIResponseError as e:
        raise RuntimeError(f"notion: failed to write deterministic for {page_id}: {e}") from e


def write_route(env: EnvConfig, page_id: str, route_target: str) -> None:
    """Route a Tagged item -> Routed: set route_target (select) + status. Idempotent."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    props = {
        "status": _select("Routed"),
        "route_target": _select(route_target),
    }
    try:
        client.pages.update(page_id=page_id, properties=props)
        log.info("notion: routed page %s -> %s", page_id, route_target)
    except APIResponseError as e:
        raise RuntimeError(f"notion: failed to route {page_id}: {e}") from e


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


def query_all_pages(env: EnvConfig) -> list[dict]:
    """Return every page in the DB with no status filter (all statuses captured).
    Each entry: {"page_id": <id>, "properties": <raw Notion properties dict>}.
    Raw properties are kept as-is so the snapshot is complete and avoids field-mapping drift."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    ds_id = _get_data_source_id(client, env.notion_database_id)
    results, cursor = [], None
    with observability.spinner("Reading Notion…"):
        while True:
            kwargs = {"start_cursor": cursor} if cursor else {}
            resp = client.data_sources.query(ds_id, **kwargs)
            for page in resp.get("results", []):
                results.append({"page_id": page["id"], "properties": page.get("properties", {})})
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
    log.info("notion: query_all_pages returned %d pages", len(results))
    return results


def mark_queued(env: EnvConfig, page_id: str) -> None:
    validate_notion(env)
    client = Client(auth=env.notion_token)
    try:
        client.pages.update(page_id=page_id, properties={"status": _select("Queued")})
        log.info("notion: marked %s Queued", page_id)
    except APIResponseError as e:
        raise RuntimeError(f"notion: mark_queued failed on {page_id}: {e}") from e


def requeue(env: EnvConfig, page_id: str, status: str) -> None:
    """Reset a Failed item to the given status and clear its failure_notes.

    Used by retry_failed to send items back to Queued or Extracted.
    failure_notes is cleared by passing an empty rich_text array — passing
    _rich_text("") would leave a block with empty content; {"rich_text": []}
    removes all blocks and reads back as empty/None."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    try:
        client.pages.update(page_id=page_id, properties={
            "status": _select(status),
            "failure_notes": {"rich_text": []},
        })
        log.info("notion: requeued %s → %s (failure_notes cleared)", page_id, status)
    except APIResponseError as e:
        log.error("notion: could not requeue %s: %s", page_id, e)
        raise


def _page_title(page: dict) -> str:
    """Title of a page in ANY database — found by property TYPE, so the knowledge DB's
    title property can be renamed without breaking resolution."""
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return "".join(b.get("plain_text", "") for b in prop.get("title") or [])
    return ""


def get_knowledge_page(env: EnvConfig, name: str) -> str:
    """Resolve a route_target page NAME -> page id in the knowledge database.

    Fails loud on absent or ambiguous: route_target keys on the page name, so a rename
    must stop the run rather than silently write somewhere else."""
    validate_notion(env)
    if not env.notion_knowledge_database_id:
        raise RuntimeError("Missing NOTION_KNOWLEDGE_DATABASE_ID — set it to the Knowledge DB")
    client = Client(auth=env.notion_token)
    ds_id = _get_data_source_id(client, env.notion_knowledge_database_id)
    matches, cursor = [], None
    while True:
        kwargs = {"page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.data_sources.query(ds_id, **kwargs)
        matches.extend(p["id"] for p in resp.get("results", []) if _page_title(p) == name)
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    if len(matches) != 1:
        raise RuntimeError(
            f"notion: expected exactly 1 knowledge page named {name!r}, found {len(matches)}")
    return matches[0]


def get_page_blocks(env: EnvConfig, page_id: str) -> list:
    """Every child block of a page, paginated (Notion caps a listing at 100)."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    blocks, cursor = [], None
    while True:
        kwargs = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.blocks.children.list(**kwargs)
        blocks.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return blocks


def _distribute_row(page: dict) -> dict:
    props = page.get("properties", {})

    def _text(name):
        spans = props.get(name, {}).get("rich_text", [])
        return "".join(b.get("plain_text", "") for b in spans) or None

    title_spans = props.get("title", {}).get("title", [])
    return {
        "page_id": page["id"],
        "source_id": _text("source_id"),
        "title": "".join(b.get("plain_text", "") for b in title_spans) or None,
        "summary": _text("summary"),
        "tags": [t["name"] for t in props.get("tags", {}).get("multi_select", [])],
        "ig_link": props.get("ig_link", {}).get("url"),
        "knowledge_base": [r["id"] for r in props.get("Knowledge Base", {}).get("relation", [])],
    }


def query_routed_for_target(env: EnvConfig, target: str) -> list:
    """Routed items whose route_target is `target`, carrying everything distribute needs.

    One query, no per-item reads. The item side of the Knowledge Base relation holds at
    most one page, so the 25-item relation cap (which bites on the page side) never applies."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    ds_id = _get_data_source_id(client, env.notion_database_id)
    flt = {"and": [{"property": "status", "select": {"equals": "Routed"}},
                   {"property": "route_target", "select": {"equals": target}}]}
    items, cursor = [], None
    while True:
        kwargs = {"filter": flt, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.data_sources.query(ds_id, **kwargs)
        items.extend(_distribute_row(p) for p in resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return items


_APPEND_CHUNK = 100  # Notion caps children per blocks.children.append call
# A distribute write that times out, gets a non-API HTTP error, or fails at the transport
# level (dropped connection, DNS, etc.) must stop apply as cleanly as an API error does, so
# the stage sees one exception type for every failed write.
_DISTRIBUTE_WRITE_ERRORS = (
    APIResponseError,
    HTTPResponseError,
    RequestTimeoutError,
    httpx.TransportError,
)


def _chunked(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def replace_section_blocks(env: EnvConfig, page_id: str, *, after_block_id: str,
                           delete_block_ids: list, blocks: list) -> str:
    """Replace one section's content: insert `blocks` after `after_block_id`, then delete
    the section's old children. The heading block itself is never deleted.

    Inserting first means a failed insert can never leave a curated section empty: the
    worst case is old content plus a partial copy of the new, which the next prepare shows
    the model to dedup. The ids are logged before deletion so an interrupted delete can be
    traced in Notion's trash.

    Inserts in chunks of 100, re-anchoring each chunk on the previous chunk's last block,
    so a section larger than one append call lands in order. Returns the last inserted
    block id (the anchor for whatever comes next)."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    try:
        anchor = after_block_id
        for chunk in _chunked(blocks, _APPEND_CHUNK):
            resp = client.blocks.children.append(block_id=page_id, children=chunk, after=anchor)
            anchor = resp["results"][-1]["id"]
        log.info("notion: deleting replaced blocks on %s: %s", page_id, delete_block_ids)
        for block_id in delete_block_ids:
            client.blocks.delete(block_id=block_id)
        log.info("notion: replaced section on %s (inserted %d, deleted %d)",
                 page_id, len(blocks), len(delete_block_ids))
        return anchor
    except _DISTRIBUTE_WRITE_ERRORS as e:
        raise RuntimeError(f"notion: failed to replace section on {page_id}: {e}") from e


def set_heading_text(env: EnvConfig, block_id: str, text: str) -> None:
    """Fill an existing heading_1 in place — used to fill the template's empty content anchor."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    try:
        client.blocks.update(block_id=block_id, heading_1={"rich_text": _rich_text(text)["rich_text"]})
    except _DISTRIBUTE_WRITE_ERRORS as e:
        raise RuntimeError(f"notion: failed to fill heading {block_id}: {e}") from e


def set_knowledge_relation(env: EnvConfig, page_id: str, target_page_id: str) -> bool:
    """Idempotently link an archive item to its knowledge page. Returns True when it added
    the link, False when it was already there. Notion mirrors the knowledge-page side."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    try:
        current = client.pages.retrieve(page_id=page_id)["properties"].get(
            "Knowledge Base", {}).get("relation", [])
        if any(r["id"] == target_page_id for r in current):
            return False
        client.pages.update(page_id=page_id, properties={
            "Knowledge Base": {"relation": current + [{"id": target_page_id}]}})
        return True
    except _DISTRIBUTE_WRITE_ERRORS as e:
        raise RuntimeError(f"notion: failed to relate {page_id} -> {target_page_id}: {e}") from e


def write_distributed(env: EnvConfig, page_id: str, version: str, *, notes=None) -> None:
    """Item -> Distributed. `notes` records why nothing was added when the item was fed
    but folded nothing in (a pure duplicate); omitted when it genuinely landed."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    props = {"status": _select("Distributed"), "distribute_version": _rich_text(version)}
    if notes is not None:
        props["distribute_notes"] = _rich_text(notes)
    try:
        client.pages.update(page_id=page_id, properties=props)
    except _DISTRIBUTE_WRITE_ERRORS as e:
        raise RuntimeError(f"notion: failed to mark {page_id} Distributed: {e}") from e


def write_misrouted(env: EnvConfig, page_id: str, reason: str | None, version: str) -> None:
    """Item -> Misrouted: dropped as off-topic for its target page. Its tags need fixing,
    so it is deliberately NOT a Failed item and NOT retryable through the pipeline.

    `distribute_notes` is written only for a non-empty reason — a missing reason stays
    unset rather than becoming an empty note."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    props = {"status": _select("Misrouted"), "distribute_version": _rich_text(version)}
    if reason:
        props["distribute_notes"] = _rich_text(reason)
    try:
        client.pages.update(page_id=page_id, properties=props)
    except _DISTRIBUTE_WRITE_ERRORS as e:
        raise RuntimeError(f"notion: failed to mark {page_id} Misrouted: {e}") from e
