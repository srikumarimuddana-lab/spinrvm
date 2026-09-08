# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude (session implementing PR #5085's hardening plan) |
| Surface(s) | backend (CI/CD infra — no application code) |
| Domain (Sentry tag) | admin (delivery pipeline; not a runtime domain) |
| PR / commit link | branch `claude/pr-5085-5079-hardening-5a2aj7` |
| Related issue or gap ID | F4b, migration-376 nightly baseline, Maestro `matrix`-in-`if` bug — validated in PR #5085 (`docs/audit/2026-09-07-pr-5079-validation-and-hardening-plan.md`, "PR-2"); tracked as `ACTION_ITEMS.md` C74/C75/C76 |

## 1. Issue / gap identified

Three independent CI gaps: (1) `security-gates.yml` and `ci-guardrails.yml`'s summary/roll-up jobs used `if: always()` with no check of their constituent jobs' results, so they always reported success on a PR regardless of whether a blocking gate actually failed. (2) The nightly duplicate-migration-prefix sweep has been red every night since 2026-09-02 because `.known_duplicate_prefixes.json` didn't list the "376" pair, even though both files are already applied in production and accepted as a historical duplicate. (3) `maestro-e2e.yml`'s job-level `if:` referenced the `matrix` context, which GitHub Actions does not make available there — every push produced a zero-duration failed workflow run (3,206 to date).

## 2. Root cause

(1) The summary jobs were written to always post a status comment/summary but nobody added a final "did anything actually fail" gate — `always()` only controls whether the job *runs*, not whether it *passes*. (2) A migration renumber/dedup review accepted "376" as a legitimate historical duplicate but never updated the nightly sweep's baseline file to match. (3) `matrix` is only in scope for `jobs.<id>.strategy` and `jobs.<id>.steps`, never `jobs.<id>.if` — confirmed with `actionlint`, which errors on the original file's line 72 and passes clean after the fix.

## 3. Fix / remediation

(1) Appended a final step to each summary job: `if: contains(needs.*.result, 'failure') || contains(needs.*.result, 'cancelled')` → `exit 1`. `skipped` (path-filtered) and jobs with job-level `continue-on-error: true` (gitleaks in security-gates.yml; coverage-regression-gate, breaking-change-gate, lint-trend-gate in ci-guardrails.yml — all deliberately advisory) are unaffected, since GitHub Actions masks their `.result` to `success` for dependents. (2) Added `"376": [...]` to `.known_duplicate_prefixes.json`, listing both already-applied files. (3) Split `maestro-android` into a new `plan` job (job-level `if:` uses only the `github` context — the real trigger gate) that computes the app matrix as JSON from the `apps` input via an `env:`-scoped step, and `maestro-android` now consumes it via `strategy.matrix.app: ${{ fromJSON(needs.plan.outputs.matrix) }}` with no job-level `if:` of its own.

## 4. Risk & impact on existing functionality

- Blast radius: isolated to these three workflow files plus one JSON baseline file. No application code touched.
- The summary jobs' names ("Security gates summary", "Post guard rail summary") are unchanged, so any future branch-protection required-check config keyed on those names is unaffected.
- A path-filtered `skipped` gate cannot now trip the new failure check (`skipped` is not matched by either `contains(...)` clause) — verified this repo's own PR #5085 (docs-only) shows all its coverage-floor gates as `skipped` and its `Security gates summary`/`Post guard rail summary` as green.
- `maestro-e2e.yml` was not previously a required check anywhere in the repo (grepped) and remains opt-in only (`workflow_dispatch` or a PR labeled `run-maestro`) — this fix makes the workflow *valid* for the first time, it does not newly gate anything. A manual dispatch will now reach the "Setup EAS" step and fail there on the still-missing `EXPO_TOKEN`/Maestro Cloud secrets (tracked separately as B25) — that is the expected next failure, not a regression from this fix.
- Once this lands, `A43`/`C73`'s required-checks list can safely include the two summary jobs' names, since they now actually reflect gate outcomes.

## 5. User-experience effect

