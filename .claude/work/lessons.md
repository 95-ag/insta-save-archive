# Lessons

One entry per lesson. Lead with the rule. Keep it to 2–3 lines max. Add/remove as patterns are confirmed or invalidated.

---

**tasks.md before first file write.** Create tasks.md checklist before writing any project file — even single-file tasks. Hard gate per CLAUDE.md.

**Assumption validation is a task.** Add it to tasks.md. Include a cleanup step. Mark done only after findings are documented and artifacts resolved.

**Headed Playwright is opt-in; headless is the default.** `display.py` handles all X setup. Run with `--headed` for a visible browser; omit for headless (no VcXsrv needed). `display.py` auto-detects IP, auto-launches VcXsrv, and closes it after use.

**WSL2 Windows host IP comes from `ip route`, not `/etc/resolv.conf`.** The nameserver in `/etc/resolv.conf` is the NAT gateway (e.g. `10.255.255.254`) — unreachable for X11. The default gateway from `ip route show default` is the WSL adapter IP (e.g. `172.22.48.1`) — where VcXsrv actually listens. These are different addresses.

**VcXsrv must be launched directly with `:1 -multiwindow -ac -noclipboard`.** XLaunch (GUI) uses `-displayfd` for dynamic display assignment — do not use it. Launch via PowerShell `Start-Process` from WSL. `cmd.exe /c start` silently fails due to UNC path rejection when CWD is inside WSL.

**Hyper-V NAT firewall blocks inbound rules for WSL2.** `New-NetFirewallHyperVRule` with `NATInboundRuleNotApplicable` means the rule was accepted but doesn't apply. The WSL adapter IP (gateway) bypasses this — use it instead of the NAT gateway IP.

**Commit lessons.md with tasks.md and session.md.** When closing out a phase or task group, stage all three together — tasks, session, and lessons — in the same commit. Never leave lessons behind.

**notion-client 3.x query pattern.** `databases.query` removed in 3.x. Use `data_sources.query(data_source_id, ...)`. Resolve data_source_id via `databases.retrieve()`['data_sources'][0]['id']. Schema inspection and property rename require `data_sources.retrieve/update`, not `databases.*`.

**Saved index needs 4s wait.** Instagram's /saved/ index page requires 4s after domcontentloaded for collection links to render. 2s is not enough.

**yt-dlp requires Netscape cookie format.** `session_cookies.json` is JSON; convert to Netscape format before passing to yt-dlp (`--cookies`). Convert at runtime into `tmp/cookies.txt`.

**Instagram `article` selector is dead.** Current IG DOM has no `<article>` element. Use `page.query_selector_all("img")` and filter by content path markers (`/t51.82787-15/`, `/t51.71878-15/`) to identify carousel slide images. Profile pics use `t51.2885-19` / `t51.89012-19`.

**IG CDN domain is `instagram.fblr22-*.fna.fbcdn.net`.** Not `cdninstagram.com`. Filter and download slide images using this domain pattern. Pass session cookies via `urllib.request` header.

**Phase 2 engines locked (post-validation).** Transcript: yt-dlp + faster-whisper `base` int8. OCR: RapidOCR (`rapidocr-onnxruntime`). Both pip-only, WSL2-clean. System dep: ffmpeg (`apt install ffmpeg`). Do not re-evaluate unless quality issues surface in Phase 3.

**Carousel img scope must be `ul img`, not `img`.** Scoping to the full page picks up feed images that load below the post once the carousel reaches its last slide. The carousel slides live in the single `<ul>` on the post page — use `page.query_selector_all("ul img")` to stay within the target carousel.

**Collection names are private — never hardcode in committed files.** Store in `config/collections.json` (gitignored). Discover via `scripts/list_collections.py --update` (smart merge preserves existing group/extract annotations). `pipeline/collections.py` loads at import time and fails clearly if the file is missing.

**Notion schema properties must be created before first write.** Phase 3 enrichment properties (`expanded_summary`, `key_insights`, `extracted_externals`) don't auto-create. Add them once via `client.data_sources.update(ds_id, properties={"name": {"rich_text": {}}})`. `databases.update` does not work in API 2025-09-03 — use `data_sources.update` with the ds_id (not the database_id).

**Venv binaries not on PATH in subprocess calls.** `subprocess.run(["yt-dlp", ...])` raises `FileNotFoundError` because the system PATH doesn't include `.venv/bin/`. Resolve via `Path(sys.executable).parent / "binary-name"` — gives the correct venv-local path regardless of how the script was invoked.

**Instagram lazy-lists must be harvested DURING scroll, not after.** Instagram virtualizes long lists (saved index, collection grids) — items unmount as they scroll off-screen, so a single `.all()` at the end misses most. Accumulate hrefs into a set on every scroll step (`scroll_harvest` in crawler.py). This fixed saved-index discovery from 12 → 43 collections. Pair with incremental `scrollBy(0, innerHeight)` + bottom-detection.

**Don't scrape the /saved/ index for collection URLs — build from collections.json.** The index lazy-loads unreliably (different subset each visit). Collection URLs are deterministic from `slug` + `numeric_id`: `/{user}/saved/{slug}/{numeric_id}/`. Discovery (additive) refreshes those ids; crawling uses them directly.

**Ingest sync safety: presence reliable, absence not.** Finding a post in a collection is certain → ADD always safe (even from an incomplete crawl). NOT finding it is uncertain → REMOVE a tag only when that collection's crawl `complete`, or it's explicitly `--confirm-removed`. This prevents a transient render glitch from stripping valid tags. Reconciliation is a pure function (`reconcile.py`) with unit-tested invariants.

**Notion text fields count UTF-16 code units, not Python chars.** Emoji (non-BMP) cost 2 units. `text[:2000]` can exceed Notion's 2000-unit cap and 400. Use `_notion_truncate` (UTF-16-aware) for single-object fields; `_rich_text_chunked` for full-length fields (caption/transcript/OCR) which split into ≤2000-unit objects.

**Clean terminal = progress only; logs = file only.** Route all `logging` to a file handler (`setup_logging`), pin httpx/notion_client to file, and let `rich` own the terminal (`StageProgress`). Never mix — a stray log line breaks the live display. The module is stage-agnostic and reusable across ingest/extraction/enrichment.

**Priority lives on the item, not the collection.** Pipeline ordering is a per-item Notion `processing_priority` select (High/Med/Low; blank = processed last), set manually. One shared `run_priority_stage` (`pipeline/runner.py`) reads buckets High→Med→Low→blank and is reused by extraction + local enrichment; summarize reuses the bucketed query. Share the loop, not the resources — browser (expand) and Ollama (enrich) stay separate stages with their own status gates. Filter with `query_by_status_and_priority`; `priority=None` → Notion `select.is_empty`.
