# Carry-over from v1

Hard-won fixes, workarounds, and non-obvious decisions that **must survive the v2 rewrite**.
Committed deliberately: `.claude/work/lessons.md` is gitignored, so these would otherwise be lost
in a fresh checkout. ⚠️ marks items found in **code but NOT in `lessons.md`** — highest risk to lose.
Paths are repo-relative to the v1 tree, now archived under `legacy/` (`legacy/pipeline/`, `legacy/scripts/`).

> Source: v1 audit of code + git history + lessons (2026-06-08). When v2 reimplements a stage,
> check this list first.

## Ingest / Scrape

- **Re-auth falls back to headed automatically when cookies expire.** A headless run with stale cookies can't complete manual login; relaunch the browser headed mid-run instead of failing. — `pipeline/session.py:ensure_authenticated`.
- **Auth/login/challenge detection uses specific selectors; never assume `<article>` or generic markers.** Auth = `svg[aria-label='Home']`; login = `input[name='username']`; 2FA = `input[name='verificationCode'], input[name='security_code']`. Login wait `300_000ms` (manual + 2FA); auth check `8_000ms`. — `pipeline/session.py`.
- **Never automate 2FA — block and wait.** `_run_login` opens the page and waits up to 5 min for a human; never types credentials. — `pipeline/session.py:_run_login`.
- **Lazy-lists must be harvested DURING scroll, not after.** IG virtualizes long lists; items unmount off-screen. Accumulate every step. `SCROLL_PAUSE=2.0`, `MAX_UNCHANGED_SCROLLS=3`, `MAX_SCROLLS=80`; bottom test `(window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 50)`. — `pipeline/crawler.py:scroll_harvest`.
- **Build collection URLs from `collections.json` (slug + numeric_id), never scrape the /saved/ index.** `/{user}/saved/{slug}/{numeric_id}/`; `all-posts` → `/{user}/saved/all-posts/`. (Inline version `41f493b` was reverted `e055e1f`, reintroduced correctly in rebuilt crawler `85c9a3b` — keep the rebuilt form.) — `pipeline/crawler.py:resolve_collection_url`.
- **Saved-index render needs a 4s wait after `domcontentloaded`** (2s misses links). Hardcoded in two places — keep both. — `pipeline/discovery.py:discover_collections`, `scripts/list_collections.py:_discover`.
- **Login-redirect is a crawl-completeness signal, not just an error.** A redirect to `accounts/login` returns `complete=False`, so absence of posts is never trusted for tag removal. — `pipeline/crawler.py:crawl_collection`.
- ⚠️ **Post-link / shortcode regexes are load-bearing across crawl + extract.** Links: `a[href*='/p/'], a[href*='/reel/'], a[href*='/tv/']`; shortcode `/(p|reel|tv)/([A-Za-z0-9_-]+)`; index collection href `/saved/([^/]+)/(\d+)`. — `pipeline/crawler.py`, `pipeline/discovery.py:_COLLECTION_HREF_RE`, `pipeline/extractor.py:_SHORTCODE_RE`.
- ⚠️ **`list_collections.py` carries a SECOND, divergent index-scroll** (`scrollTo(0, scrollHeight)` + `unchanged < 3`) instead of shared `scroll_harvest`. **v2 should consolidate onto one harvester.** — `scripts/list_collections.py:_discover`.
- ⚠️ **Browser context pins a desktop Chrome UA + 1280x900 viewport.** Mobile/empty UA changes IG's DOM and gating. Launch args `--no-sandbox --disable-gpu`. — `pipeline/session.py:_new_context` (`Chrome/124.0.0.0 ... Win64`).

## Extract

