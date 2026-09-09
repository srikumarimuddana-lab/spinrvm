# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code session |
| Surface(s) | backend (CI/CD config only — `.github/workflows/ci.yml`) |
| Domain (Sentry tag) | admin (closest fit — deploy-pipeline/CI change, not application code) |
| PR / commit link | (filled in on push) |
| Related issue or gap ID | `ACTION_ITEMS.md` C96 (mitigation, not a confirmed root-cause fix) |

## 1. Issue / gap identified

Every GitHub Actions job in the repo started failing at queue time (rejected before a
runner is assigned, no logs, 2–5s "failures") starting ~20:23 UTC 2026-09-08 — tracked as
`ACTION_ITEMS.md` C96. Root cause requires GitHub billing/Actions-usage access no session
in this repo has, and is not independently confirmed. This entry does not close C96; it
closes a real, separately-verifiable inefficiency in `ci.yml` that is a plausible
contributor regardless of the exact trigger.

## 2. Root cause (of the inefficiency being fixed here, not of C96 itself)

`ci.yml` — the heaviest workflow in the repo (16 jobs: 4 test suites, 3 Playwright E2E
suites, `security-scan`, `docker-image-scan`, `mobile-build`, `deploy-admin`, a smoke
test, `detect-changes`, `python-dependency-audit`) — had no `concurrency:` block at all.
`security-gates.yml`, `pr-checks.yml`, and `ci-guardrails.yml` all already cancel
superseded runs for the same PR (`cancel-in-progress: true`); `ci.yml` did not. Every push
to a PR queued an entirely new 16-job run stacked on top of any still in flight for that
same PR, instead of superseding it.

This compounds a separate, already-fixed bug (C93, same day): `detect-changes`'s missing
`pull-requests: read` permission meant it 403'd on every PR, and every downstream job's
`if:` condition fails open on a non-`success` `detect-changes` result — so every PR ran
the *full* unfiltered job matrix regardless of what changed, for an unknown prior period.
Combined with no concurrency cancellation, a PR pushed to repeatedly in a short window
(this repo had several such PRs today, including 3 commits on PR #5133 within ~30
minutes) could produce multiple full, unfiltered, stacked 16-job runs instead of one
filtered, superseding run — a substantial multiplier on Actions-minute consumption.

This is presented as a plausible contributor to whatever tripped the C96 outage
(GitHub Actions rejecting jobs at queue time is consistent with a spend limit or
included-minutes cap being hit), not as a confirmed cause — that confirmation needs
GitHub billing/usage data this session cannot access.

## 3. Fix / remediation

Added a `concurrency:` block to `ci.yml`, matching the pattern already used by
`security-gates.yml`/`pr-checks.yml`/`ci-guardrails.yml`:

```yaml
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

`cancel-in-progress` is deliberately gated to `pull_request` events only. `ci.yml` also
triggers on `push` to `main`/`staging`, and that path includes `deploy-admin` (Vercel) and
`mobile-build` — cancelling an in-flight deploy because a second commit landed on `main`
seconds later would be a worse failure mode than a few extra minutes of redundant CI, so
push-triggered runs are never cancelled by this change.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one workflow file's top-level `concurrency:` key.** No job
  logic, no `if:` conditions, no application code changed.
- **What else reads/writes this?** Grepped every `concurrency:` block across
  `.github/workflows/*.yml` before editing — this is the only file that lacked one among
  the four PR-triggered heavy workflows; no other file references or depends on `ci.yml`
  running without cancellation.
- **Could this regress a working flow?** The only behavior change: a second push to the
  same PR while `ci.yml` is still running on the prior commit now cancels the prior run
  instead of letting both complete. Since only the latest commit's result is ever acted on
  (required checks re-evaluate against the current head), the cancelled run's result was
  already going to be superseded and ignored — no loss of real signal. Push-triggered runs
  (main/staging deploys) are explicitly exempted, so deploy safety is unchanged.
- **Interaction with background loops / ride state machine / money paths:** none — CI/CD
  config only.

## 5. User-experience effect

None — internal CI pipeline only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/ci.yml` | Added a `concurrency:` block (`group: ci-${{ github.ref }}`, `cancel-in-progress` gated to `pull_request` events) | Stop redundant full-cost runs from stacking on repeatedly-pushed PRs; close the one gap among this repo's 4 heavy PR-triggered workflows that didn't already have this |

## 7. Before / after

```yaml
# Before
  workflow_dispatch:

env:
  PYTHON_VERSION: '3.12'
```

```yaml
# After
  workflow_dispatch:

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

env:
  PYTHON_VERSION: '3.12'
```

## 8. Rollback plan

`git revert`-safe. Removing the `concurrency:` block restores the prior (no-cancellation)
behavior exactly; no live data or already-triggered run is affected either way.

## 9. Verification performed

- [x] YAML syntax validated (`yaml.safe_load` against the full file after editing)
- [x] Grepped every `concurrency:` block in `.github/workflows/*.yml` before editing to
      confirm the pattern used elsewhere (`security-gates.yml`, `pr-checks.yml`,
      `ci-guardrails.yml`) and that `ci.yml` was the genuine outlier
- [x] Confirmed via `list_workflow_jobs`/job timestamps on real recent runs that `ci.yml`
      currently has no cancellation behavior (multiple full runs observed queued/running
      concurrently for the same PR across today's activity)
- [ ] `actionlint` — not available in this sandboxed session (no Docker daemon), same
      disclosed gap as every other CI-config change this session
- [ ] A real GitHub Actions run confirming a superseded PR run actually gets cancelled —
      not observable right now since the repo is mid-C96-outage (every run fails at queue
      time regardless); this is the first real test once C96 clears

## What was NOT verified

- **Whether this inefficiency is actually what triggered C96**, as opposed to being a
  real-but-incidental inefficiency that happened to coexist with an unrelated outage
  trigger — stated as a plausible contributor throughout, not a confirmed cause. Only
  someone with GitHub Actions billing/usage access can confirm the actual trigger.
- **The cancellation behavior itself**, live — C96 makes every current run fail
  identically regardless of this change, so there is no clean way to observe "run A
  properly cancelled by run B" until the platform issue clears.
- **Whether other repos/workflows outside this one contributed to the same account-level
  cap**, if a cap is in fact the cause — out of scope for a single-repo CI config fix.
