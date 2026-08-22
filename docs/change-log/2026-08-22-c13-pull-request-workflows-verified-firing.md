# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-22 |
| Author | Claude Code session (vikas@ngitservices.com) |
| Surface(s) | docs (ACTION_ITEMS.md), infra/CI |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md C13 (`pull_request` workflows silently never fire) — verification note, item stays open |

## 1. Issue / gap identified

C13 documents a 2026-08-10 incident where `CI/CD Pipeline` and other
`pull_request`-triggered workflows never fired on PR #3494, with the root
cause narrowed to a repo-admin-only settings question (Actions restriction
or webhook failure) this session has never had access to check.

## 2. Root cause

Unknown — genuinely unresolved, not something this update claims to fix.
This update only checks whether the symptom (zero `pull_request`-triggered
runs on a PR) currently reproduces.

## 3. Fix / remediation

No code/config change. Queried the GitHub Actions API directly
(`list_workflow_runs`, filtered to `event=pull_request`) and cross-checked
against this session's own 8 PRs opened today — every one had `CI/CD
Pipeline` and every other `pull_request`-triggered workflow fire normally
within low-minute latency. Recorded this as a verification note on C13:
the symptom does not currently reproduce, but the item stays open since
the actual root cause (an admin-only settings question) was never
identified or ruled out — only that it isn't actively blocking PRs today.

## 4. Risk & impact on existing functionality

Zero — this is a read-only verification against the GitHub Actions API,
no code or configuration changed.

## 5. User-experience effect

None — internal CI/process observation only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `ACTION_ITEMS.md` | C13 — added a verification note that the symptom doesn't currently reproduce; item stays open | Give the next session accurate, current signal without falsely closing an unresolved root cause |
| `docs/change-log/2026-08-22-c13-pull-request-workflows-verified-firing.md` | New change-log | Required Change Impact & Risk Log |

## 7. Before / after

No prior text changed — this is an additive note appended before C13's
next section header. No diff to show beyond the addition itself (see the
PR diff).

## 8. Rollback plan

**`git-revert-safe`** — pure documentation text.

## 9. Verification performed

- [x] Queried the GitHub Actions API directly (`list_workflow_runs`, `event=pull_request`) — 2,923 total `pull_request`-triggered runs recorded for `ci.yml`, most recent matching this session's own PR branches.
- [x] Cross-checked against this session's own 8 PRs opened today, all of which had every `pull_request`-triggered workflow fire normally.

## What was NOT verified

- The actual root cause of PR #3494's 2026-08-10 incident — still unidentified. This update does not close that question, only confirms the symptom isn't currently reproducing.
- Whether the symptom could still occur under some specific condition (e.g. first-time external contributor, a particular label, a specific time window) not exercised by this session's own PRs, all of which were opened by the same account under normal conditions.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed (doc-only, zero code impact)
- [x] No silent behavior change — none; this only adds an observational note, doesn't claim resolution