- **Trust the Notion `type` set at ingest, not re-detection, to pick the extractor.** Reel/IGTV → transcript + OCR frames; Carousel → slide OCR; Post → single-image OCR; Unknown → skip. — `pipeline/extract_runner.py:run_extract_item`.
- **`/p/` type disambiguation: Carousel first, then `video` ⇒ Reel, else Post.** The audio-toggle button (`button[aria-label='Toggle audio']`) was removed as a signal — hover-only, absent in headless, misclassified reels as Post. yt-dlp path refines Post→Reel when `vcodec`/`duration` present. — `pipeline/extractor.py:_detect_type` (`30e2be0`), `extract_metadata_ytdlp`.
- **Carousel image scope is `ul img`, NOT `img`** (full-page `img` grabs feed images loaded below once the carousel ends). Post OCR uses `img` (single image, not in a `<ul>`). — `pipeline/extractor_deep.py:_content_image_urls`.
- **Content images filtered by CDN path markers** to exclude profile pics/UI: `/t51.82787-15/`, `/t51.71878-15/`; profile pics use `t51.2885-19` / `t51.89012-19`. — `pipeline/extractor_deep.py:_CONTENT_PATH_MARKERS`.
- **IG CDN domain is `instagram.fblr22-*.fna.fbcdn.net`, not `cdninstagram.com`.** Image downloads pass session cookies as a `Cookie` header via `urllib.request` + `Mozilla/5.0` UA. — `pipeline/extractor_deep.py:_download_image`, `_load_session_cookies`.
- **yt-dlp needs Netscape cookies converted at runtime** from `session_cookies.json` (JSON `expirationDate`/`secure`/`domain` → tab-separated). — `pipeline/extractor_deep.py:_netscape_cookies`.
- **Venv binaries aren't on subprocess PATH** — resolve yt-dlp via `Path(sys.executable).parent / "yt-dlp"`. ffmpeg is the exception: bare `"ffmpeg"` (system dep). — `pipeline/extractor_deep.py`, `pipeline/extractor.py`.
- ⚠️ **Temp files (audio, video, frames, cookies.txt) are ALWAYS cleaned in `finally`.** cookies.txt deleted per yt-dlp session to reduce on-disk credential exposure (`437e411`). OCR-frames re-downloads the mp4 separately as `{shortcode}_ocr.mp4`. — `pipeline/extractor_deep.py` (every `finally`), `pipeline/ingest.py:sync`.
- ⚠️ **Transcript "available" gate prevents music-only/garbage transcripts.** `False` when empty, `< _TRANSCRIPT_MIN_WORDS (3)` words, or `language_probability < _TRANSCRIPT_MIN_LANG_PROB (0.5)`. Run with `beam_size=5, vad_filter=True, device="cpu", compute_type="int8"`. The `transcript_available` *field* was removed (`c5b9ca1`) but the **in-function gate that nulls bad transcripts remains — keep it.** — `pipeline/extractor_deep.py:transcribe`.
- ⚠️ **Inter-item delay is in a `finally` so it runs even on failure** (anti-429). `random.uniform(min,max)`, env `EXTRACT_DELAY_MIN/MAX`, default 3–7s. — `pipeline/extract_runner.py:run_extract_stage`.
- ⚠️ **Content guard: if transcript + ocr_text + carousel_slides all empty, skip the write — item stays Queued (not Extracted)** so it can be retried. Returns `no_content`. — `pipeline/extract_runner.py:run_extract_item`.
- ⚠️ **Carousel stepping: click `button[aria-label='Next']` until it disappears, 1.0s/click, dedup URLs by seen-set; OCR-frames samples `fps=1` via ffmpeg, dedups lines.** Diagnostics log initial slide count + next-button presence (`59313c2`). `extract_carousel` imports `CAROUSEL_NEXT_SEL` from `extractor.py` (single source). — `pipeline/extractor_deep.py`.
- ⚠️ **Post/carousel page waits a hardcoded 2.5s after `domcontentloaded` before reading images** (`PAGE_LOAD_PAUSE=2.5`, same in metadata extractor). — `pipeline/extractor_deep.py`, `pipeline/extractor.py`.
- ⚠️ **Phase-1 metadata via `yt-dlp --dump-json` is preferred over browser render — more 429-resilient and works on image posts.** `-j --no-warnings --ignore-no-formats-error --cookies`; first JSON line only; author from `uploader|channel|uploader_id`, caption from `description`, date from `timestamp` (epoch) or `upload_date`. — `pipeline/extractor.py:extract_metadata_ytdlp`.
- ⚠️ **Caption = longest `span[dir='auto']`, stripping an `{author}\n\xa0\n{relative_time}\n` prefix** (the `\xa0` on line 2 is the prefix signal). Author = first `a[role='link']` with href `^/[A-Za-z0-9._]+/$` excluding nav hrefs; date = first `time[datetime]`. Brittle DOM heuristic — revalidate against current IG DOM in v2. — `pipeline/extractor.py:_extract_caption`, `_extract_author`.

## Notion adapter

