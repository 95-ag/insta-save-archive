# Git Rules

## main branch

- main is always stable and runnable
- No large architectural work directly on main — branch first
- On main, undo via `git revert` only — never `reset --hard` or force-push

## Branches

- Branch from main only — never branch off another feature branch
- Naming: `feature-*`, `fix-*`, `refactor-*`, `chore-*`
- Delete merged branches after completion

## Commits

Every commit must:
- Run without errors (scripts execute, imports resolve)
- Represent one logical change
- Not mix unrelated changes
- Never use `git add --all`, `git add -A`, or `git add .` — always stage by explicit file path. Bulk-add picks up secrets, data dumps, and unrelated changes.

Prefer small, focused commits. Separate pipeline logic, schema changes, scripts, and config into distinct commits when practical. Commits should be understandable from the diff without needing full project context.

Refactors stay behavior-safe unless intentionally changing behavior.
Prefer incremental progress over large rewrites.

## Approval gate (mandatory before any staging)

Before running `git add`, `git rm`, `git commit`, `git restore --staged`, or any operation that changes the index:

1. Propose the full commit plan in chat. Each cluster must include:
   - Cluster name and purpose (one phrase)
   - Exact file list to be staged
   - Exact single-line commit message
2. Wait for explicit approval before touching the index.
3. **Inside a plan**: commit clusters and messages are part of the plan. Plan approval covers them; do not re-ask. If the work diverges from the planned clusters, stop and re-propose.
4. **Outside a plan**: explicit propose-and-wait every time. Approval for one cluster never carries to the next.
5. If the user says "don't commit cluster N", drop it entirely — do not stage its files.

Skipping this gate is a process violation, not a shortcut. Recovery (`git reset --soft`) costs more than the proposal.

## Commit message format

**Single line only — no body, no trailing description, no bullet list.** Everything fits in the subject.

- Format: `type: short description`
- Imperative mood — `add`, `fix`, `refactor`, not `added` or `adds`
- Lowercase after the colon, no trailing period
- The message explains the WHY at a glance — not a recap of the diff
- Always pass via `-m "…"` — never via HEREDOC, never via `-F`, never via editor
- Never include `Co-Authored-By` trailers or generator credits

**Prefixes:**
- `feat:` — new feature or pipeline capability
- `fix:` — bug fix
- `refactor:` — code change that neither fixes a bug nor adds a feature
- `style:` — formatting, whitespace, no code change
- `docs:` — documentation
- `chore:` — tooling, process config, maintenance — use for `.claude/`, scripts, rule files
- `test:` — tests
- `perf:` — performance
- `data:` — schema changes, seed data, output format changes
- `build:` — build system, deps, config
- `ci:` — CI config

## Never commit

- Broken or erroring scripts
- Temporary hacks or leftover debug statements
- Abandoned experiments or dead code
- Unused dependencies
- Commented-out legacy code
- `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`
- Secrets, `.env` values, `session_cookies.json`
- Large raw data exports unless explicitly scoped
- OS/editor cruft (`.DS_Store`, `.vscode/` unless intentionally shared)

## PR and merge policy

- Working branches merge into `main` via **squash merge** — keeps `main` history one-commit-per-feature
- The squash commit message is the PR title (single line, same `type: short description` format)
- Working branch is deleted after squash merge — expected, not a loss
- Verify before opening the PR: scripts run cleanly, no import errors, expected output produced

## PR closure — Claude does not execute

Claude never runs `gh pr create`, `gh pr merge`, `git merge`, or `git branch -d` for the working branch. Closure is the user's action.

When the working branch is ready to PR, Claude provides:

1. **PR title** — single line, copy-paste ready
2. **PR body** — markdown, copy-paste ready, structured as: Summary · Changes · Verification · Notes
3. **Next-branch instructions** — exact commands for the user to run after merge:
   ```
   git checkout main && git pull && git checkout -b <next-branch-name>
   ```

## .gitignore discipline

Treat `.gitignore` as part of the rules. Required entries:
- `.venv/`
- `__pycache__/`
- `*.pyc`
- `.pytest_cache/`
- `.env*` (except `.env.example` if you add one)
- `session_cookies.json`
- `.DS_Store`
- Raw data exports and large binary outputs
