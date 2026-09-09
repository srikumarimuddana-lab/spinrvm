# Change Impact & Risk — Play Store production release path for rider + driver apps

**Date:** 2026-09-09
**Surfaces:** `rider-app`, `driver-app`, CI (`.github/workflows/eas-native-build.yml`)

## Issue/gap identified

The rider app has never been published to Google Play, and no workflow in the
repo could have published it: `eas-native-build.yml` advertises an
`auto_submit` input but never provisions the Play service-account key its
Android submission needs.

## Root cause

Two independent causes, both confirmed against real EAS build history rather
than assumed:

1. **No Play credential in the generic workflow.** Both apps' `eas.json`
   submit profiles pin `serviceAccountKeyPath: ./play-service-account.json`.
   `eas-cli` reads that file locally when registering a submission, so an
   Android `--auto-submit` fails without it. Only
   `deploy-driver-play-testing.yml` ever decoded the secret — which is why
   that workflow is the sole path that has ever reached Play.
2. **Rider was never built as an AAB.** `eas build:list` for
   `8f1e4f60-720e-46b0-9b71-33c13d3af043` shows all 14 Android builds on the
   `preview` profile, every artifact a `.apk`. Play requires an AAB. The
   driver app, by contrast, has a working `android-auto` AAB line (build 24,
   2026-09-08).

## Fix/remediation

- `eas-native-build.yml`: decode + `jq`-validate `PLAY_SERVICE_ACCOUNT_JSON`
  into `<app>/play-service-account.json` before the build, and `rm -f` it in
  an `if: always()` cleanup. Gated on
  `auto_submit == 'true' && platform != 'ios'`.
- Both `eas.json` files: `submit.production.android` retargeted from
  `track: internal` / `releaseStatus: draft` to `track: production` /
  `releaseStatus: completed`, per an explicit product-owner decision recorded
  in session `session_01SJdEKLCLLLUDY7o544gTsC`.

## Risk & impact on existing functionality

**Blast radius of the `eas.json` change — every consumer of the `production`
submit profile**, found by grepping the workflow directory for `--auto-submit`
and `eas submit`:

| Consumer | Uses | Affected? |
|---|---|---|
| `eas-native-build.yml` | `--auto-submit` when `profile == production` | **Yes** — now targets the production track |
| `deploy-driver-play-testing.yml` | `--auto-submit-with-profile android-auto` | No — reads the separate `android-auto` submit profile (still `internal`/`completed`), untouched |
| `ci.yml` `mobile-build` | `eas build --profile production`, no submit flag | No — builds only, never submits |
| `test-env.yml` `build-mobile-test` | `--profile preview` | No |
| `eas-build.yml` | `eas update` (OTA only) | No |

The `production` **build** profile is unchanged; only the **submit** target
moved. No app code, no backend code, and no ride/payment/auth path is touched
by this diff.

**The material risk is the release-status change, not the code.**
`releaseStatus: completed` on `track: production` is an immediate 100% rollout
to all Google Play users, with no staged-rollout gate and no draft step for a
human to review in Play Console first. The previous setting (`internal` /
`draft`) could not reach a real user at all. See "What was NOT verified".

## User experience effect

Rider- and driver-facing, and visible to people already using the app: a
production release makes the build available to **all** Play users, and
existing installs auto-update. Anyone mid-ride during a rollout keeps their
running JS bundle until the app is restarted, so no session is interrupted at
the moment of publish.

## Files modified

| File | What changed | Why |
|---|---|---|
| `.github/workflows/eas-native-build.yml` | Added Play key decode/validate + cleanup steps; documented two prerequisites | Android `--auto-submit` could not work without the key file |
| `rider-app/eas.json` | `submit.production.android`: `internal`/`draft` → `production`/`completed` | Product-owner decision to release publicly |
| `driver-app/eas.json` | Same | Same |

## Before/after

```jsonc
// before — could not reach a real user
"android": { "track": "internal",   "releaseStatus": "draft" }
// after  — immediate 100% public rollout
"android": { "track": "production", "releaseStatus": "completed" }
```

## Rollback plan

Not a `git revert` — a production release is already-applied outward-facing
state, and reverting the config does not unpublish a live build.

1. **Play Console → Production → Halt rollout.** This is the real rollback and
   needs no deploy. Halting stops further distribution; users who already
   updated keep the build.
2. To put the previous binary back in front of users, re-promote the prior
   production release in Play Console (Play retains it) — do not rely on
   uploading a lower `versionCode`, which Play rejects.
3. To stop *future* automated releases, revert the two `eas.json` hunks; that
   affects the next submission only.
4. A JS-only regression can also be corrected without a store round-trip via
   `eas-build.yml`'s OTA channel, subject to `runtimeVersion` matching.

## Verification performed

- Both changed workflow files parse under `yaml.safe_load`, with the expected
  step sequence and ordering.
- Both `eas.json` files parse under `json.load`; the resulting
  `submit.production.android` object was printed and inspected.
- Blast radius established by grepping every workflow for `eas submit` /
  `--auto-submit`, and confirming `android-auto` reads a different profile.
- Real EAS build history queried for both projects (not assumed) to establish
  the rider APK-only finding and the driver AAB line.

## What was NOT verified

- **No production build was run for either app** (`npm run build` has no
  analogue here; the equivalent is an EAS build). This environment has no
  `EXPO_TOKEN`, no `eas` CLI, and no npm registry access, so the submit path
  is unexercised end to end.
- **The `PLAY_SERVICE_ACCOUNT_JSON` secret's existence and validity are
  unconfirmed.** `EXPO_TOKEN` is recorded as present (ACTION_ITEMS B25);
  this one is not. The new step fails fast with an actionable message if it is
  missing.
- **Rider-app submission is expected to FAIL on the upload key.**
  `docs/runbooks/android-signing-fingerprint-mismatch.md` §3 records, dated
  2026-09, that Play's upload certificate for `com.spinr.user` is
  `D3:C7:7E:B0:…:43:7A` while EAS's only credentials set is
  `26:39:10:88:…:7C:DB`, with no second set to switch to. No change-log entry
  records a reset since. Until the §3b upload-key reset is requested and
  applied by Google (1–2 business days), a rider AAB will be rejected with
  "signed with the wrong key". The driver app's equivalent mismatch was
  resolved in 2026-08 and is not affected.
- **Staged rollout was not used.** `releaseStatus: completed` was chosen
  explicitly; `inProgress` with a `rollout` fraction remains available if a
  percentage rollout is preferred later.
- No visual-regression tooling exists for rider-app or driver-app, so no
  screenshot comparison backs this change — it is config-only and renders
  nothing, but the disclosure is stated rather than left implied.