- **API 2025-09-03 / notion-client 3.x: use `data_sources.query/update`, NOT `databases.*`.** Resolve `ds_id` via `databases.retrieve()['data_sources'][0]['id']`. — `pipeline/notion.py:_get_data_source_id`.
- **Notion counts UTF-16 code units, not Python chars.** Non-BMP (emoji) cost 2 units; `text[:2000]` can exceed the 2000-unit cap → 400. `_notion_truncate` (single-object fields) and `_rich_text_chunked` (long fields → ≤2000-unit objects, max 100 ≈ 200k units then warn+drop). — `pipeline/notion.py`.
- **Schema properties must be created before first write** (don't auto-create) via `data_sources.update`. v1 created them manually; **v2 adds an `ensure_*_property` helper.** — `pipeline/notion.py`.
- **Null fields are omitted entirely — never `null`/`""`/`"N/A"`.** Builders return `None`; caller skips. Title is the one placeholder exception. — `pipeline/notion.py:_build_properties`.
- ⚠️ **`raw_extraction` is versioned + append-only** — read existing JSON, add the new `processing_version` key, never overwrite prior versions. Survives re-extraction without data loss. Default `v1.0-base`. — `pipeline/notion.py:write_extraction`.
- ⚠️ **`ocr_text` is synthesized from `carousel_slides` when absent** — joined `[Slide N]\n{text}` so single-field consumers always have OCR text. — `pipeline/notion.py:write_extraction`.
- ⚠️ **`bulk_load_state` replaces per-post dedup with one paginated pass; `needs_metadata` = author OR posted_date missing — deliberately NOT caption** (caption is optional; triggering on it never converges). Pages without `source_id` skipped. — `pipeline/notion.py:bulk_load_state`.
- ⚠️ **All select reads tolerate missing keys via `... or {}`** — Notion can return a select with `null` value. — `pipeline/notion.py`.
- **`make_console_logger` is monkeypatched to a no-handler logger** — it adds a fresh `StreamHandler` on every `Client()` init with no guard and forces WARNING, spamming the terminal. — `pipeline/observability.py:setup_logging` (`a12578b`).

## Terminal / Observability

- **Two streams that never mix: rich owns the terminal, all `logging` → a file.** A stray log line corrupts the live display. `setup_logging` removes root handlers, adds one `FileHandler` to `logs/<stage>_<ts>.log`, pins `httpx/httpcore/notion_client/urllib3/asyncio` to `NullHandler`. — `pipeline/observability.py`.
- ⚠️ **Library retry/timeout WARNINGs are intercepted as a quiet in-place counter, not spam.** `_RetryWatcher` matches `notion_client|httpx|httpcore` records containing `fail|timed out|timeout|retry` → live "↻ retries" indicator; detail still hits the file. — `pipeline/observability.py:_RetryWatcher`.
- ⚠️ **`promote.py` and `list_collections.py` use `logging.basicConfig`, not `setup_logging`** (no live display, so fine) — but **v2 must NEVER mix `basicConfig` with any `StageProgress` stage.**
- ⚠️ **`queue.py` was renamed `promote.py` because it shadowed the stdlib `queue` module.** Don't reintroduce a top-level `queue.py`. — `23c49ca`.

## Config / Collections

- **`config/collections.json` is gitignored + private — never hardcode collection names in committed code.** Loaded at import; fails loudly if missing. — `pipeline/collections.py`.
- ⚠️ **`GROUP_PRIORITY` group *names* are currently in committed code** (Hustling/Content/Creative/Biz/Biz-Clothing/Lifestyle). **v2 decision (open): move group names+order into the gitignored config** for consistency with the privacy stance. — `pipeline/collections.py`.
- **Discovery is additive smart-merge** — refresh slug/numeric_id, preserve `group`/`extract`/`enrichment_order`, never delete; unseen → flagged `missing`, never auto-removed. — `pipeline/discovery.py:_merge_additive`. (Two merge impls exist — `discovery.py` + `list_collections.py` — **consolidate in v2.**)
- ⚠️ **Config split: `load_config()` requires only `IG_USERNAME` + `TARGET_COLLECTION`; Notion creds validated lazily.** Lets non-Notion commands run without creds. Defaults to preserve: `NOTION_WRITE_DELAY=0.4`, `BATCH_SIZE=50`, `PROCESSING_VERSION=v1.0-base`, `WHISPER_MODEL=base`, `EXTRACT_DELAY_MIN/MAX=3/7`, `OLLAMA_MODEL=qwen2.5:7b`, `OLLAMA_BASE_URL=http://localhost:11434`. — `pipeline/config.py`.

## Cross-cutting

- **Reconcile is a pure, unit-tested function with presence/absence safety.** ADD on any sighting (safe even from incomplete crawl); REMOVE only if crawl `complete` OR `--confirm-removed`. Output is an absolute desired set → idempotent. Excluded `{"All Posts","all-posts"}`. — `pipeline/reconcile.py:reconcile`, `tests/test_reconcile.py`.
- **Idempotent writes + durable snapshots are the recovery mechanism, not an in-memory queue.** `set_collections` writes the absolute set (re-apply = no-op); snapshots written the instant a collection is crawled; crash loses at most the in-flight crawl. Snapshot reuse only if `complete` AND younger than `max_age` (default 360 min). — `pipeline/notion.py:set_collections`, `pipeline/snapshots.py`, `pipeline/ingest.py:sync`.
- ⚠️ **Ingest yt-dlp "wall" guard: after `_WALL_AFTER=5` consecutive metadata failures, stop calling yt-dlp and defer to the next run** (rate-limit-wall protection). Per-call throttle `random.uniform(2.0, 4.0)`. Browser fallback only when yt-dlp yields no author. — `pipeline/ingest.py:_extract_meta`.
- ⚠️ **Self-healing backfill: existing pages with `needs_metadata` + a known URL get re-extracted in the apply stage** (defers, doesn't fail, when author still missing). — `pipeline/ingest.py:sync`.
- **Priority lives on the item, not the collection.** One shared `run_priority_stage` reads buckets `["High","Medium","Low",None]` (None last, never dropped); reused by extract/title/summarize; read up front for an accurate total. `priority=None` → `select.is_empty`. — `pipeline/runner.py`, `pipeline/notion.py:query_by_status_and_priority`.
- **Title pass is decoupled from the status machine** — `write_title` writes no status. ⚠️ A **temp** filter currently restricts title to Extracted + `exclude_priorities=["High","Medium","Low"]` (marked `# TODO(temp)`, `a06848f`). **Do NOT carry the temp filter into v2.** — `scripts/title.py`.
- **`route_target` is deterministic from collection membership, not AI-generated.** The model extracts content in the destination's *format*; it never chooses the destination.

## LLM-specific (titler / summarize)

- **Local LLM scope = title only.** Semantic work (externals/summary/tags) → Claude.
- **Ollama `format=<JSON schema dict>` (constrained decoding), NOT `tool_use`** (~40% prose-failure on 7B; schema is ~37x faster on long transcripts). Title schema `{"title": str}`; caption primary, first 300 chars of transcript for thin captions, OCR unused. — `pipeline/titler.py:generate_title`.
- ⚠️ **Title idempotency relies on the placeholder regex `^.+ — [A-Za-z0-9_-]+$`** ({author} — {shortcode}); re-runs skip non-matching titles unless `--force`. If v2 changes the placeholder format, update this regex or every item re-titles. — `scripts/title.py:_PLACEHOLDER_RE`.
- ⚠️ **Summarize batches double-capped to avoid Claude context compaction: `_MAX_ITEMS=30` OR `_CONTENT_BUDGET=100_000` chars, whichever first** (item cap was the fix `8cf512b` — many short items accumulate prompt overhead). — `scripts/summarize.py`.
- ⚠️ **Summary/externals output format is prompt-enforced and load-bearing for Notion readability.** Summary: prose with blank-line paragraph breaks (`83f14e3` — Notion renders single blocks as walls). Externals: `[Category]\n  name — context`, categories Tools/Brands/Creators/Links/Techniques/Locations, full URLs verbatim. — `scripts/summarize.py:_build_prompt`.
- ⚠️ **Upload cleans tmp only on full success (`failed==0 and written>0`)** — preserves batch/prompt/results for retry on partial failure. — `scripts/summarize.py:upload`.

## Headed Playwright / WSL2 X11 (display.py)

- **Headless is default; `--headed` is opt-in and auto-manages VcXsrv.** `ensure_display()` auto-detects IP, TCP-probes port `6001` (=`6000+DISPLAY_NUM`), launches if needed; `close_display()` stops it only if this session launched it. — `pipeline/display.py`.
- **Windows host IP = default gateway from `ip route show default`, NOT the `/etc/resolv.conf` nameserver** (nameserver is the unreachable NAT gateway; gateway is the WSL-adapter IP where VcXsrv listens). — `pipeline/display.py:_windows_host_ip`.
- **Launch VcXsrv directly with `:1 -multiwindow -ac -noclipboard` via PowerShell `Start-Process`.** XLaunch's `-displayfd` is wrong; `-ac` required or remote clients rejected; Hyper-V NAT firewall blocks inbound rules so the gateway-IP path is the only one that works. — `pipeline/display.py:_launch_vcxsrv`.
