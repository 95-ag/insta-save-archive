"""Notion adapter (v2). API 2025-09-03 / notion-client 3.x: data_sources.*, not databases.*.

Carries v1's UTF-16-aware truncation/chunking and append-only raw_extraction, retargeted
to EnvConfig and extract_version. ensure_schema is additive + idempotent (never removes)."""

import json
import logging
import os
import re
import unicodedata

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
    "primary_topic": {"select": {}},
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


def _slide_images_from_raw(raw: dict, data_dir: str, extract_version: str) -> list[str]:
    """Resolve carousel/post slide image paths (recorded relative-to-data_dir at
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
    return [os.path.abspath(os.path.join(data_dir, s["image"])) for s in slides
            if isinstance(s, dict) and s.get("image")]


def _enrich_props(title, summary, externals, tags, primary_topic, version) -> dict:
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
    if primary_topic:
        props["primary_topic"] = _select(primary_topic)
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


def _recipe_relation_spec(data_source_id: str) -> dict:
    """The `Recipes` relation, mirroring `Knowledge Base`. `dual_property: {}` asks Notion to
    create the synced property on the recipe side and name it after this database; a name passed
    here is ignored on create, so the resulting name is verified after the fact, not asserted."""
    return {
        "relation": {
            "data_source_id": data_source_id,
            "type": "dual_property",
            "dual_property": {},
        }
    }


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

    # The relation's target comes from the environment, so it cannot live in _V2_PROPERTIES.
    if env.notion_recipe_database_id and "Recipes" not in props:
        recipe_ds = _get_data_source_id(client, env.notion_recipe_database_id)
        to_add = {**to_add, "Recipes": _recipe_relation_spec(recipe_ds)}

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
    route_select = props.get("route_target", {}).get("select") or {}
    primary_select = props.get("primary_topic", {}).get("select") or {}
    return {
        "page_id": page["id"],
        "source_id": sid_blocks[0]["text"]["content"] if sid_blocks else None,
        "author": author_blocks[0]["text"]["content"] if author_blocks else None,
        "ig_link": props.get("ig_link", {}).get("url"),
        "type": type_select.get("name"),
        "collections": collections,
        "tags": tags,
        "route_target": route_select.get("name"),
        "primary_topic": primary_select.get("name"),
    }


def query_by_status(env: EnvConfig, status: str) -> list[dict]:
    """Pages where status==status. Paginates.
    Each row: {page_id, source_id, author, ig_link, type, collections, tags, route_target,
    primary_topic}."""
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


def get_item_status(env: EnvConfig, page_id: str) -> str | None:
    """Live status of one page, read fresh with pages.retrieve rather than trusted from a
    possibly-stale query index. Used by route --reroute to re-verify an item is still
    Routed immediately before a write, guarding against a concurrent status change (e.g.
    a distribute apply settling the same item mid-run) that the index hasn't caught up to
    yet. Unwrapped like the other readers (query_by_status, get_page_content, …): a
    failure here propagates to the caller, which for run_reroute_stage is the stage
    runner's own failure counting."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    page = client.pages.retrieve(page_id=page_id)
    select = page.get("properties", {}).get("status", {}).get("select") or {}
    return select.get("name")


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
    slide_images = _slide_images_from_raw(raw, env.data_dir, extract_version)

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
    """Write one enriched item -> Tagged. fields: title, summary, externals, tags(list),
    primary_topic (select; omitted when None)."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    props = _enrich_props(
        title=fields.get("title"), summary=fields.get("summary"),
        externals=fields.get("externals"), tags=fields.get("tags") or [],
        primary_topic=fields.get("primary_topic"), version=version)
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


def write_unroute(env: EnvConfig, page_id: str) -> None:
    """A Routed item whose route re-evaluates to no mapping -> back to Tagged,
    route_target cleared. Used by route --reroute; distribute/rebuild state is untouched
    (a Routed item never carries any)."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    props = {
        "status": _select("Tagged"),
        "route_target": {"select": None},
    }
    try:
        client.pages.update(page_id=page_id, properties=props)
        log.info("notion: unrouted page %s -> Tagged", page_id)
    except APIResponseError as e:
        raise RuntimeError(f"notion: failed to unroute {page_id}: {e}") from e


