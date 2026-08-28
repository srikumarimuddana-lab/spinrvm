# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude, self-initiated — investigating the user's live report ("i checked i didnt see a heatmap on driver app at the top in the middle of the screen") |
| Surface(s) | CI/CD (infra) — `.github/workflows/eas-build.yml`, `eas-native-build.yml`, `deploy-driver-play-testing.yml`, `ci.yml`, `maestro-e2e.yml`, `test-env.yml` |
| Domain (Sentry tag) | n/a (infra/CI, not application code) |
| PR / commit link | commit following this log |
| Related issue or gap ID | Root-caused a multi-day, repo-wide OTA delivery outage while investigating the user's report; not previously tracked in `ACTION_ITEMS.md` |
| Review | `spinr-cicd-infra-reviewer` — first pass found the fix incomplete (3 of 6 exposed workflow/job combinations, plus a step-ordering bug in a 4th file, plus a checked-in comment overclaiming what was verified); all findings addressed in this version — see §3 and §9 |

## 1. Issue / gap identified

The user reported not seeing the demand heatmap on a real driver-app device, despite
production DB state being fully correct (`driver_heatmap_enabled`/`v2_enabled` on,
`heatmap_k_floor=3`, all four rollout service areas `show_demand_heatmap=true`).
Investigating led to a much bigger finding: the **"EAS Mobile Update" GitHub Actions
workflow — the pipeline that pushes JS-only OTA updates to already-installed
rider-app/driver-app devices — has failed on every single run for at least the last
3 days** (30/30 most recent runs, confirmed via `list_workflow_runs`), including
every driver-app change from this session (loading-shimmer fix, consent-checkbox
scoping, swatch-contrast fix). This means the device the user tested on was almost
certainly running a build that predates some or all of today's fixes — the code
itself was never the problem for that specific symptom; the delivery pipeline was.

## 2. Root cause

Pulled the actual failing job's logs directly (`get_job_logs`) rather than guessing
from the "failure" status alone:

```
error @oclif/plugin-autocomplete@3.3.0: The engine "node" is incompatible with
this module. Expected version ">=22.0.0". Got "20.20.2"
error Found incompatible module.
```

`.github/workflows/eas-build.yml`'s `expo/expo-github-action` step is configured
with `eas-version: latest`, which always installs eas-cli's **current** dependency
tree at run time — not a pinned snapshot. Confirmed via `npm view`:

- `eas-cli`'s own `engines.node` has been `>=20.0.0` across every recent version
  checked (20.5.1 through 22.6.0) — eas-cli itself never changed its requirement.
- `@oclif/plugin-autocomplete` (a transitive dependency eas-cli pulls in) bumped
  its own `engines.node` from `>=18.0.0` (3.2.x) to `>=22.0.0` in `3.3.0`,
  published very recently.

The workflow's `actions/setup-node` step was pinned to `node-version: 20`. yarn
classic hard-fails an install on an `engines.node` mismatch (not a warning), so
every run of this workflow since `@oclif/plugin-autocomplete@3.3.0` was published
has failed at the `eas-cli` install step, before ever reaching the actual
`Publish OTA update` step (which the job logs show as `skipped`, not attempted).

`eas-native-build.yml` and `deploy-driver-play-testing.yml` use the identical
`node-version: 20` + `eas-version: latest` + `expo/expo-github-action` combination,
so they carry the same exposure — they simply haven't been directly observed
failing because both are gated behind commit-message triggers (`[build]`,
`[play-test]`) rather than running on every push, so neither has actually run
since the dependency bump.

**A `spinr-cicd-infra-reviewer` pass on the first version of this fix caught that
this list was incomplete.** `grep -rl "expo-github-action" .github/workflows/*.yml`
turns up **six** files, not three:

- `ci.yml`'s `mobile-build` job — no `setup-node` step at all (ran on the
  runner's default Node), gated behind `[build]` + push to `main`.
- `maestro-e2e.yml`'s `maestro-android` job — same, no `setup-node` step at all,
  gated behind `workflow_dispatch` or a `run-maestro` PR label.
- `test-env.yml`'s `build-mobile-test` and `ota-update-test` jobs — **had** a
  `setup-node` step, but it ran *after* "Setup EAS", so it never actually took
  effect for the eas-cli install regardless of what Node version it requested.
  One or the other of these two jobs runs on **every push to `develop`**
  (`ota-update-test`'s condition is unconditional except for excluding `[build]`
  commits), making this the most likely of the three missed files to already be
  failing silently in practice.

All three were missed in the first pass because the review scope (informed by
this investigation) started from "the workflow the user's symptom traces to" and
generalized to its two nearest siblings by name/structure, rather than grepping
the whole `.github/workflows/` tree for the actual failure signature
(`expo-github-action` + `eas-version: latest`) up front — exactly the blast-radius
check CLAUDE.md requires before writing the fix, not after. Fixed in this version:
Node 22 (or, for `test-env.yml`, the bumped shared `NODE_VERSION` env var) added
ahead of every "Setup EAS" step across all six files, and `test-env.yml`'s two
jobs reordered so `setup-node` actually runs first. Re-verified programmatically
afterward — walked every job in all six files and confirmed `setup-node` precedes
`expo-github-action` with `node-version` resolving to 22 in each case; see §9.

