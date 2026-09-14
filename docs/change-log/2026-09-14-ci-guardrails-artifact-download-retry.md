# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (session 011p5WHfBFz6Tschi8iYyJDt), user-authorized (ittalenthire.ca@gmail.com) |
| Surface(s) | backend (CI infrastructure only — `.github/workflows/ci-guardrails.yml`) |
| Domain (Sentry tag) | n/a — this is CI tooling, not an application code path |
| PR / commit link | (filled in after PR opens) |
| Related issue or gap ID | Found while investigating CI failures on PR #5382, run [34814240460](https://github.com/srikumarimuddana-lab/spinrvm/actions/runs/34814240460) |

## 1. Issue / gap identified

On PR #5382's CI run, 2 of the 3 jobs that download the shared `shared-coverage-${{ github.run_id }}` artifact (`coverage-regression-gate` and `money-path-coverage-floor-gate`) hard-failed with `Unable to download artifact(s): Artifact not found for name: shared-coverage-34814240460`, even though `shared-coverage-run`'s own `Upload shared coverage artifact` step had already completed successfully in the same run.

**Scope correction:** an earlier draft of this diagnosis (during this session) incorrectly named 4 jobs as sharing this artifact, including `corporate-coverage-floor-gate`. A full read of the workflow file plus a `needs:`/`download-artifact` grep across the whole file confirmed only 3 jobs actually depend on `shared-coverage-run` and download that artifact: `coverage-regression-gate`, `money-path-coverage-floor-gate`, and `admin-coverage-floor-gate`. `corporate-coverage-floor-gate` has `needs: [detect-changes]` only and computes its own separate coverage into `../corp-coverage.json` via its own scoped pytest run — it never touches the `shared-coverage-*` artifact. This log and the accompanying fix use the corrected 3-job scope.

## 2. Root cause

GitHub Actions' artifact backend has a known category of flakiness: a brief read-after-write lag when multiple jobs in the same run download a freshly-uploaded artifact in parallel. This is an Actions-infrastructure timing issue, not a logic bug in this repository's workflow file — `shared-coverage-run`'s upload step (`if: always()`) had already completed by the time the downstream jobs' download steps ran.

All three affected download steps already carry `continue-on-error: true`, which tolerates the *step* failing — but each job's downstream Python check script (the coverage-regression extraction, `check_money_path_coverage_floor.py`, `check_admin_coverage_floor.py`) correctly and intentionally treats a missing `shared-coverage.json` as a hard failure (this repo's "don't silently swallow errors" convention, CLAUDE.md). So a transient artifact-backend lag today has zero retry between the tolerated download failure and the loud downstream failure — a real reliability gap for something that is not actually a real problem with the PR being tested.

## 3. Fix / remediation

For exactly the 3 affected jobs (`coverage-regression-gate`, `money-path-coverage-floor-gate`, `admin-coverage-floor-gate`), added a step, `id`-tagged, an `id: download_shared_coverage` on the existing (unmodified) `Download shared coverage artifact` step, and a new step immediately after it, `Retry shared coverage artifact download on backend lag`, gated on `if: steps.download_shared_coverage.outcome == 'failure'`. That step retries via the pre-installed GitHub CLI (`gh run download <run_id> -n shared-coverage-<run_id> -D .`, authenticated with `GH_TOKEN: ${{ github.token }}`) up to 3 times, 5 seconds apart, checking for `shared-coverage.json`'s existence between attempts. No new pinned third-party marketplace action was added, per this repo's stated preference to minimize new supply-chain surface — `gh` is already present on `ubuntu-latest` runners.

If the artifact is still missing after retries (i.e. it was genuinely never uploaded — e.g. `shared-coverage-run` crashed before its upload step), the existing downstream Python scripts' own loud failure behavior is completely unchanged: this fix never substitutes a fallback/soft-pass result for a real failure.

