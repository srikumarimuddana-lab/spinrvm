# Change Impact & Risk Log — production OTA becomes dispatch-only; rollout-blocked publishes fail legibly

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | Claude Code session (reported by user: "OTA for production keeps failing in Actions") |
| Surface(s) | rider-app / driver-app (release pipeline) |
| Domain (Sentry tag) | rides / drivers (every mobile surface) |
| PR / commit link | branch `claude/brave-clarke-wo8pv7` |
| Related issue or gap ID | follow-on to 2026-09-20 review item C7 (`docs/change-log/2026-09-20-ota-production-gate-rollout-sourcemaps.md`) |

## 1. Issue / gap identified

Every push to main has failed the `rider-production` and `driver-production` jobs of **EAS Mobile Update** since 2026-09-20 22:29. Run 1103 (2026-09-20 20:15) is the last green one — it predates commit `94ade62`; runs 1109-1122 were checked individually and every one fails identically, with 1104-1108 falling in the same window. Production-channel riders and drivers received no OTA in that window, and the single update that did land stayed stuck at 10% of devices.

## 2. Root cause

C7 (commit `94ade62`) started publishing the production channel on **every push to main** with `--rollout-percentage=10`. Expo permits exactly **one active rollout per (branch, platform, runtime version)**, and `eas update` in the pinned eas-cli 24.7.0 has **no non-interactive way to supersede one** — its full flag list is `--branch --channel -m --input-dir --skip-bundler --clear-cache --emit-metadata --rollout-percentage -p --auto --private-key-path --environment --json --non-interactive`. The error text's "publish again specifying it as the rollout to supersede" refers to an interactive prompt that `--non-interactive` cannot reach.

So the first push after C7 created a rollout, and every push after it was refused:

```
Cannot publish an update for runtime version 2.1.0 while a rollout is in progress.
Update 01a0c190-41d0-718e-ad47-a84349da13fa is currently rolling out to 10%.
```

The job had no way to recover on its own. A rollout is human-paced state by design; an unattended push-triggered job cannot end one.

Two conditions let this run for two days unnoticed:

- **The gate that was meant to pace this is inert.** C7 put the production jobs behind `environment: production-ota`, expecting a required reviewer to make each production OTA a deliberate human act (who would then also ramp the rollout). No reviewer was ever configured, so GitHub's auto-created environment has no protection rules. Evidence: in run `35743544726`, `rider-production` went `created_at 14:56:08` → `started_at` 95s later, the same queue delay as the ungated `rider-preview` job — no approval pause. C7's own change log called out this precondition; it was never met.
- **The CI error auditor never fired for it** — see §4, tracked separately.

## 3. Fix / remediation

- **Production OTA is now dispatch-only.** A push to main publishes the `preview` channel only (`android:preview`, ungated, 100%, no rollout — unchanged). `production_pairs` is empty and `is_production=false` on the push path, and both production jobs' `if:` now require `github.event_name == 'workflow_dispatch'` explicitly, so a push cannot reach them by any path. `workflow_dispatch` with `profile=production` is the only route to a production OTA and still runs under `environment: production-ota`.
- **A rollout-blocked publish now says what to do about it.** A `publish()` wrapper in the composite action tees `eas update`'s output; when it fails on `rollout is in progress`, it parses the real update group ID out of Expo's message and re-prints it as `::error::` annotations with the exact remediation commands, instead of leaving the operator to read a wall of text ending in "Error: GraphQL request failed". Failure still fails the job.

**Alternatives considered** (put to the user as an explicit four-way choice; they picked dispatch-only):
- *Auto-ramp the previous rollout to 100%, then publish the new one at 10%* — keeps production on continuous delivery, but auto-promotes an un-verified canary to every production handset on the next merge; at this repo's merge rate that canary window is often minutes.
- *Drop the rollout, publish production at 100% behind the reviewer gate* — no stuck state, but no canary at all, and it depends on the required reviewer that has demonstrably not been configured.
- *Keep 10%, treat "rollout in progress" as a clean skip instead of a failure* — turns CI green while leaving production OTA silently not shipping, i.e. today's state minus the visible red X.

Dispatch-only wins because it is the only option that does not change what reaches real handsets without a human deciding, and it respects the Expo constraint rather than fighting it.

## 4. Risk & impact on existing functionality

