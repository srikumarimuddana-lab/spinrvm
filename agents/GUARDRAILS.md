# What this pipeline can't do — read this before trusting any run's output

Every stage in `agents/pipeline.workflow.js` inherits Claude Code's normal
tool-permission boundaries, plus the Spinr-specific rules below. This list exists so
that "the pipeline approved it" is never mistaken for "a human doesn't need to look at
it." Some of these are hard stops (the pipeline physically cannot do the thing);
others are soft stops (it can, but must not proceed without a human saying yes first).

## Hard stops — the pipeline cannot do these at all

- **Cannot touch money that's already moved.** It can write code that changes how a
  future charge, refund, or wallet delta is calculated. It cannot reverse a Stripe
  charge, edit a wallet balance directly, or "fix" a ledger by hand. A `git revert` on
  the code does not undo a payment that already happened — CLAUDE.md says this
  explicitly, and the pipeline has no tool that reaches Stripe's or Supabase's
  production data directly.
- **Cannot apply a migration to production.** `run_migrations.py` requires a real
  `DATABASE_URL` this pipeline is never handed. It can write and review migration
  files; a human runs them.
- **Cannot merge its own pull request.** Every PR the pipeline opens is a **draft**.
  Merging is a separate, human action, always.
- **Cannot see or move real user PII.** No stage has a path to raw rider/driver GPS
  coordinates, full names, phone numbers, government IDs, or payment card numbers —
  the same list CLAUDE.md bans from logs and Sentry applies here. If a stage's own
  output would contain any of these, that's treated as a bug in the run, not
  acceptable output.
- **Cannot change the Saskatchewan regulatory posture.** Trip retention windows (7
  years), GPS retention (3 years), the surge cap (2.5×), driver eligibility rules —
  these are legal facts about the business, not tunable parameters. A stage that
  thinks one of these should change writes that up as a decision for a human, never
  edits the number itself.
- **Cannot silence a failing check to get to green.** No stage may skip, disable, or
  weaken a test, lint rule, or CI gate to make its own PR pass. Root-cause it or say
  clearly it's blocked — matches CLAUDE.md's CI-drive-to-green rules exactly.

## Soft stops — the pipeline can do the work, but a human must approve before Release

A run touching any of these must pause at the Change Review stage (see
`PIPELINE_DESIGN.md`) and wait for an explicit human "go" before the Release stage
opens anything beyond a draft PR:

- Anything touching **rides, dispatch, payments, auth, corporate billing, or safety**
  — the same "live-tested surface" list CLAUDE.md already uses for its Change Impact
  Log requirement.
- Anything that **changes what an already-shipped screen does** — not a crash fix, an
  actual behavior change a rider/driver/admin would notice mid-session.
- Anything where the **blast-radius check comes back unclear** — the agent couldn't
  confidently enumerate every other caller of a shared function/table/component.
- Anything that would **repurpose an existing column or field's meaning**, instead of
  adding a new one.
- **Any new npm/pip dependency** — a supply-chain decision, not a code decision.

## Things the pipeline will not pretend it verified

Borrowed directly from CLAUDE.md's Change Impact Log template, applied to every run,
not just money/auth changes: `progress-report.md` for every run must say plainly what
was **not** checked — no live Supabase hit if only mocks were used, no visual
regression tool run if the change is UI, no real device test if only a simulator ran.
Silence on this is not allowed to imply full coverage; it must be stated.

## If a stage isn't sure

An agent that's genuinely uncertain — about blast radius, about whether something
counts as a live-tested surface, about whether a fix is small enough to just do vs.
large enough to propose-and-wait — stops and writes the question into
`decisions.md` rather than guessing either direction. This mirrors CLAUDE.md's own
"Escalate, don't silently ship, when in doubt" rule; the pipeline doesn't get an
exception from it just because it's automated.

## This list is not exhaustive by design

CLAUDE.md itself changes over time (new gates get added, e.g. the pre-merge release
gates from 2026-08). Every stage reads the live `CLAUDE.md` at run time rather than a
frozen copy of these rules, so a new project-wide constraint applies to the next run
automatically. This file is the pipeline-specific *summary and emphasis*, not the
system of record — CLAUDE.md is.
