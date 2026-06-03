# Lessons

One entry per lesson. Lead with the rule. Keep it to 2–3 lines max. Add/remove as patterns are confirmed or invalidated.

---

**tasks.md before first file write.** Create tasks.md checklist before writing any project file — even single-file tasks. Hard gate per CLAUDE.md.

**Assumption validation is a task.** Add it to tasks.md. Include a cleanup step. Mark done only after findings are documented and artifacts resolved.

**Playwright on this machine requires VcXsrv.** WSLg windows are non-interactable. All Playwright runs: `DISPLAY=172.22.48.1:1.0`. VcXsrv must be running on `:1` (`vcxsrv.exe :1 -multiwindow -ac -noclipboard` from Windows).

**Commit lessons.md with tasks.md and session.md.** When closing out a phase or task group, stage all three together — tasks, session, and lessons — in the same commit. Never leave lessons behind.

**notion-client 3.x query pattern.** `databases.query` removed in 3.x. Use `data_sources.query(data_source_id, ...)`. Resolve data_source_id via `databases.retrieve()`['data_sources'][0]['id']. Schema inspection and property rename require `data_sources.retrieve/update`, not `databases.*`.

**Saved index needs 4s wait.** Instagram's /saved/ index page requires 4s after domcontentloaded for collection links to render. 2s is not enough.