- **Blast radius of the composite action:** `.github/actions/eas-ota-publish` is consumed by exactly four jobs, all in `eas-build.yml` (`rider-preview`, `driver-preview`, `rider-production`, `driver-production`). Grep across `.github/` and `docs/` found no other `uses:` of it — every other hit is prose. No reusable-workflow or `workflow_call` consumer exists.
- **Blast radius of `eas-build.yml`:** no workflow triggers on it via `workflow_run`. Every other reference in `.github/workflows/` (`eas-native-build.yml`, `submit-play-store.yml`, `maestro-e2e.yml`, `ci.yml`, `deploy-driver-play-testing.yml`, `test-env.yml`, `detect-changes.yml`, `ci-guardrails.yml`, `sync-mobile-lockfiles.yml`, `mobile-bundle-smoke.yml`) is a **comment** pointing at its Node-22 rationale or its `detect` job as a pattern — none of them read its outputs or depend on its schedule.
- **iOS gets no OTA from a push at all now.** iOS has no `preview` population — App Store installs sit on the `production` channel — so previously a push was the only thing shipping iOS JS changes. iOS JS fixes now require dispatching the workflow with `profile=production`. This is inherent to the chosen option, is called out in the workflow header, and is the single most likely thing to surprise someone.
- **Android preview is unaffected.** Internal-distribution APK testers keep receiving every merge automatically, exactly as before.
- **Bundle-level regressions are still caught on push.** The preview legs still run `eas update` (which re-bundles) for both apps on every push, and `mobile-bundle-smoke.yml` runs on every PR to main. Removing the production leg does not remove bundle coverage.
- **A dispatched production run can still be refused** if the operator has not ended the prior rollout. That is unchanged and correct — but it now fails with actionable annotations rather than a bare GraphQL error, and it fails at a moment when a human is watching.
- **Unrelated, not fixed here — `ci-error-audit.yml` has never fired for this workflow.** Its `workflow_run.workflows` watch list contains `EAS Mobile (Build + Update)`; no workflow in the repo carries that name (this one is `EAS Mobile Update`). `workflow_run` matches on exact name, so the auditor that should have filed an issue on the first failure has been silently dead for this workflow. Fixed in a separate commit so it can be reverted independently.
- **Not addressed here:** the two rollouts currently stuck at 10% (`01a0c190-41d0-718e-ad47-a84349da13fa`, rider runtime 2.1.0; `01a0c190-e04e-7fac-92c4-c56b55cd001e`, driver runtime 2.7.0) still exist in Expo and must be ended by a human with Expo access before the next production dispatch will succeed. No credential in this environment can do it.

## 5. User-experience effect

- **Riders / drivers on the `production` channel:** no change to what is on their device today. Going forward, a production OTA arrives only when someone dispatches it, then to 10% of devices first. Before this fix they were receiving nothing at all, so this is strictly an improvement over the actual (not intended) prior state.
- **Android internal testers on `preview`:** no change whatsoever.
- **Internal:** EAS Mobile Update goes green on pushes. Shipping a production JS fix becomes an explicit action (Actions → EAS Mobile Update → Run workflow → `profile=production`), not a side effect of merging.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/eas-build.yml` | push path emits `production_pairs=` / `is_production=false`; both production jobs' `if:` require `workflow_dispatch`; header + channel-routing comments rewritten to document the constraint and the iOS consequence | stop an unattended job creating state it cannot clear |
| `.github/actions/eas-ota-publish/action.yml` | new `publish()` wrapper: tees `eas update`, and on a `rollout is in progress` failure prints the remediation commands with the real group ID | the blocking error was unreadable, which is why it went unfixed for two days |
| `.github/workflows/ci-error-audit.yml` | watch list: `EAS Mobile (Build + Update)` → `EAS Mobile Update`; comment flagging that `Deploy Backend` matches no workflow either | the auditor that should have filed an issue on the first failure was watching a name nothing has (separate commit) |
| `docs/change-log/2026-09-22-ota-production-dispatch-only.md` | this file | |

## 7. Before / after

```bash
# Before — detect, push arm: production published on every merge
echo "preview_pairs=android:preview"                        >> "$GITHUB_OUTPUT"
echo "production_pairs=ios:production android:production"   >> "$GITHUB_OUTPUT"
echo "is_production=true"                                   >> "$GITHUB_OUTPUT"
echo "rollout=10"                                           >> "$GITHUB_OUTPUT"
```

```bash
# After — push arm: preview only
echo "preview_pairs=android:preview" >> "$GITHUB_OUTPUT"
echo "production_pairs="             >> "$GITHUB_OUTPUT"
echo "is_production=false"           >> "$GITHUB_OUTPUT"
echo "rollout=100"                   >> "$GITHUB_OUTPUT"
```

```yaml
# Before — production job reachable from a push
if: |
  needs.detect.outputs.is_production == 'true' && (
    (github.event_name == 'push' && needs.detect.outputs.rider == 'true') ||
    (github.event_name == 'workflow_dispatch' && (github.event.inputs.app == 'rider-app' || github.event.inputs.app == 'both'))
  )
