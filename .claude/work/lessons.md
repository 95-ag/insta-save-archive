# Lessons

One entry per lesson. Lead with the rule. Keep it to 2–3 lines max. Add/remove as patterns are confirmed or invalidated.

---

**tasks.md before first file write.** Create tasks.md checklist before writing any project file — even single-file tasks. Hard gate per CLAUDE.md.

**Assumption validation is a task.** Add it to tasks.md. Include a cleanup step. Mark done only after findings are documented and artifacts resolved.

**Playwright on this machine requires VcXsrv.** WSLg windows are non-interactable. All Playwright runs: `DISPLAY=172.22.48.1:1.0`. VcXsrv must be running on `:1` (`vcxsrv.exe :1 -multiwindow -ac -noclipboard` from Windows).

**Commit lessons.md with tasks.md and session.md.** When closing out a phase or task group, stage all three together — tasks, session, and lessons — in the same commit. Never leave lessons behind.

**notion-client 3.x query pattern.** `databases.query` removed in 3.x. Use `data_sources.query(data_source_id, ...)`. Resolve data_source_id via `databases.retrieve()`['data_sources'][0]['id']. Schema inspection and property rename require `data_sources.retrieve/update`, not `databases.*`.

**Saved index needs 4s wait.** Instagram's /saved/ index page requires 4s after domcontentloaded for collection links to render. 2s is not enough.

**yt-dlp requires Netscape cookie format.** `session_cookies.json` is JSON; convert to Netscape format before passing to yt-dlp (`--cookies`). Convert at runtime into `tmp/cookies.txt`.

**Instagram `article` selector is dead.** Current IG DOM has no `<article>` element. Use `page.query_selector_all("img")` and filter by content path markers (`/t51.82787-15/`, `/t51.71878-15/`) to identify carousel slide images. Profile pics use `t51.2885-19` / `t51.89012-19`.

**IG CDN domain is `instagram.fblr22-*.fna.fbcdn.net`.** Not `cdninstagram.com`. Filter and download slide images using this domain pattern. Pass session cookies via `urllib.request` header.

**Phase 2 engines locked (post-validation).** Transcript: yt-dlp + faster-whisper `base` int8. OCR: RapidOCR (`rapidocr-onnxruntime`). Both pip-only, WSL2-clean. System dep: ffmpeg (`apt install ffmpeg`). Do not re-evaluate unless quality issues surface in Phase 3.

**Carousel img scope must be `ul img`, not `img`.** Scoping to the full page picks up feed images that load below the post once the carousel reaches its last slide. The carousel slides live in the single `<ul>` on the post page — use `page.query_selector_all("ul img")` to stay within the target carousel.

**Venv binaries not on PATH in subprocess calls.** `subprocess.run(["yt-dlp", ...])` raises `FileNotFoundError` because the system PATH doesn't include `.venv/bin/`. Resolve via `Path(sys.executable).parent / "binary-name"` — gives the correct venv-local path regardless of how the script was invoked.