## 3. Fix / remediation

Bumped to Node 22 (via `node-version: 22`, or — in `test-env.yml`'s two jobs — the
shared `NODE_VERSION` env var they already read) ahead of every `expo-github-action`
step across **all six** exposed files: `eas-build.yml` (rider + driver jobs),
`eas-native-build.yml`, `deploy-driver-play-testing.yml`, `ci.yml`'s `mobile-build`,
`maestro-e2e.yml`'s `maestro-android`, and `test-env.yml`'s `build-mobile-test` +
`ota-update-test`. Each edit carries a comment explaining the actual root cause, not
just "bump the version." `test-env.yml`'s two jobs also had their step order fixed
(`setup-node` now runs *before* "Setup EAS", not after — see §2).

Comments were revised after the `spinr-cicd-infra-reviewer` pass flagged the first
version's wording as overclaiming: "verified rider-app's/driver-app's own
`yarn install --frozen-lockfile` succeeds under Node 22" is true, but it verifies a
*different* step than the one that actually fails (eas-cli's own install, inside
`expo-github-action`, on a real GitHub Actions runner — which remains unverified
until this workflow actually runs there). The checked-in comments now say this
explicitly, not just this log.

**Before pushing, verified the app toolchain itself tolerates Node 22** (not just
eas-cli): ran `yarn install --frozen-lockfile` for both `driver-app` and
`rider-app` under this sandbox's Node v22.22.2 — both exited 0 cleanly. This
session's entire driver-app/rider-app work today (`npx jest`, `npx tsc --noEmit`,
`npx eslint`, full test suites) already ran under this same Node 22.22.2 all
session with fully green results — corroborating, if indirect, evidence the app
toolchain works under Node 22 in this environment. **Not verified**: whether
GitHub Actions' own `ubuntu-latest` runner behaves identically to this sandbox for
the actual failing step (native module compilation, platform-specific toolchain
differences in `expo-github-action`'s own eas-cli install) — that can only be
confirmed by an actual CI run once this is pushed.

Deliberately did **not** touch `sync-mobile-lockfiles.yml` or
`mobile-bundle-smoke.yml`/`mobile-dep-check.yml`, which also pin Node 20 — none of
them invoke `expo-github-action`/`eas-cli` (confirmed via
`grep -rl "expo-github-action" .github/workflows/*.yml`, which returns exactly the
six files touched here and no others), so there's no evidence they're affected by
this specific issue, and changing them would be unjustified scope creep.

**Not addressed in this fix, flagged as a real follow-up**: the reviewer also
noted that `eas-version: latest` is itself the recurring-instability root cause —
it always tracks eas-cli's current dependency tree, so a future release could bump
its Node floor again (e.g. to `>=24`) and reproduce this exact failure class with
no code change on Spinr's side. Pinning `eas-version` to a specific, verified
release (matching this repo's own SHA-pinning convention for third-party actions)
would close that recurrence risk, but picking and validating a specific version is
its own piece of work — scoped out of this fix, which restores today's break;
tracked as a follow-up rather than silently dropped.

## 4. Risk & impact on existing functionality

- **Blast radius: six CI/CD workflow files, eight job definitions.** No
  application code touched. Confirmed via
  `grep -rl "expo-github-action" .github/workflows/*.yml` — exactly six files
  match, all six now fixed; programmatically re-verified afterward (walked every
  job in all six files, confirmed `setup-node` precedes `expo-github-action` with
  `node-version` resolving to 22 in each). Two other Node-20-pinned mobile
  workflows (`sync-mobile-lockfiles.yml`, `mobile-bundle-smoke.yml`/
  `mobile-dep-check.yml`'s shared `NODE_VERSION`) do not invoke
  `expo-github-action` and were left alone.
- **This is purely a CI/infra change** — it cannot itself introduce an application
  bug. The worst-case failure mode is the *same* failure mode already
  happening (the workflow fails to install its tooling) if Node 22 turns out to
  have some GitHub-Actions-runner-specific incompatibility this sandbox didn't
  surface — in which case OTA delivery stays exactly as broken as it already is,
  not worse.
- **Urgency**: every driver-app/rider-app OTA-only fix merged since this
  workflow started failing (at minimum: the loading-shimmer fix, the
  consent-checkbox scoping, the swatch-contrast fix, and now this workflow fix
  itself once it's live) has been sitting undelivered to real devices. This fix
  is what actually gets them there.

## 5. User-experience effect

**Indirect but significant.** This fix touches no rider/driver-facing screen
itself, but it's the blocking dependency for every recent rider-app/driver-app fix
actually reaching installed devices via OTA — including the heatmap
loading-shimmer fix and consent-checkbox scoping from earlier today. Riders and
drivers on the OTA channel should start receiving those updates once this
workflow run succeeds.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/eas-build.yml` | `node-version: 20` → `22` in both the `rider` and `driver` jobs, with explanatory comments (revised after review) | The confirmed, currently-failing root cause of the multi-day OTA outage |
| `.github/workflows/eas-native-build.yml` | Same bump | Same underlying `expo-github-action`/`eas-version: latest` exposure |
| `.github/workflows/deploy-driver-play-testing.yml` | Same bump | Same |
| `.github/workflows/ci.yml` | Added a missing `setup-node` (Node 22) step ahead of `mobile-build`'s "Setup EAS" step — this job previously had none | Same exposure, missed in the fix's first pass; caught by `spinr-cicd-infra-reviewer` |
| `.github/workflows/maestro-e2e.yml` | Added a missing `setup-node` (Node 22) step ahead of `maestro-android`'s "Setup EAS" step | Same |
| `.github/workflows/test-env.yml` | Bumped the shared `NODE_VERSION` env var `'20'` → `'22'`; reordered `setup-node` to run *before* "Setup EAS" in both `build-mobile-test` and `ota-update-test` (was after — a real bug, not just a missing version bump) | Same exposure plus a step-ordering bug that made the pre-existing `setup-node` step ineffective for the eas-cli install; one of these two jobs runs on every push to `develop` |
| `docs/change-log/2026-08-28-eas-mobile-update-node-version-fix.md` | This log | CI/CD infra fix with real, confirmed production-delivery impact |

## 7. Before / after

```yaml
# Before
- uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7
  with:
    node-version: 20
- uses: expo/expo-github-action@c7b66a9c327a43a8fa7c0158e7f30d6040d2481e # v8
  with:
    eas-version: latest
    token: ${{ secrets.EXPO_TOKEN }}
```

```yaml
# After
- uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7
  with:
    node-version: 22
- uses: expo/expo-github-action@c7b66a9c327a43a8fa7c0158e7f30d6040d2481e # v8
  with:
    eas-version: latest
    token: ${{ secrets.EXPO_TOKEN }}
```

## 8. Rollback plan

`git revert` is complete and sufficient — pure CI config change, no data,
migration, or runtime component. Reverting restores the currently-broken (already
broken today) state; it cannot make OTA delivery worse than it already is.

## 9. Verification performed

- [x] Root-caused from real job logs (`get_job_logs`), not inferred from the
      "failure" status alone.
- [x] Cross-checked `eas-cli`'s and `@oclif/plugin-autocomplete`'s actual
      `engines.node` fields across several recent versions via `npm view`,
      confirming which package's requirement actually changed and when.
- [x] Confirmed via `list_workflow_runs` that every recent run of the affected
      workflow failed identically (30/30) — not a one-off flake.
- [x] `python3 -c "import yaml; yaml.safe_load(...)"` on all six edited files —
      valid YAML.
- [x] `yarn install --frozen-lockfile` for both `driver-app` and `rider-app`
      under Node v22.22.2 — both exit 0.
- [x] **Independent adversarial review** (`spinr-cicd-infra-reviewer`) — first
      pass found the fix incomplete (3 of 6 exposed files, a step-ordering bug in
      a 4th, and an overclaiming comment); all findings addressed and
      re-submitted for this version.
- [x] Programmatic re-verification after the fix: walked every job in all six
      files via a small Python/PyYAML script, confirmed `setup-node` precedes
      `expo-github-action` and resolves to Node 22 (directly or via
      `env.NODE_VERSION`) in all 8 job definitions — not just eyeballed.
- [ ] **Not verified**: an actual GitHub Actions run of any of the six fixed
      workflows. This commit's own push is the first real test on the actual
      `ubuntu-latest` runner — will be watched via the PR's CI to confirm
      `eas-build.yml`'s `driver`/`rider` jobs (the only two that run
      unconditionally on every push) actually go green.
- [ ] Did not confirm this fixes `eas-native-build.yml`, `ci.yml`'s
      `mobile-build`, `maestro-e2e.yml`'s `maestro-android`, or
      `deploy-driver-play-testing.yml` directly — none has a pending trigger
      right now (all gated behind commit-message flags or manual dispatch). The
      fix is applied on the strength of the shared, confirmed root cause and the
      programmatic step-order check, not a direct repro of each one failing.

## What was NOT verified

- Did not confirm receipt of the OTA update on an actual physical device — that
  requires the workflow to run successfully first (this commit's own CI) and then
  a real device to poll the update channel, neither of which is available from
  this sandbox. Watching the PR's CI is the closest available confirmation.
- Did not investigate whether this ~3-day gap caused any other user-facing
  reports beyond the heatmap one already raised — scoped this investigation to
  the specific report at hand.

## 10. Sign-off

- [x] Rollback plan is concrete (plain `git revert`, no data-layer component).
- [x] Blast radius is stated, not assumed — exactly the workflow files that
      share the confirmed root cause, verified by grepping every workflow file
      for the same pattern before deciding which to touch.
- [x] No silent behavior change — this restores an already-broken pipeline to
      (expected) working order; it does not change what the pipeline is
      supposed to do.
