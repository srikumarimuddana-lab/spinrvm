# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude Code (interactive session), explicit user go-ahead: "yes, go ahead and build the coverage consolidation fix" |
| Surface(s) | backend (CI tooling only — `.github/workflows/ci-guardrails.yml`) |
| Domain (Sentry tag) | n/a — CI infrastructure, not application code |
| PR / commit link | PR #4595, commit following this log |
| Related issue or gap ID | `ACTION_ITEMS.md` C47 |

## 1. Issue / gap identified

`ci-guardrails.yml`'s `Coverage regression check` and `Money-path coverage floor check` jobs
each independently ran the entire, unscoped backend `pytest --cov=.` suite from scratch just to
slice the resulting `coverage.json` differently afterward — tripling CI compute per PR event
(the third full run being `ci.yml`'s own `backend-test` job, in a separate workflow, deliberately
left out of this fix — see §2). Both jobs were also observed, twice independently on two
different commits of the same PR, getting killed mid-suite by an unexplained external GitHub
Actions cancellation (`exit 143`, "runner has received a shutdown signal", job step conclusion
`cancelled`) rather than any real pytest failure — full investigation in `ACTION_ITEMS.md` C47.
Whatever that external cause is, running the same full suite three times per PR event triples
each gate's exposure window to it.

## 2. Root cause

Historical, not a bug: `money-path-coverage-floor-gate`'s own header comment already documented
that its full-suite run duplicates `ci.yml`'s `backend-test`, and explained that **cross-workflow**
artifact sharing was deliberately rejected as too complex for a first version (see
`docs/change-log/2026-08-19-money-path-coverage-floor-gate-fix.md` §4). That reasoning is sound
and unchanged by this fix — this fix does not touch `ci.yml` or attempt cross-workflow sharing.
What was never done is the much simpler **same-workflow** consolidation: `coverage-regression-gate`
and `money-path-coverage-floor-gate` live in the same file, and nothing about their downstream
logic actually requires two separate pytest invocations — they only ever needed two different
*slices* of one coverage report.

## 3. Fix / remediation

Added one new job, `shared-coverage-run`, that runs `pytest --cov=.` exactly once and uploads
the resulting `coverage.json` as a workflow artifact (`actions/upload-artifact`, 1-day retention
— this is transient CI plumbing, not a report anyone needs after the run completes).
`coverage-regression-gate` and `money-path-coverage-floor-gate` now `needs: [shared-coverage-run]`
and download that artifact (`actions/download-artifact`) instead of running their own full-suite
pytest. Neither job's actual gate *logic* changed — same tolerance math, same Codecov-baseline
fetch, same per-module floor manifests, same scripts, same file paths relative to each job's own
`working-directory: backend`. Only the source of the `coverage.json` they read changed, from
"this job's own pytest run" to "a shared upload from one pytest run."

`corporate-coverage-floor-gate` is explicitly **not** touched — it runs a `-k corporate`-scoped
pytest invocation deliberately narrower than the full suite (see its own header comment), and
swapping it to the full-suite shared artifact would silently change what it measures (a full run
generally shows higher per-module coverage than a keyword-scoped one, which could quietly loosen
an already-tuned, already-tested floor gate). Out of scope, left alone.

Both consuming jobs get `if: always()` so they still run — and reach their existing, unchanged
missing-coverage-report handling — even if `shared-coverage-run` itself fails or is externally
cancelled, rather than silently showing as "skipped" (see §4 for why this matters).

## 4. Risk & impact on existing functionality

- **Blast radius: single file, `.github/workflows/ci-guardrails.yml`.** Grepped the repo for any
  other consumer of `pr-coverage.json`/`money-coverage.json` (the old, now-unused intermediate
  filenames) or of these two job names by ID — only `guardrail-summary`'s `needs:`/status-table
  references them, and those references (job names, `.outputs.baseline_status`) are unchanged.
  `check_money_path_coverage_floor.py`, `_coverage_floor_lib.py`, and
  `check_corporate_coverage_floor.py` are untouched — same CLI contract, same manifests.
- **What else reads/writes the same state:** nothing outside this one workflow file. No
  application code, no database, no production runtime path — this is CI tooling only.
