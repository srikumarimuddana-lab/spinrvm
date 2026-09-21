# Change Impact & Risk Log — production OTA publishes are gated, rolled out gradually, and symbolicated

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code session (2026-09-20 full-repo review, Phase 0 item C7) |
| Surface(s) | rider-app / driver-app (release pipeline) |
| Domain (Sentry tag) | rides / drivers (every mobile surface) |
| PR / commit link | branch `claude/loving-thompson-amjk8b` |
| Related issue or gap ID | 2026-09-20 review, 🚨 C7 |

## 1. Issue / gap identified

`.github/workflows/eas-build.yml` published an Expo OTA JS update to the `production` channel for both apps on **every merge to main** — no approval, no rollout, no source-map upload. A JS-only regression reached every production handset on next launch, its crashes arrived in Sentry unsymbolicated (source maps were only uploaded at EAS *build* time, but an OTA replaces the bundle), and the only kill switch was another `eas update`.

## 2. Root cause

The workflow grew from a single "publish preview" job; the production leg was added (2026-09-09) without a corresponding gate, and the Expo/Sentry post-update upload step was never wired.

## 3. Fix / remediation

- One composite action, `.github/actions/eas-ota-publish`, does setup → then, **per platform:channel pair**, `eas update` followed immediately by `npx sentry-expo-upload-sourcemaps dist` (the step the Expo guide prescribes after `eas update`; skips with a `::warning::` when `SENTRY_AUTH_TOKEN` / `SENTRY_ORG` / `SENTRY_PROJECT` are not configured). The upload sits *inside* the loop on purpose: every `eas update` re-exports into the same `dist/`, so a single upload after the loop would ship only the last platform's maps — silently, since both publishes succeed and the upload exits 0. Caught by `spinr-cicd-infra-reviewer` before merge; the comment in the action says not to hoist it.
- `eas-build.yml` splits each app into an ungated **preview** job and a **production** job that runs under `environment: production-ota` and publishes with `--rollout-percentage=10` (workflow_dispatch input can override). `detect` resolves pairs/rollout/message once.
- Channel routing is unchanged from the previous file: push → android `preview` (ungated) + ios/android `production` (gated); dispatch keeps the operator's profile uniform.

**Alternatives considered:** (a) `environment:` as an expression on the existing single job — GitHub requires the approval before *any* step, so preview publishes would also wait on a reviewer; rejected. (b) Explicit `release`/`dist` in `Sentry.init` — the Expo integration derives them from the update automatically and the upload script uses the same identifiers, so setting them by hand risks a mismatch; rejected, no app-code change.

## 4. Risk & impact on existing functionality

- **The `production-ota` environment must be created in repo Settings → Environments with a required reviewer.** GitHub auto-creates a referenced environment with *no* protection, so until a human does this the gate is inert (production publishes still run, now as 10% rollouts). Documented at the top of the workflow.
- **One active rollout per branch (Expo constraint).** While a production rollout is in progress the next production publish to that branch fails loudly; an operator must end it first (`eas update:edit` → 100, or `eas update:revert-update-rollout`). At this repo's merge rate that means production OTA becomes deliberately paced by the reviewer, not automatic — that is the point of the change, and the failure is visible, not silent.
- Preview-channel behaviour is unchanged (same pairs, same `--environment` mapping, still ungated, still publishes on every push). The reviewer confirmed byte-for-byte parity of the preview leg and the `test`/`preview` dispatch profiles against the previous file.
- **One intentional tightening beyond parity:** `workflow_dispatch` with `profile=production` previously published ungated; it now routes through the same `production-ota` gate, so manual dispatch cannot bypass the reviewer.
- All five jobs now carry `timeout-minutes` (10 for `detect`, 30 for the publish jobs) — previously none did. Environment-approval wait does not consume the job timer.
- Observability wrinkle (no code bug): `ci-error-audit.yml` triggers on this workflow's `workflow_run: completed`, and a job paused awaiting the required reviewer keeps the whole run non-`completed`, so the auditor does not fire until the approval resolves. Expected, not a failure.
- Secrets: `EXPO_TOKEN` unchanged. New optional `SENTRY_AUTH_TOKEN` (secret) and `SENTRY_ORG`, `SENTRY_PROJECT_RIDER`, `SENTRY_PROJECT_DRIVER` (variables) — until added, the upload step warns and skips, so the workflow cannot start failing because of them.
- No app code, `app.config.ts`, or `eas.json` change; runtime versions and channels untouched.