None — CI/delivery-pipeline only, no rider/driver/corporate-admin/internal-admin-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/security-gates.yml` | Added a failing final step to the `summary` job | F4b — make the required check actually required |
| `.github/workflows/ci-guardrails.yml` | Added a failing final step to the `guardrail-summary` job | F4b — same |
| `backend/migrations/.known_duplicate_prefixes.json` | Added `"376"` entry | Stop the nightly sweep from being red on an already-accepted, already-applied historical duplicate |
| `.github/workflows/maestro-e2e.yml` | Split `maestro-android` into a `plan` job (computes matrix) + the run job (consumes it via `fromJSON`); `apps` input passed via `env:` | Fix the invalid `matrix`-in-job-`if` reference that failed every run before any job started |

## 7. Before / after

```yaml
# Before (ci-guardrails.yml / security-gates.yml summary jobs)
if: always()
steps:
  - name: Summarize
    run: ...
```

```yaml
# After
if: always()
steps:
  - name: Summarize
    run: ...
  - name: Fail if any required gate failed
    if: ${{ contains(needs.*.result, 'failure') || contains(needs.*.result, 'cancelled') }}
    run: exit 1
```

```yaml
# Before (maestro-e2e.yml) — invalid, matrix not available in job-level if:
jobs:
  maestro-android:
    if: >
      (...) && (
        github.event.inputs.apps == matrix.app.dir
      )
    strategy:
      matrix:
        app: [ {dir: driver-app, ...}, {dir: rider-app, ...} ]
```

```yaml
# After
jobs:
  plan:
    if: github.event_name == 'workflow_dispatch' || (...)
    outputs:
      matrix: ${{ steps.compute.outputs.matrix }}
    steps:
      - id: compute
        env: { APPS: ${{ github.event.inputs.apps }} }
        run: echo "matrix=$MATRIX" >> "$GITHUB_OUTPUT"
  maestro-android:
    needs: plan
    strategy:
      matrix:
        app: ${{ fromJSON(needs.plan.outputs.matrix) }}
```

## 8. Rollback plan

`git revert` — pure workflow/config changes, no schema, no data, no flag. Reverting restores the previous (non-enforcing / red-nightly / invalid-workflow) state exactly.

## 9. Verification performed

- [x] `actionlint` (downloaded binary, Docker unavailable in this sandbox) against all three edited workflow files: clean. Confirmed it correctly flags the pre-existing, unrelated `migration-check.yml` merge_group/paths issue (not touched here).
- [x] Confirmed `actionlint` errors on the original (pre-fix) `maestro-e2e.yml` at the exact `matrix.app.dir` reference (`context "matrix" is not allowed here`), and is clean after the fix — reproduces the defect and its resolution.
- [x] Ran the nightly sweep's own embedded Python script locally against the current `backend/migrations/` directory: `PASS: no new duplicate migration prefixes (66 known historical duplicates, unchanged)`.
- [x] Traced the Maestro matrix-computation logic by hand for all input shapes: `apps=driver-app` → 1 entry; `apps=rider-app` → 1 entry; `apps=both` or unset (the `pull_request` trigger case) → both entries — matches prior intended behavior.
- [x] `spinr-cicd-infra-reviewer` subagent pass: verdict SAFE TO MERGE (confirmed `skipped` is never matched by the new failure check; confirmed gitleaks/coverage-regression-gate/breaking-change-gate/lint-trend-gate's job-level `continue-on-error` masks them to `success` so they can't newly block a PR; confirmed no required-check config depends on job names, which are unchanged).
- [ ] Not run: an actual GitHub Actions execution of these workflows (no Actions-dispatch access from this session). The nightly sweep will next run on its 09:17 UTC schedule, or a human can dispatch it sooner.

## What was NOT verified

Not exercised against a real GitHub Actions run — verified via `actionlint` (syntax/context validity) and by hand-tracing the logic, not by an actual workflow execution in this repo's Actions environment. The summary jobs' new failing step has not been observed catching a real gate failure end-to-end (only reasoned about via GitHub's documented `needs.*.result`/`continue-on-error` semantics).