- **New permission added:** `actions: read` at the workflow-level `permissions:` block, required
  for upload/download-artifact. Matches this repo's own existing, proven-working precedent in
  `ci-error-audit.yml` (same permission level, same pair of actions, same SHA pins).
- **Real behavioral tradeoff, stated plainly rather than glossed over:**
  1. **Wall-clock serialization.** These two gates previously ran fully in parallel with every
     other guardrail job. They now wait for `shared-coverage-run` to finish first (a `needs:`
     dependency), so their own start is delayed by however long the shared pytest run takes.
     Total CI *compute* drops (2 full-suite runs instead of 3, within this workflow); total
     *wall-clock time to a final status* on these two specific gates may not — worth watching on
     the resulting PR run below, not assumed.
  2. **Correlated failure.** Before this fix, when the external C47 cancellation hit, it hit each
     job independently — sometimes only one of the two failed, sometimes both, non-deterministically.
     After this fix, if `shared-coverage-run` itself gets hit by that same external cancellation,
     *both* downstream gates lose their coverage data together (though neither silently vanishes
     as "skipped" — see the `if: always()` note in §3, and their existing fail-closed/degrade
     behavior in §1 of the diff). Fewer total full-suite runs per PR event should lower the overall
     *probability* any given PR event hits the unexplained external cause at all — but if it does
     hit the shared run, the blast radius within that one event is now both gates instead of
     possibly just one. This is a real tradeoff, not a strict improvement in every dimension.
- **`ci.yml`'s `backend-test` job remains a third, separate full-suite run** — cross-workflow
  sharing with it is unchanged from the prior, already-documented decision not to attempt it (§2).
  This fix reduces 3 redundant full-suite runs per PR event to 2, not to 1.

## 5. User-experience effect

None. No rider/driver/corporate-admin/internal-admin-facing change whatsoever — this only affects
what a future PR author (including a future Claude session) sees in this repo's own CI checks.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/ci-guardrails.yml` | New `shared-coverage-run` job; `coverage-regression-gate` and `money-path-coverage-floor-gate` now download its artifact instead of running their own full-suite pytest; `actions: read` added to `permissions:`; stale header comment on `money-path-coverage-floor-gate` updated to reflect the new same-workflow-consolidated state. | ACTION_ITEMS.md C47 — eliminate redundant full-suite pytest runs and shrink each gate's exposure window to the unexplained external cancellation. |
| `docs/change-log/2026-08-27-ci-guardrails-coverage-consolidation.md` | New — this file. | CLAUDE.md's mandatory Change Impact Log for a behavior change with repo-wide blast radius (every future PR to `main`/`develop`), even though the surface is CI tooling rather than a product domain. |

## 7. Before / after

```yaml
# Before -- each gate ran its own full-suite pytest independently
money-path-coverage-floor-gate:
  steps:
    - name: Install backend deps
      run: pip install ...
    - name: Run full backend test suite with coverage
      run: pytest --cov=. --cov-report=json:../money-coverage.json ...
    - name: Check per-module floors
      run: python3 scripts/check_money_path_coverage_floor.py --coverage-json ../money-coverage.json ...
```

```yaml
# After -- downloads the one shared run's coverage.json instead
money-path-coverage-floor-gate:
  needs: [shared-coverage-run]
  if: always()
  steps:
    - name: Download shared coverage artifact
      continue-on-error: true
      uses: actions/download-artifact@...
      with:
        name: shared-coverage-${{ github.run_id }}
        path: backend
    - name: Check per-module floors
      run: python3 scripts/check_money_path_coverage_floor.py --coverage-json shared-coverage.json ...
```

`coverage-regression-gate` follows the identical pattern (its own "Run coverage on PR branch"
pytest step replaced by a download step; the small inline coverage-percentage-extraction snippet
kept, just reading the downloaded file instead of one it produced itself).

## 8. Rollback plan

**`git revert` is a complete, sufficient rollback plan here** — this is a pure CI-configuration
change with no data, schema, or production runtime component. No app_settings flag, no migration,
no live data was touched. Reverting the commit restores each gate's independent full-suite pytest
run exactly as it was.