## 5. User-experience effect

- **Riders / drivers on the production channel:** a new OTA reaches 10% of devices first and the rest only after a human ramps it. Nothing visible except later arrival of JS-only changes.
- **Internal:** a reviewer approval appears on every production OTA run; Sentry issues from OTA bundles show real stack frames once the secrets are configured.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/eas-build.yml` | `detect` resolves pairs/rollout/message; four publish jobs; production jobs under `environment: production-ota`; operator notes | gate + rollout |
| `.github/actions/eas-ota-publish/action.yml` | new composite: setup, per-pair `eas update` (+ `--rollout-percentage`), Sentry source-map upload | shared by the four jobs |
| `docs/change-log/2026-09-20-ota-production-gate-rollout-sourcemaps.md` | this file | |

## 7. Before / after

```yaml
# Before — one job per app, push to main:
PAIRS="ios:production android:preview android:production"
eas update --platform "$platform" --branch "$channel" --environment "$env" --non-interactive --message "$MESSAGE"
```

```yaml
# After
rider-preview:     pairs: android:preview                       # ungated
rider-production:  environment: production-ota                  # required reviewer
                   pairs: ios:production android:production
                   rollout_percentage: 10
# composite: eas update ... --rollout-percentage=10 && npx sentry-expo-upload-sourcemaps dist
```

## 8. Rollback plan

Revert the two files; the previous single-job workflow returns on the next push. No state outside GitHub/Expo is touched. An in-progress Expo rollout is ended with `eas update:revert-update-rollout`, which republishes the previous update to 100% of devices — that is also the kill switch for a bad OTA under the new flow.

## 9. Verification performed

- Both YAML files parse (`yaml.safe_load`); job graph is `detect → {rider,driver}-{preview,production}`.
- The composite action's shell body was extracted and run with `bash -n` (syntax) and dry-run twice with `eas`/`npx` stubbed: with Sentry unset (two publishes, two skip warnings naming each platform) and with it set (two publishes, **two** uploads) — proving the per-platform upload actually fires for both platforms.
- `spinr-cicd-infra-reviewer` run against the diff before commit: no blockers, pins/secret-handling/composite-input threading/parity all verified clean. Its one substantive finding — the single trailing upload clobbering iOS maps — is fixed above; its environment-precondition and `timeout-minutes` notes are addressed in §4.
- `eas update --rollout-percentage`, `eas update:edit`, `eas update:revert-update-rollout` and the one-rollout-per-branch rule confirmed against the Expo docs (`docs.expo.dev/eas-update/rollouts`); `npx sentry-expo-upload-sourcemaps dist` after `eas update` confirmed against `docs.expo.dev/guides/using-sentry`.

## 10. What was NOT verified

- **No workflow run was executed** — the sandbox has no Actions dispatch access and this repo's agent integration cannot trigger `workflow_dispatch`. A human should run `workflow_dispatch` with `profile=preview` on the branch first, then `profile=production`, and confirm (a) the environment approval prompt appears, (b) `eas update:list --branch production` shows a 10% rollout, (c) a test event from the OTA bundle symbolicates in Sentry.
- `actionlint` is not installed here; expression syntax was checked by reading, not by tooling.
- The `sentry-expo-upload-sourcemaps` binary name and `dist/` output were taken from the Expo guide, not from the installed `@sentry/react-native` package (npm registry is blocked in this sandbox).