```

```yaml
# After — dispatch-only
if: |
  needs.detect.outputs.is_production == 'true' &&
  github.event_name == 'workflow_dispatch' &&
  (github.event.inputs.app == 'rider-app' || github.event.inputs.app == 'both')
```

## 8. Rollback plan

`git revert` is a complete rollback here: both files are CI configuration, and neither this change nor its revert touches live data, Expo state, Stripe, wallets, or ride state. Reverting restores push-triggered production publishing — which will immediately start failing again for the original reason, so revert only alongside a decision about the rollout constraint. No deploy, migration, or feature flag is involved. The kill switch for a *bad OTA* is unchanged and independent of this change: `eas update:revert-update-rollout`, which republishes the previous update to 100% of devices.

## 9. Verification performed

- Both changed workflow YAMLs and the action YAML parse (`yaml.safe_load`). Job graph is unchanged: `detect → {rider,driver}-{preview,production}`.
- The `detect` `mode` script was extracted and executed for **every** trigger combination, asserting the resolved outputs:

  | event / profile / rollout | preview_pairs | production_pairs | is_production | rollout |
  |---|---|---|---|---|
  | push | `android:preview` | *(empty)* | `false` | 100 |
  | dispatch / preview | `ios:preview android:preview` | *(empty)* | `false` | 100 |
  | dispatch / test | `ios:test android:test` | *(empty)* | `false` | 100 |
  | dispatch / production / 25 | *(empty)* | `ios:production android:production` | `true` | 25 |
  | dispatch / production / *(unset)* | *(empty)* | `ios:production android:production` | `true` | 10 |

  The three non-production rows are byte-for-byte identical to the pre-change behaviour; only the push row changed.
- The composite action's shell body was extracted, checked with `bash -n`, and executed against stubbed `eas`/`npx` in four scenarios:
  1. dispatch production, both platforms, 10% → 2 publishes each with `--rollout-percentage=10`, **2** source-map uploads (one per platform — confirms the per-platform upload the previous review flagged has not regressed), exit 0;
  2. push preview, 100% → 1 publish with no rollout flag, 1 upload, exit 0;
  3. production refused by an in-progress rollout, using Expo's **verbatim** error text → group ID `01a0c190-41d0-718e-ad47-a84349da13fa` correctly parsed, all three remediation annotations printed, **exit 1** (the job still fails);
  4. an unrelated `eas` failure → exit 1 with **no** spurious rollout remediation.
- `eas update`'s flag list, `eas update:edit`, `eas update:revert-update-rollout`, and the one-rollout-per-branch-platform-runtime rule re-confirmed against the EAS CLI reference for the pinned version 24.7.0 (`docs.expo.dev/eas/cli`, `docs.expo.dev/eas-update/rollouts`).
- `actionlint` v1.7.7 run against both changed workflows (`eas-build.yml`, `ci-error-audit.yml`): **clean, no findings** (with `-shellcheck= -pyflakes=`, since neither is installed here).
- Blast radius established by grep, not assumption — see §4 for the enumerated consumer list.
- `spinr-cicd-infra-reviewer` run against the working-tree diff before commit.

## 10. What was NOT verified

- **No workflow run was executed.** This repo's agent integration has no Actions-dispatch access, so neither the push path nor a `profile=production` dispatch was exercised for real. The `eas`/`npx` stubs reproduce Expo's error text verbatim but are not Expo. A human should: push a rider-app/driver-app change and confirm EAS Mobile Update goes green with only the two preview jobs running, then end the two stuck rollouts and dispatch `profile=production` to confirm the production path still publishes.
- **The stuck rollouts were not ended** — no Expo credential exists in this environment. Until a human ends them, the *next* production dispatch will still be refused (now with readable instructions).
- **The `production-ota` required reviewer was not configured** — that is a repo Settings change, not a file in this diff. The inert-gate conclusion is inferred from job start timings in run `35743544726`, not from reading the environment's protection rules directly.
- **`actionlint` does not lint composite `action.yml` files**, only workflows — so the clean actionlint result covers the two workflow files, not `eas-ota-publish/action.yml`. `shellcheck` is not installed here either, so that action's shell body was verified by `bash -n` plus the four functional stub scenarios above rather than by a static linter.
- No mobile app code changed, so no build, visual, or device verification applies. rider-app and driver-app have no visual-regression tooling in any case.
