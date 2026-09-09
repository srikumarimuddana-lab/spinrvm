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

---

## Addendum — OTA reachability for the new production-channel Android installs

**Issue/gap identified.** Cutting the first Play production AAB creates an
Android population the OTA workflow cannot reach. Found while watching the
first `production`-profile Android build report `channel: production`.

**Root cause.** `eas-build.yml`'s push path published
`--platform android --branch preview` only. That was correct while every
Android store build used the `preview` channel — a fact that workflow's own
comment relied on explicitly. The `production` build profile sets
`channel: production`, so a Play-installed device resolves updates from branch
`production`, which received only `--platform ios` publishes. An Android
device there finds no applicable update: the OTA hotfix path silently does not
exist for Play users.

**Fix.** On push, android now publishes to **both** branches
(`ios:production android:preview android:production`). Additive — `preview`
receives exactly what it received before, so existing internal-distribution
testers are unaffected.

**Risk & impact.** One extra `eas update` invocation per push (3 instead of 2
per app). `workflow_dispatch` is unchanged: an operator's explicit profile
choice still applies uniformly to both platforms. No consumer reads the
removed `CHANNEL_IOS`/`CHANNEL_ANDROID` variables — grep confirms the only
remaining references were in a comment, which was rewritten.

**Verification performed.** Both workflow files parse under `yaml.safe_load`.
The channel-selection logic was re-implemented in a test script and asserted
across all four trigger shapes (push, schedule, dispatch-preview,
dispatch-production); on push it asserts android serves both channels and iOS
publishes exactly once. The `for pair in $PAIRS` loop was executed in a real
shell and emits exactly the three intended `eas update` commands with the
correct `--environment` for each.

**What was NOT verified.** No real `eas update` was run — no `EXPO_TOKEN` or
`eas` CLI in this environment. That the `production` channel is actually
linked to a branch named `production` for both projects is assumed from the
existing comment's stated convention, not confirmed against the EAS dashboard;
if it is not, the first push-triggered android production publish will fail
with "Channel has no branches associated with it" and needs a one-time
`eas channel:edit production --branch production`.

---

## Addendum 2 — internal-track-first, and the App Check finding that drove it

**Decision changed mid-session.** The initial product-owner choice was
`track: production` / `releaseStatus: completed` (immediate 100% rollout).
After the finding below, that was revised to **submit to `internal` first,
verify on a Play-signed install, then promote to production in Play Console**
— a one-click promotion of the *same* AAB, no rebuild. Both apps'
`submit.production.android` therefore now read `track: internal`,
`releaseStatus: completed` (`completed`, not `draft`, so internal testers can
install immediately for the verification).

### The finding: App Check fails closed and the client degrades silently

`backend/core/middleware.py` mounts
`FirebaseAppCheckMiddleware(enforcement_enabled=is_production)`. Under
enforcement, a missing or invalid `X-Firebase-AppCheck` header returns **401 on
every `/api/*` route** — the app is entirely non-functional, not degraded.

On the client, `shared/services/firebase.ts`'s `getAppCheckToken()` returns
`null` on *any* failure and only `console.log`s the cause. So a device that
cannot attest produces no header, gets 401 on every call, and surfaces no
diagnostic beyond a dev-console line.

Why that mattered specifically here: **Play Integrity had never been exercised
for `com.spinr.user` from a Play-signed build** — this is its first Play
distribution ever. Play-distributed installs are signed with Google's *app
signing key*, not the upload key, so attestation depends on the Firebase/Play
Integrity linkage for that package, which no prior build had proven. Both
`google-services.json` files additionally carry `"oauth_client": []` for both
packages, i.e. no SHA-1 registered against either Firebase Android app
(runbook §4b) — that field governs Google Sign-In rather than App Check
directly, but it is consistent with the linkage never having been set up.

Combined with **317 active installs on the pre-rewrite 1.0.2** (Play Console,
supplied by the product owner), a 100% rollout risked locking every existing
rider out of a working app, with recovery requiring a halt plus a reinstall.

