# Insta Save Archive

@work/session.md
@work/tasks.md
@work/lessons.md


## Read First

Before any task:
- `.claude/docs/ARCHITECTURE.md` — v2 architecture: stages, run modes, backends, engines, Notion schema, decision log
- Archived v1 (reference only): `.claude/docs/archive/PROJECT.md`, `.claude/docs/archive/IMPLEMENTATION_PLAN.md`

Current session state → `.claude/work/session.md`

## Stack

Python. Playwright for browser automation. Notion Python client for API writes.
No infrastructure beyond local machine. No daemons, workers, or cloud services.

## Rules

- `.claude/rules/git.md` — branching, commits, staging discipline

## Phase Gate (non-negotiable)

Do not implement Phase N+1 until Phase N exit criteria are met against real Instagram content.
Real-world findings override planning assumptions — update the plan, not the architecture.

## Architecture Escalation Rule

Prefer changes to implementation, prompts, selectors, extraction logic, or planning assumptions before introducing new infrastructure or abstractions. Complexity requires evidence.

If implementation findings suggest the architecture or plan needs to change, surface the conflict and proposed change to the user before modifying `.claude/docs/PROJECT.md` or `.claude/docs/IMPLEMENTATION_PLAN.md`. Never update those docs unilaterally.

## Data Integrity

Null fields are stored as `None` — never as empty strings, `"N/A"`, or placeholder text.
Exception: `title` in Phase 1 uses `{author} — {shortcode}` until AI titles are implemented (Phase 2+).

## Sensitive Files

`session_cookies.json` and `.env` are gitignored and must never be staged or committed.
Do not attempt to automate Instagram 2FA — pause and wait for manual input.
