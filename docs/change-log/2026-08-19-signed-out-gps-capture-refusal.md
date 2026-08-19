# Driver app: refuse all GPS capture (re)starts after sign-out

**Date:** 2026-08-19
**Surface:** driver-app location capture (privacy/PIPEDA, live-tested), 1 commit (`0512701`)
**Trigger:** Owner report from live app testing — the driver app was still collecting location after the driver signed out.

## Issue/gap identified
Capture could survive or be **resurrected** after sign-out. The teardown chain (`sessionTeardown.ts`), the background handler's session gate, the geofence exit handler, and the Android Auto start path were all gated on the positive sign-out marker — but the two capture **start** paths were not: (1) `recoverTripLocation()`, which is reachable **headless** via the FCM `location_health` recovery nudge — the server's gap monitor doesn't know a driver signed out, so an abandoned in-progress ride keeps nudging, and each push restarted GPS capture on the signed-out device; (2) `startBackgroundLocation()` itself, whose "foreground auth store knows best" rationale stopped holding the moment a headless caller (the nudge) could reach it. Collecting location from a signed-out user is collection outside a consented session — PIPEDA data-minimization territory.

## Root cause
The sign-out marker discipline was applied to running-handler and re-arm paths when it was introduced, but the recovery-nudge start path was added later (Phase 1 of the tracking overhaul) without the gate, and the deliberate exemption on `startBackgroundLocation` was never revisited.

## Fix/remediation
Both start paths now check `isSessionEnded()` first — **before** the permission flow, so a headless resurrection can never pop a system permission dialog on a signed-out device — refuse the start, reap any orphaned running task instead of adopting it, and report a non-fatal (`bg_start_refused_signed_out` / `recover_refused_signed_out`) so resurrection attempts are visible in the field. The stale doc rationale on `isSessionEnded` was rewritten.

## Risk & impact on existing functionality
- **Legitimate go-online cannot be blocked**: `isSessionEnded()` fails **open** on SecureStore errors (only a positively recorded sign-out refuses), and `setTokens()` deletes the marker on sign-in before any go-online can run. Pinned by the existing "unreadable store = live session" test.
- **Callers of the gated functions** (full blast radius): go-online (`useDriverDashboard.toggleOnline`), geofence exit handler, FCM `location_health` handler, dashboard WS `location_health` handler, ride-flow cadence changes. All signed-in paths behave identically (53/53 suite green, incl. the pre-existing recover/nudge cases).
- **Recovery nudge remains fully functional for signed-in drivers** — the SPR-PE7TTB self-heal story is unchanged.

## User experience effect
None for signed-in drivers. A signed-out device stops acquiring GPS entirely (no notification, no location indicator, no dialog). Not visible mid-session — the change only manifests after sign-out.

## Before/after
```
before: sign-out → teardown stops capture → server nudge (FCM) arrives →
        recoverTripLocation() → startBackgroundLocation() → CAPTURE RESUMES
after:  sign-out → teardown stops capture → server nudge arrives →
        marker check refuses, reaps any orphan, reports non-fatal → silent
```

## Rollback plan
`git revert 0512701` — pure client-side guard, no data or schema involvement.

## Verification performed
- 3 new regression tests (refusal via nudge, orphan reaping, no permission dialog) + full driver-app jest suite: **588/588**, `tsc --noEmit` clean.
- **No production mobile build was run** (EAS unavailable in this environment) — jest + tsc only.

## What was NOT verified
- On-device end-to-end (sign out while docked to Android Auto, then send a nudge). **Important:** testers' installed builds predate this fix (and possibly the earlier session gates entirely) — the observed behavior can only be confirmed fixed after a new mobile build (`[build]` commit tag / EAS) reaches the test devices. Until then, the observation from the current installed build is expected to persist.
- Whether the owner's observation was device capture vs. the admin dashboard showing a stale "online" driver row (server-side `is_online` decays via presence TTL + the stale-intent reconciler, a separate bounded mechanism). If the report was dashboard-based, that path deserves its own check.