def requeue_for_retag(env: EnvConfig, page_id: str) -> None:
    """Send a Tagged/Routed item back to Extracted so enrich re-tags it. route_target is
    cleared because route recomputes it from the new tags; a stale one would read as decided."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    props = {
        "status": _select("Extracted"),
        "route_target": {"select": None},
    }
    try:
        client.pages.update(page_id=page_id, properties=props)
        log.info("notion: requeued %s -> Extracted for re-tag", page_id)
    except APIResponseError as e:
        raise RuntimeError(f"notion: failed to requeue {page_id} for re-tag: {e}") from e


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
        "recipes": [r["id"] for r in props.get("Recipes", {}).get("relation", [])],
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
    whatever block ids the caller passes in `delete_block_ids`. This function makes no
    promise a heading survives: apply only ever passes a section's content-block ids
    (headings stay in place), but rebuild also passes later H1 heading ids here to remove
    a whole section when resetting the page to its template.

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
    """Fill an existing heading_1 in place — used to fill the template's empty content anchor.

    Empty `text` writes no span at all: an empty-content span is not the same block as the
    template's truly empty anchor, and rebuild restores exactly that anchor."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    rich_text = _rich_text(text)["rich_text"] if text else []
    try:
        client.blocks.update(block_id=block_id, heading_1={"rich_text": rich_text})
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


def query_items_for_rebuild(env: EnvConfig, target: str, page_id: str) -> list:
    """Every archive item a rebuild of `target` resets: items related to the page, plus
    items routed there that distribute already settled (Distributed or Misrouted).

    The relation arm catches an item whose route_target has since moved; the status arm
    catches skipped and dropped items, which never got a relation. Routed items are
    deliberately out of scope — nothing of theirs is on the page.

    Notion's compound filters (and/or) nest at most two levels deep, so the two settled
    statuses are each their own `route_target`+`status` branch of the top-level `or`
    rather than a nested `or` inside an `and` (a third level, which Notion rejects)."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    ds_id = _get_data_source_id(client, env.notion_database_id)
    flt = {"or": [
        {"property": "Knowledge Base", "relation": {"contains": page_id}},
        {"and": [{"property": "route_target", "select": {"equals": target}},
                 {"property": "status", "select": {"equals": "Distributed"}}]},
        {"and": [{"property": "route_target", "select": {"equals": target}},
                 {"property": "status", "select": {"equals": "Misrouted"}}]},
    ]}
    items, cursor = [], None
    try:
        while True:
            kwargs = {"filter": flt, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = client.data_sources.query(ds_id, **kwargs)
            for page in resp.get("results", []):
                row = _distribute_row(page)
                status = (page.get("properties", {}).get("status", {}).get("select") or {}).get("name")
                items.append({"page_id": row["page_id"], "source_id": row["source_id"],
                              "title": row["title"], "status": status,
                              "knowledge_base": row["knowledge_base"]})
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
    except _DISTRIBUTE_WRITE_ERRORS as e:
        raise RuntimeError(f"notion: failed to query rebuild items for {target!r}: {e}") from e
    return items


def remove_knowledge_relation(env: EnvConfig, page_id: str, target_page_id: str) -> bool:
    """Unlink an archive item from one knowledge page, keeping every other link. Returns
    True when it removed the link, False (and writes nothing) when it was not there.

    The relation is written back whole, so a truncated read (Notion caps a relation in
    pages.retrieve at 25) would silently drop the unread links — refuse instead."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    try:
        prop = client.pages.retrieve(page_id=page_id)["properties"].get("Knowledge Base", {})
        if prop.get("has_more"):
            raise RuntimeError(
                f"notion: {page_id}'s Knowledge Base relation is truncated by Notion (more "
                f"than one page of links) — remove its link to {target_page_id} in Notion "
                "manually")
        current = prop.get("relation", [])
        if not any(r["id"] == target_page_id for r in current):
            return False
        kept = [{"id": r["id"]} for r in current if r["id"] != target_page_id]
        client.pages.update(page_id=page_id, properties={"Knowledge Base": {"relation": kept}})
        return True
    except _DISTRIBUTE_WRITE_ERRORS as e:
        raise RuntimeError(f"notion: failed to unlink {page_id} -> {target_page_id}: {e}") from e


