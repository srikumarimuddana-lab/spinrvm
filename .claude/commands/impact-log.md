# /impact-log — Scaffold a Change Impact & Risk Log entry

Fills in the mechanical parts of `docs/templates/CHANGE_IMPACT_LOG.md` from the
current diff, so the mandatory log (CLAUDE.md → "Change Impact & Risk Log") has
a real starting point instead of a blank template every time. It does **not**
write the judgment fields for you — Root cause, User-experience effect, and
Rollback plan need a human or Claude's actual reasoning about *this* change,
not a template filler.

## Usage

```
/impact-log                    # scaffold from staged changes (or branch vs main if nothing staged)
/impact-log <short-slug>       # also save a filled copy to docs/change-log/YYYY-MM-DD-<short-slug>.md
```

## Why this exists

Every fix/gap-closure touching a live-tested surface (rides, dispatch,
payments, auth, corporate, safety) requires this log per CLAUDE.md — but as of
the 2026-09-07 Spinr Control Plane audit, `/spinr-swarm` was the only command
that actually generated one; `/pr`, `/commit`, and `/review` all assume a human
writes it from scratch. This command exists so the log gets *started*
correctly from every path, not just the autonomous one.

## What it does

1. Determine diff scope: `git diff --cached` if anything is staged, else
   `git diff main...HEAD`.
2. Auto-fill what the diff can actually tell you — never guess the rest:
   - **Date** — today
   - **Author** — `git config user.name` / the PR author
   - **Surface(s)** — derived from changed path prefixes (`backend/`,
     `rider-app/`, `driver-app/`, `admin-dashboard/`, `shared/`)
   - **Domain (Sentry tag)** — inferred from path (e.g. `routes/payments.py`
     → `payments`, `services/dispatch_service.py` → `dispatch`) — flagged
     `<confirm>` rather than asserted, since path-based inference is a guess
   - **Files modified table** — `git diff --name-status`, one row per file,
     "What changed" left as a one-line stub per file for the author to
     complete, "Why" left blank
   - **Blast-radius starter** — for each changed function/table/component
     name, run a `grep -rn` for other callers/importers and list the hit
     count and files, so §4 opens with real data instead of "checked, looks
     fine" (the exact anti-pattern CLAUDE.md calls out)
3. Leave every judgment field as an explicit `<TODO: ...>` placeholder —
   Root cause, User-experience effect, Rollback plan, Before/After snippet,
   Verification performed, Sign-off. Never fabricate these from the diff
   alone; a plausible-sounding guess here is worse than an honest blank,
   since CLAUDE.md's own gate exists specifically to stop rubber-stamped
   entries.
4. Print the filled template to paste into the PR description (Tier 5–7
   money/migration/high-risk sections in `pr_checks.yml`'s `expand-sections`
   job already look for this content), or write it to
   `docs/change-log/YYYY-MM-DD-<slug>.md` if a slug was given.

## Conventions

- This command drafts the mechanical sections; it does not replace the
  judgment CLAUDE.md's gate requires. Every `<TODO>` must be resolved by a
  human or by Claude reasoning about the actual change before the PR is
  ready — an unresolved `<TODO>` in a submitted log is the same violation as
  never running this command.
- Re-run it after amending the diff — it does not track incremental changes.
- If the change doesn't touch a live-tested surface at all, say so and skip
  the log rather than filling one in for form's sake — CLAUDE.md's gate is
  scoped to rides/dispatch/payments/auth/corporate/safety, not every diff.

## When to use it

- Right before `/pr`, on anything touching a live-tested surface
- Any bug fix or gap-closure CLAUDE.md's Pre-merge release gates section covers
- When `/review` or a `spinr-*` agent flags that a Change Impact Log is required and none exists yet

## When NOT to use it

- `Type: trivial` diffs (formatting, typos, comments, lockfile-only) — CLAUDE.md's own gate doesn't require one here either
- Pure documentation changes with no code path touched
- Anything already logged this session — re-running just to "check the box" produces exactly the boilerplate CLAUDE.md warns against

## Do NOT

- Do not invent a rollback plan, root cause, or UX-effect statement the diff doesn't support — leave the `<TODO>` and say what's actually known
- Do not mark Sign-off checkboxes complete on the scaffold's behalf — those are the human/reviewer's attestation, not this command's
- Do not skip the blast-radius grep step because the diff "looks isolated" — that's the judgment call CLAUDE.md's gate exists to force, not skip