The driver app is the contrasting case: it has been Play-distributed on the
internal track since 2026-08 against the same Firebase project
(`spinrapp-6e464`), so its attestation path has real-world evidence behind it.

### Upload key — resolved

The rider signing blocker recorded in "What was NOT verified" above is
**closed**. The product owner performed the §3b upload-key reset; Play now
registers SHA-1 `26:39:10:88:F5:85:BB:62:E0:70:DD:10:32:DC:09:C2:A0:28:7C:DB`,
which is the keystore EAS already holds — the cheap path the runbook
recommends, with no new key minted and no EAS change needed.

### Version code — no collision

Play's highest existing versionCode for `com.spinr.user` is **3** (v1.0.2,
2026-03-30). The new build is **15**, and the driver's is **25**. Both clear.

### Verification performed

- Middleware enforcement and its 401 paths read directly from
  `backend/core/middleware.py`; the `enforcement_enabled=is_production` mount
  confirmed at its `add_middleware` call site.
- Client degrade-to-`null` behaviour read directly from `getAppCheckToken()`.
- Firebase registration state read from both committed `google-services.json`
  files (parsed, `oauth_client` empty for both packages).
- Version codes cross-checked against the Play Console listing.

### What was NOT verified

- **Whether Play Integrity actually works for `com.spinr.user`.** That is
  precisely what the internal-track step exists to establish; it cannot be
  determined from the repo. If the internal install 401s on every request,
  the fix is registering the app signing SHA-1 in Firebase and enabling the
  Play Integrity link for that package — not a code change.
- No App Check token was minted or verified from this environment.

---

## Addendum 3 — `submit-play-store.yml`, a submit-only workflow

**Issue/gap identified.** No path in the repo could submit an *already-built*
AAB. `eas-native-build.yml` only submits as a side effect of building, and
`deploy-driver-play-testing.yml`'s `skip_build` mode is driver-only and pinned
to the `android-auto` profile. So a finished build whose submission failed had
no re-run route short of a full rebuild.

**Root cause.** Submission was only ever modelled as a build side effect. That
held while the sole Android submit path was the driver's Android Auto line;
it stopped holding the moment a build succeeded but its submit did not.

**Fix.** New `workflow_dispatch` workflow that submits an existing EAS build:
picks app (`rider-app` / `driver-app` / `both`), track, optional per-app build
ID (blank = `--latest`), and an optional staged-rollout fraction.

**Risk & impact.** Additive — a new file, no existing workflow changed. It can
reach the production track, so it is genuinely capable of a public release;
that is gated only by the operator's dispatch inputs, matching how
`eas-native-build.yml` already treats `auto_submit`. The track is patched into
the runner's working copy with `jq` rather than committed, so the repo keeps
one canonical default (`internal`) and the track stays a per-run choice.
`driver-app`'s `android-auto` submit profile is not read or modified —
verified explicitly in the test below.

**User experience effect.** None directly; it is a release mechanism. What it
releases is user-visible, which is why the job summary ends with the App Check
verification step rather than declaring success.

**Verification performed.** A test script exercised, against the real
`eas.json` files: YAML parse and step order; the `plan` matrix for all three
`app` values (each output parsed as JSON); the `jq` track patch for both apps
across `internal`/`production`, confirming `releaseStatus` survives; the
staged-rollout patch producing `{"track":"production","releaseStatus":
"inProgress","rollout":0.1}`; that `android-auto` is untouched; build-ID
validation accepting both real UUIDs and rejecting `not-a-uuid` and
`$(curl evil.sh)`; and rollout validation accepting 0/0.1/1/1.0 while
rejecting 1.5, `abc`, and `0.1; rm -rf /`.

**What was NOT verified.** The workflow has never run — Actions dispatch
returns 403 for this session's integration, so `eas submit` itself is
unexercised here. It also cannot appear in the Actions UI until the file is on
the default branch; `workflow_dispatch` workflows are only listed from there.