def reset_item_to_tagged(env: EnvConfig, page_id: str) -> None:
    """Item -> Tagged with its routing and distribute state cleared, so route and distribute
    treat it as never having run. Text fields clear with `[]`, as in requeue — an
    empty-content span would read back as an empty string rather than as unset."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    props = {
        "status": _select("Tagged"),
        "route_target": {"select": None},
        "distribute_notes": {"rich_text": []},
        "distribute_version": {"rich_text": []},
    }
    try:
        client.pages.update(page_id=page_id, properties=props)
    except _DISTRIBUTE_WRITE_ERRORS as e:
        raise RuntimeError(f"notion: failed to reset {page_id} to Tagged: {e}") from e


# "Salt  + Extra" is the user's "buy extra" note, not part of the item's name — 23 live rows
# carry one, and the bare name must still be addressable.
_GROCERY_QUALIFIER_RE = re.compile(r"\s*\+.*$")
# Below this length a stem over-matches: "oat" sits inside "oatmeal", "ice" inside "ice cream".
_MIN_STEM_FOR_VARIANT = 4


def _fold_accents(text: str) -> str:
    """Drop combining marks so one spelling matches all of them. Recipe lines write the same
    item as `jalapeno`, `jalapenos`, `jalapeño`, `jalapeños`; enumerating those in the alias
    list is how the fourth gets missed."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalise_grocery_name(name: str) -> str:
    """Grocery rows use the same `Family - Item` convention as recipes (`Spices - Rasam Powder`),
    so matching an ingredient line against the bare item needs the prefix gone. Accents are
    folded so a line's spelling does not decide whether it matches."""
    return _fold_accents(name.split(" - ")[-1].strip().lower())


def _plural_variants(key: str) -> set:
    """The other of the singular/plural pair, when the stem is long enough to be safe.
    Live rows are named `Onions` and `Potatoes` while recipe lines say `onion`, `Potatoes`."""
    if len(key) < _MIN_STEM_FOR_VARIANT:
        return set()
    out = set()
    if key.endswith("ies") and len(key) > 4:
        out.add(key[:-3] + "y")
    elif key.endswith("es") and len(key) > 4:
        # Both strippings are needed and only one is ever real: potatoes -> potato (-2) but
        # olives -> olive (-1). The other is an alias no ingredient line contains, so harmless.
        out.add(key[:-2])
        out.add(key[:-1])
    elif key.endswith("s"):
        out.add(key[:-1])
    elif key.endswith("y"):
        out.add(key[:-1] + "ies")
    elif key.endswith("i"):
        # A name ending in `i` pluralises both ways and neither is predictable from the word:
        # chilli -> chillies, but roti -> rotis. Emitting both costs an unused key, while
        # emitting only `+s` lost every `green chillies` line to a `Green chilli` row.
        out.add(key + "s")
        out.add(key + "es")
    else:
        out.add(key + "s")
    return {v for v in out if len(v) >= _MIN_STEM_FOR_VARIANT}


def grocery_keys(name: str) -> set:
    """Every lookup key a grocery row answers to: its full name, its bare item name, each with
    the `+ extra` qualifier removed, plus the singular/plural counterpart of the bare name."""
    full = _fold_accents(name.strip().lower())
    bare = normalise_grocery_name(name)
    plain_full = _GROCERY_QUALIFIER_RE.sub("", full).strip()
    plain_bare = _GROCERY_QUALIFIER_RE.sub("", bare).strip()
    base = {k for k in (full, plain_full, bare, plain_bare) if k}
    # Vary ONLY the qualifier-free item name: varying the prefixed form or the `+ extra` form
    # just manufactures keys ("spice - salts", "salt + extras") no ingredient line can contain.
    return base | _plural_variants(plain_bare)


