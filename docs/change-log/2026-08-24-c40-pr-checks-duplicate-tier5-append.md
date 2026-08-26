# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code session (vikas@ngitservices.com) |
| Surface(s) | docs (ACTION_ITEMS.md) |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md C40 — new finding, no code change |

## 1. Issue / gap identified

`pr-checks.yml`'s "Expand conditional template sections" step re-appends
a blank Tier 5 (UI change details) block to a PR's body on every edit,
even when a filled one already exists earlier in the same body —
observed across three PRs this session (#4469, #4470, #4481).

## 2. Root cause

Inferred from behavior, not from reading the workflow source: the step
appears to check only "is this PR UI-labeled / type:fix" before
appending, not "does a Tier 5/6 section already exist in the current
body".

## 3. Fix / remediation

Recorded as a new backlog item (C40) rather than attempting a workflow
fix inside an unrelated content PR. No code changed.

## 4. Risk & impact on existing functionality

Zero — documentation-only. No workflow file touched.

## 5. User-experience effect

None.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `ACTION_ITEMS.md` | New C40 entry | Record a confirmed, repeatable CI-workflow bug for a future fix |
| `docs/change-log/2026-08-24-c40-pr-checks-duplicate-tier5-append.md` | New change-log | Required |

## 7. Before / after

Additive note only — no prior text removed.

## 8. Rollback plan

**`git-revert-safe`** — pure documentation text.

## 9. Verification performed

- [x] Observed directly across three separate PRs this session (#4469, #4470, #4481)
- [x] Confirmed on #4481 specifically: filled Tier 5 correctly, re-fetched the PR, found a second blank Tier 5 appended at the end
- [x] Confirmed the duplicate does not block the "Required PR fields filled" check (that check still passed on #4481)

## What was NOT verified

- The actual workflow YAML/script source for the "Expand conditional
  template sections" step — root cause is inferred from observed
  behavior, not confirmed by reading the implementation.
- Whether this affects Tier 6 (bug-fix notes) the same way in all cases.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed (doc-only)
- [x] No silent behavior change — none