`corporate-coverage-floor-gate` was **not touched** — it does not share this artifact (see Section 1's scope correction) and has zero diff (verified via an explicit block-level diff against `origin/main` before opening the PR).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to 3 jobs' download steps only**, within a single CI workflow file (`.github/workflows/ci-guardrails.yml`). No application code, no backend/rider-app/driver-app/admin-dashboard runtime code, no database, no migrations, no money/wallet/ride-state paths are touched.
- Grepped the entire workflow file for every `needs:` and `download-artifact` usage before making any change, to confirm the exact set of consumers of `shared-coverage-${{ github.run_id }}`. Confirmed: exactly 3 jobs consume it (`coverage-regression-gate`, `money-path-coverage-floor-gate`, `admin-coverage-floor-gate`); `corporate-coverage-floor-gate` and every other job in the file (`detect-changes`, `shared-coverage-run` itself, `lint-trend-gate`, `security-posture-gate`, `migration-safety-gate`, `breaking-change-gate`, `mobile-test-placement-gate`, `change-impact-log-gate`, `risk-label`, `guardrail-summary`, etc.) are untouched — confirmed by `git diff` showing exactly 3 hunks, one per target job, and a separate explicit block-diff of `corporate-coverage-floor-gate` against `origin/main` showing zero difference.
- The pre-existing `continue-on-error: true` on each original download step was left in place, unmodified — the new step is purely additive and only activates when that step's `outcome` is `failure`.
- The only behavior change on the happy path (artifact downloads successfully first time, as is the normal case) is a no-op: the new step's `if:` condition evaluates false and it is skipped entirely. On the previously-failing path (artifact-backend lag), the new step now gives the download a few more chances before the job proceeds to its existing fail-closed check — this can only turn a previously-hard-failing job into a passing one when the artifact was genuinely available all along; it cannot mask a genuine upload failure, since the downstream scripts' existing "expected coverage report ... but it does not exist" / exit-1 behavior is completely unchanged.
- No interaction with `backend/core/lifespan.py` background loops, the ride state machine, or wallet/Stripe money paths — this is CI-only.

## 5. User-experience effect

None. This is internal CI infrastructure with no rider/driver/corporate-admin/internal-admin-facing surface. The only "user" affected is a PR author whose coverage-floor checks previously flaked under artifact-backend lag; this should make those checks (and future PRs' coverage-regression/money-path/admin-utils checks) more reliable, with no visible change mid-session to anyone using the live app.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/ci-guardrails.yml` | Added `id: download_shared_coverage` to the existing "Download shared coverage artifact" step, and a new "Retry shared coverage artifact download on backend lag" step immediately after it, in each of `coverage-regression-gate`, `money-path-coverage-floor-gate`, and `admin-coverage-floor-gate` | Retry a transient GitHub Actions artifact-backend read-after-write lag before letting the existing downstream check fail |
| `docs/change-log/2026-09-14-ci-guardrails-artifact-download-retry.md` | New Change Impact Log entry (this file) | Required for any commit changing behavior on a merge-gating CI surface, per CLAUDE.md |

## 7. Before / after

```yaml
# Before (each of the 3 jobs, e.g. coverage-regression-gate)
      - name: Download shared coverage artifact
        continue-on-error: true
        uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8
        with:
          name: shared-coverage-${{ github.run_id }}
          path: backend

      - name: Extract PR branch coverage
        ...
```

```yaml
# After
      - name: Download shared coverage artifact
        id: download_shared_coverage
        continue-on-error: true
        uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8
        with:
          name: shared-coverage-${{ github.run_id }}
          path: backend

      - name: Retry shared coverage artifact download on backend lag
        if: steps.download_shared_coverage.outcome == 'failure'
        working-directory: backend
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          for attempt in 1 2 3; do
            if [ -f shared-coverage.json ]; then
              echo "shared-coverage.json present -- no retry needed."
              break
            fi
            echo "Attempt $attempt: retrying shared-coverage-${{ github.run_id }} download via gh CLI..."
            gh run download "${{ github.run_id }}" -n "shared-coverage-${{ github.run_id }}" -D . && break
            sleep 5
          done

      - name: Extract PR branch coverage
        ...
```

## 8. Rollback plan

Pure CI config, purely additive, applied to no live data. `git revert` of the single commit fully and immediately restores the prior behavior (each job's existing `continue-on-error: true` download step with no retry) with zero deploy or data-migration involved. No feature flag is needed or applicable — this is not user-visible, runtime, or live-data-affecting.

## 9. Verification performed

- [x] `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci-guardrails.yml'))"` passes.
- [x] Re-read the full modified file adversarially after editing: confirmed all 3 new `if:` conditions reference the correct step id (`download_shared_coverage`) that was actually added on the correct preceding step in the same job; confirmed correct YAML indentation (list item vs. property indent matches the file's existing convention throughout); confirmed `continue-on-error: true` is still present, unmodified, on all 3 original download steps.
- [x] Confirmed via `git diff` that exactly 3 hunks changed, one per target job (`coverage-regression-gate`, `money-path-coverage-floor-gate`, `admin-coverage-floor-gate`), 91 insertions / 0 deletions, no other job touched.
- [x] Confirmed via an explicit standalone block-diff of `corporate-coverage-floor-gate` (this file's content for that job, both on this branch and on `origin/main`) that it is byte-for-byte identical — genuinely untouched.
- [x] Confirmed via grep, before making any change, that `needs: [detect-changes, shared-coverage-run]` plus a `download-artifact` step is present in exactly 3 jobs in the file, and that `corporate-coverage-floor-gate` has `needs: [detect-changes]` only with its own separate `../corp-coverage.json` pytest step.
- [x] Blast-radius grep performed: searched the whole file for `needs:`, `download-artifact`, and `shared-coverage` to enumerate every consumer of the shared artifact before editing.

## 10. What was NOT verified

This fix cannot be exercised without a real GitHub Actions run — there is no local Actions simulator in this repo. The retry loop's actual behavior against a live artifact-backend lag (or a live genuinely-missing-artifact case) has not been observed running; only the YAML's syntax, structure, and step-id wiring were checked statically. The real test is watching this and future PRs' `coverage-regression-gate` / `money-path-coverage-floor-gate` / `admin-coverage-floor-gate` checks for reduced flake frequency after this merges.

## Sign-off

- [x] Rollback plan is concrete and testable (single `git revert`, no live-data involvement).
- [x] Blast radius is stated, not assumed: isolated to 3 jobs' download steps in one CI workflow file; `corporate-coverage-floor-gate` and every other job explicitly confirmed untouched.
- [x] No silent behavior change to an already-shipped flow: the only jobs affected are 3 CI gates, and their behavior only differs (for the better) when the download step would otherwise have failed; the User-Experience Effect field above states there is none for the live app.
