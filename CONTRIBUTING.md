# Contributing to Spinr

This is a working guide for anyone (human or AI agent) making changes to this repo. It doesn't
replace `CLAUDE.md` — that file is the authoritative engineering-rules doc and takes precedence
on anything it covers. This file is the shorter "how do I actually open a PR here" companion.

## Before you start

- Read the root [`CLAUDE.md`](./CLAUDE.md) — working style, mandatory task decomposition,
  release gates, and the Change Impact & Risk Log requirement for anything touching a
  live-tested surface (rides, dispatch, payments, auth, corporate, safety).
- Check [`ACTION_ITEMS.md`](./ACTION_ITEMS.md) for current priorities — it's the live backlog.
  `.claude/context/sprint-current.md`, `.planning/ROADMAP.md`, and `.planning/STATE.md` are
  historical and self-flagged stale; don't treat them as current.
- If your change is ambiguous in scope, state the ambiguity and ask rather than guessing —
  see "Think before coding" in `CLAUDE.md`.

## Local setup

See [`README.md`](./README.md)'s Setup Instructions for backend/frontend environment setup, and
each surface's own manifest (`backend/requirements.txt`, `rider-app/package.json`,
`driver-app/package.json`, `admin-dashboard/package.json`) for its specific toolchain.

## Branching & commits

- One feature branch per unit of work; branch names typically follow `claude/<slug>` or
  `<initials>/<slug>` conventions already in use in this repo's history.
- Limit each commit to one logical change (see `CLAUDE.md`'s "Batch size rule" — split a diff
  if it exceeds ~200 lines of unrelated changes).
- Never force-push over history other people rely on; never rewrite a published commit unless
  explicitly asked.

## Before opening a PR

1. Run the relevant surface's tests and lint/format checks (`pytest` + `ruff` for backend; each
   frontend surface's own `lint`/`typecheck`/`test` scripts).
2. For anything touching a live-tested surface, fill in a Change Impact & Risk Log entry per the
   template at `docs/templates/CHANGE_IMPACT_LOG.md` — paste it into the PR description or add
   it under `docs/change-log/YYYY-MM-DD-<slug>.md`.
3. For `admin-dashboard` changes to one of the 6 visual-regression-seeded pages (see `CLAUDE.md`
   §6 of "Pre-merge release gates"), expect the Playwright visual-regression job to fail on an
   intended UI change — say so in the PR rather than assuming the diff is spurious; baselines are
   re-seeded by a human via `update-visual-baselines.yml`.
4. Check the repo's PR template at `.github/pull_request_template.md` and fill in its sections.

## Review routing

See [`.github/CODEOWNERS`](./.github/CODEOWNERS) — money/schema/security/dispatch/safety paths
route to specific reviewers; everything else needs at least one reviewer.

## Style

- Match existing code style in whatever file you're editing over introducing a new convention.
- Don't reformat or "clean up" unrelated code while you're in a file for something else — see
  "Surgical changes" in `CLAUDE.md`.
- No speculative abstractions, no unused configurability — see "Simplicity first" in `CLAUDE.md`.

## Questions

If something in this file conflicts with `CLAUDE.md`, `CLAUDE.md` wins — open an issue or flag it
in your PR description so this file can be corrected.