def query_recipe_rows(env: EnvConfig) -> list:
    """Every row in the recipe DB, for dedupe and family lookup. Names and URLs only — the page
    bodies are never read, because nothing in this stage rewrites one."""
    validate_notion(env)
    if not env.notion_recipe_database_id:
        raise RuntimeError("Missing NOTION_RECIPE_DATABASE_ID — set it to the recipe DB")
    client = Client(auth=env.notion_token)
    ds_id = _get_data_source_id(client, env.notion_recipe_database_id)
    rows, cursor = [], None
    while True:
        kwargs = {"page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.data_sources.query(ds_id, **kwargs)
        for page in resp.get("results", []):
            props = page.get("properties", {})
            spans = props.get("Name", {}).get("title", [])
            name = "".join(b.get("plain_text", "") for b in spans).strip()
            row = {
                "page_id": page["id"],
                "name": name,
                "family": name.split(" - ")[0] if " - " in name else None,
                "video_link": props.get("Video Link", {}).get("url"),
                "web_link": props.get("Web Link", {}).get("url"),
            }
            rows.append(row)
        if not resp.get("has_more"):
            return rows
        cursor = resp.get("next_cursor")


def query_grocery_rows(env: EnvConfig) -> dict:
    """Lookup name -> grocery page id, keyed on BOTH the full title and the bare item.

    Stripping the `Family - Item` prefix can conflate distinct items: measured live, `Garlic`
    and `Pickle - Garlic` both reduce to "garlic", and a garlic pickle is not garlic. So a
    contested bare name is owned by the row whose full title IS that name, and dropped when no
    row claims it outright — the ingredient is then reported as unmatched. A missed link is
    recoverable; a wrong one silently rewrites a shopping list."""
    validate_notion(env)
    if not env.notion_grocery_database_id:
        raise RuntimeError("Missing NOTION_GROCERY_DATABASE_ID — set it to the Grocery DB")
    client = Client(auth=env.notion_token)
    ds_id = _get_data_source_id(client, env.notion_grocery_database_id)
    claims, cursor = {}, None
    while True:
        kwargs = {"page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.data_sources.query(ds_id, **kwargs)
        for page in resp.get("results", []):
            spans = page.get("properties", {}).get("Name", {}).get("title", [])
            raw = "".join(b.get("plain_text", "") for b in spans).strip()
            full = raw.lower()
            if not full:
                continue
            for key in grocery_keys(raw):
                claims.setdefault(key, []).append((page["id"], full))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    out = {}
    for key, rows in claims.items():
        if len(rows) == 1:
            out[key] = rows[0][0]
            continue
        exact = [pid for pid, full in rows if full == key]
        if len(exact) == 1:
            out[key] = exact[0]
    return out


def set_recipe_relation(env: EnvConfig, page_id: str, recipe_row_id: str) -> bool:
    """Idempotently link an archive item to its recipe row. True when it added the link, False
    when it was already there. Notion mirrors the recipe side."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    try:
        props = client.pages.retrieve(page_id=page_id)["properties"]
        current = props.get("Recipes", {}).get("relation", [])
        if any(r["id"] == recipe_row_id for r in current):
            return False
        linked = current + [{"id": recipe_row_id}]
        client.pages.update(page_id=page_id, properties={"Recipes": {"relation": linked}})
        return True
    except _DISTRIBUTE_WRITE_ERRORS as e:
        raise RuntimeError(f"notion: failed to relate {page_id} -> {recipe_row_id}: {e}") from e


def create_recipe_row(env: EnvConfig, *, title, properties, children) -> str:
    """Create one recipe row with its body in a single call. Notion requires a column_list's
    `column` children inline at creation, so the body cannot be appended afterwards."""
    validate_notion(env)
    client = Client(auth=env.notion_token)
    ds_id = _get_data_source_id(client, env.notion_recipe_database_id)
    props = {"Name": _title(title), **properties}
    parent = {"type": "data_source_id", "data_source_id": ds_id}
    try:
        page = client.pages.create(parent=parent, properties=props, children=children)
        return page["id"]
    except _DISTRIBUTE_WRITE_ERRORS as e:
        raise RuntimeError(f"notion: failed to create recipe row {title!r}: {e}") from e


def get_blocks_deep(env: EnvConfig, block_id: str, *, max_depth: int = 4) -> list:
    """Every block under `block_id`, with each block's own children attached under "children".

    `get_page_blocks` lists one level only, which reads a recipe row as `[column_list, paragraph]`
    and hides every ingredient and step nested in the columns. Depth is capped because a cycle
    would otherwise recurse forever, and recipe bodies nest three levels at most."""
    validate_notion(env)
    client = Client(auth=env.notion_token)

    def _children(bid, depth):
        out, cursor = [], None
        while True:
            kwargs = {"block_id": bid, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = client.blocks.children.list(**kwargs)
            for block in resp.get("results", []):
                if block.get("has_children") and depth < max_depth:
                    block["children"] = _children(block["id"], depth + 1)
                out.append(block)
            if not resp.get("has_more"):
                return out
            cursor = resp.get("next_cursor")

    return _children(block_id, 0)
