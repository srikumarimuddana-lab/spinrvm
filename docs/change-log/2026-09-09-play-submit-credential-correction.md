# Change Impact & Risk — restore the Play service-account key path (corrects same-day change)

**Date:** 2026-09-09 · **Surfaces:** `rider-app`, `driver-app`, CI
**Corrects:** `docs/change-log/2026-09-09-play-store-production-release.md`

## Issue/gap identified

The first real run of `submit-play-store.yml` (run `34350746122`) failed for
both apps at the submit step:

```
Looking up credentials configuration for com.spinr.driver...
Google Service Account Keys cannot be set up in --non-interactive mode.
```

## Root cause

Two mistakes earlier the same day, both traceable to one bad inference.

The Expo MCP connector rejected every submission with *"contains a conflict
between exclusive peers [googleServiceAccountKeyId, googleServiceAccountKeyJson]"*.
That was read as proof the EAS project holds a stored Google Service Account
key, so `serviceAccountKeyPath` was removed from both `production` submit
profiles as a redundant second source.

**That inference was wrong.** The EAS project holds no stored key; the
connector contributes the duplicate field itself. Removing the path therefore
deleted the only working credential source. Consequently `submit-play-store.yml`
was authored with no decode step at all, and `eas-native-build.yml`'s decode
step was softened to skip when the secret is unset.

**What actually proves it:** `deploy-driver-play-testing.yml` has **7
successful runs** (150, 144, 141, 113, 70, 61, 28), every one decoding
`PLAY_SERVICE_ACCOUNT_JSON` into `play-service-account.json` with
`serviceAccountKeyPath` set. The secret exists and works; the file is the
credential source.

## Fix/remediation

- Restored `serviceAccountKeyPath` on both apps' `production` submit profiles.
- Added the decode + validate + `if: always()` cleanup steps to
  `submit-play-store.yml`, ordered before `eas submit`.
- Reverted `eas-native-build.yml`'s decode step to hard-fail on a missing
  secret, and corrected both files' prerequisite comments.

## Risk & impact on existing functionality

Restores the configuration that was already proven in production, so the blast
radius is a return to a known-good state rather than a new one. Every Android
submit profile in the repo now points at the same key path — verified by
enumerating all three (`rider-app/production`, `driver-app/production`,
`driver-app/android-auto`). `deploy-driver-play-testing.yml` is untouched.

The one new failure mode: a run with `PLAY_SERVICE_ACCOUNT_JSON` unset now
fails fast with an actionable message instead of proceeding. That is intended —
it cannot succeed without the key.

## User experience effect

None. No app code changed; this is release plumbing.

## Files modified

| File | What changed | Why |
|---|---|---|
| `rider-app/eas.json` | Re-added `serviceAccountKeyPath` | Only working credential source |
| `driver-app/eas.json` | Same | Same |
| `.github/workflows/submit-play-store.yml` | Added decode + cleanup steps; corrected prerequisites | Step was missing entirely — the run's actual cause |
| `.github/workflows/eas-native-build.yml` | Reverted optional-secret escape hatch to hard-fail | Built on the same wrong inference |

## Before/after

```jsonc
// before (broken — no credential source anywhere)
"android": { "track": "internal", "releaseStatus": "completed" }
// after
"android": { "serviceAccountKeyPath": "./play-service-account.json",
             "track": "internal", "releaseStatus": "completed" }
```

## Rollback plan

Revert this commit; it restores the state that produced the failing run, so
rollback is only appropriate if a *different* credential source is configured
in EAS first. Nothing here is applied to live data — no store release resulted
from the failed run, so there is nothing outward-facing to undo.

## Verification performed

A script asserted, against the real files: all three workflows parse under
`yaml.safe_load`; all three Android submit profiles carry
`serviceAccountKeyPath`; `submit-play-store.yml`'s step order is
decode(4) < submit(7) < cleanup(8) with cleanup at `if: always()`; and the new
decode step's `env` and `run` blocks are **byte-identical** to the proven
`deploy-driver-play-testing.yml` step, hard-failing on a missing secret and
validating the decoded JSON. `eas-native-build.yml` no longer contains the
`exit 0` escape hatch.

## What was NOT verified

The corrected workflow has not been run — Actions dispatch is 403 for this
session, so a human must trigger it. The failure mode it fixes is understood
from the run's own log, but the fix's success is unproven until a real run
reaches `eas submit` and the store accepts the upload. Whether Play Integrity
works for `com.spinr.user` remains separately unverified.