## 9. Verification performed

- [x] **YAML syntax validated**: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci-guardrails.yml'))"` parses clean; confirmed `needs:`/`if:` wiring on both consuming jobs via a structured read of the parsed dict, not just eyeballing the text.
- [x] **File-path tracing performed by hand, end to end**: confirmed the shared job's `pytest --cov=. --cov-report=json:../shared-coverage.json` (run with `working-directory: backend`) writes to the repo root (not `backend/`), matching `upload-artifact`'s `path: shared-coverage.json` (resolved from `GITHUB_WORKSPACE`, unaffected by any step's `working-directory:`); confirmed `download-artifact`'s `path: backend` in both consumers extracts to `backend/shared-coverage.json`, matching both consumers' own `working-directory: backend` + relative-path reads. Caught and fixed one real bug in this exact tracing (an initial `path: backend/shared-coverage.json` on the upload step, which would have pointed at a file that doesn't exist there) before this log was written.
- [x] **Action version/SHA pins matched to this repo's own existing, proven-working precedent**: `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7` and `actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8`, copied from `ci-error-audit.yml`'s already-working usage of the identical pair of actions with the identical `actions: read` permission level, rather than picking new/unverified pins.
- [x] **Downstream script behavior confirmed unchanged by reading `_coverage_floor_lib.py` directly**: a missing/unreadable `coverage.json` already produces a loud, fail-closed `FAIL: expected coverage report at {path} but it does not exist` — this fix's `continue-on-error: true` on the download step degrades into that exact same existing path on a missing artifact, not a new or different failure mode.
- [x] **Blast-radius grep performed** (§4) — no other consumer of the old intermediate filenames or these two job names found outside `guardrail-summary`, which is unchanged.
- [x] **Live-verified — degradation path, on the real next PR run (run `33091467676`, commit `4c76c6f41`)**: `actionlint`/`yamllint` weren't available locally (only `yaml.safe_load`-level syntax was checked pre-push), so this run was the real test. `shared-coverage-run` was hit by the exact same external cancellation this whole fix targets, on its very first live run — an accident that turned out useful, since it exercised the `if: always()` fallback for real rather than only in theory:
  - `coverage-regression-gate` ran anyway; its download step genuinely failed (`Unable to download artifact(s): Artifact not found`), `continue-on-error: true` let it proceed, and it correctly logged `Coverage read failed: [Errno 2] No such file or directory` → landed on the pre-existing, honest "UNKNOWN: no base-branch coverage data available" path (C24) — never a false PASS.
  - `money-path-coverage-floor-gate` also ran anyway with the same artifact-missing error, but printed `No tracked money-path module in this PR's diff -- gate not applicable, PASS` — this PR doesn't touch any of the 5 tracked money-path files, so the script's own scope check short-circuits before it would ever need the coverage file. **This means the fail-closed "coverage report missing AND a tracked file is touched" branch was not exercised** — not a flaw found, an honest gap: this PR's diff never reaches that code path regardless of coverage.json availability.
- [ ] **Not yet verified: the happy path** (shared run succeeds, both gates compute a real coverage number from the shared artifact). Every `ci-guardrails.yml` run on this PR has hit the external cancellation on at least one full-suite job so far (four independent occurrences now, across old and new job graphs alike) — itself the exact problem this fix targets, and this PR appears to have had unusually persistent bad luck with it. Not forcing another push purely to chase a clean run, in the same spirit as the fix's own goal of not burning extra CI cycles on this issue. The next PR that touches a tracked money-path file *and* gets a clean shared run will be the first real end-to-end confirmation of the intended primary path, not just its fallback.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer component).
- [x] Blast radius is stated, not assumed — single file, no other consumers found, explicit
      tradeoffs (wall-clock serialization, correlated shared-job failure) stated rather than
      hidden.
- [x] No silent behavior change to an already-shipped flow — both gates' actual pass/fail logic,
      floor manifests, and Codecov-baseline handling are byte-for-byte unchanged; only the source
      of their input `coverage.json` changed, and their existing missing-file handling was traced
      and confirmed to degrade identically either way.
