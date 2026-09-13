# Last two test rides: validated findings and implementation plan

**Date:** 2026-09-13  
**Scope:** completed test rides `SPR-5BNURH` and `SPR-BCPJPV`  
**Branch:** `codex/test-ride-recovery-audit` (based on `origin/main` at `6c21b7929`)  
**Status:** investigation in progress; two auth fixes committed, insurance fix tested locally, no production writes or deployment performed

## Executive finding

The first ride completed normally. The second completed after the iOS driver process restarted mid-ride and produced only five stored location rows (four counted as in-progress points), leaving two route-gap events unresolved at completion. The immediate operational effects were loss of continuous GPS tracking, a new driver login, estimated-distance settlement, and an incorrectly attributed insurance Period 2 interval.

The process-death root cause is still unknown. The class of failure predates the September 13 map commits, so the evidence does not support reverting those commits. Source review also corrects the original assertion that iOS had no native crash capture: the driver app configures the Sentry React Native native integration and build plugin, and SDK 8.25.0 enables iOS watchdog termination and app-hang tracking by default. The remaining gap is to prove that the installed production build had a valid DSN, debug symbols, and event delivery, then obtain a native event from a reproduced failure.

## Ride comparison

| Evidence | `SPR-5BNURH` | `SPR-BCPJPV` | Assessment |
|---|---:|---:|---|
| Status | completed | completed | Both state transitions completed |
| Planned distance | 2.06 km | 2.37 km | Booking estimates |
| Stored actual distance | 2.796 km | 2.37 km | Second exactly equals its plan |
| Stored GPS rows | 189 | 5 | Second ride lost almost all tracking |
| In-progress points used by distance quality | healthy trace | 4 | Below the five-point minimum |
| Route-gap events | 2 resolved | 1 resolved, 2 unresolved at completion | Backend detected the degraded second route |
| Route confidence | healthy measured route | low | Second ride is explicitly marked degraded |

Read-only production queries were used to confirm these values. IDs are retained only where needed for engineering correlation; no names, phone numbers, addresses, or raw coordinates are included here.

## Findings

### F1 — iOS process death during the second ride

**Status: confirmed symptom; root cause unknown.** Sentry cold-start markers and the location gap establish a process restart around 13:50 Regina. Thermal state was `serious` in one marker, but one observation does not establish thermal pressure as the cause. Memory pressure was not indicated.

The failure existed by September 11, before the September 13 route-subtree changes. No driver-app call to `reloadAsync`, `fetchUpdateAsync`, or `checkForUpdateAsync` was found, so an Expo update would apply at a subsequent natural launch rather than programmatically restarting the running ride.

Correction to the first report: native iOS capture is present in `shared/services/errorReporting.ts`, `driver-app/app.config.ts`, and `@sentry/react-native` 8.25.0. Zero unhandled events proves that the incident was not captured; it does not prove the capture integration is absent. Build-time DSN, symbol upload, and physical-device delivery still need validation.

### F2 — session was not restored after process death

**Status: incident mechanism inferred; two concrete code defects reproduced and fixed.** At 13:53:10 a new login was issued while the prior iOS refresh token remained valid and unrevoked. This is consistent with the client not presenting the prior token, but database history alone cannot prove whether the token was absent, unreadable, never durably written, or unavailable in the installed OTA.

Code tests reproduced two independent ways the shared auth store could create that outcome:

1. A SecureStore read exception was converted to `null`, the same value as a missing key. Cold-start initialization then called `clearAuthStorage()` and deleted valid credentials. Commit `b4f279e13` distinguishes unavailable storage from confirmed absence, preserves the credential, and enters the driver's existing recovery flow.
2. A SecureStore write exception was swallowed after the access token had already been published in memory. Login appeared successful but no durable refresh token existed for the next process. Commit `eaa90b29c` makes the write fail visibly and publishes the session only after persistence succeeds.

Targeted driver tests passed after these changes: 48 tests across initialization, refresh races, recovery routing, and session teardown. Security review found no auth bypass. One follow-up remains: settle `isInitialized`/`isLoading` if a revoked-token logout also fails to persist the session-ended marker, so cold start cannot remain on the splash indefinitely.

Blast radius is both mobile apps because `shared/store/authStore.ts` is shared. The driver already has a recovery screen and retry loop. The rider app does not yet expose the same recovery UI, although preserving the credential allows a later retry/relaunch.

### F3 — estimated distance used when GPS was sparse

**Status: confirmed by source; settlement policy decision required.** `backend/utils/trip_distance.py` intentionally uses `planned_distance` when fewer than five `trip_in_progress` points exist or all segments are rejected. `backend/routes/drivers/ride_complete.py` stores that result and may use it to recalculate the fare when fare lock is disabled. This explains the exact `2.37 km == 2.37 km` values without inference.

The backend already recorded low route confidence and unresolved gaps, but completion and payment continued. This behavior avoids charging from an obviously incomplete GPS trace, yet it can overcharge or undercharge because the estimate is not a measured trip. No money behavior should change until product chooses among: keep and flag, defer settlement for review, or reconcile later from another trusted source. The decision must also cover incentives, receipts, corporate rides, Stripe idempotency, and the sub-one-second settlement target.

### F4 — Period 2 attributed to the cancelled ride

**Status: root cause confirmed; fix prepared and locally tested.** The open Period 2 interval remained attached to cancelled ride `ac38399b…`. When the next ride was assigned, migration 253's RPC compared only `period = 2` and returned `noop`; it did not compare `ride_id`. The next ride therefore had no Period 2 interval before Period 3 began.

Migration 419 changes idempotency to compare both the period and the NULL-safe ride identity. A transition from Period 2/ride A to Period 2/ride B closes A's open interval and appends B's interval, preserving completed audit rows. The function signature, unique-index serialization, and service-role-only execution remain unchanged.

A disposable PostgreSQL 17 test failed against migration 253 with `new ride must open its own Period 2`, then passed against migration 419. The migration and test are not yet committed or applied to production. Historical correction for the affected ride is intentionally excluded from the schema fix and requires a separately reviewed append-only correction record.

### F5 — near-2.0 accuracy values on the degraded ride

**Status: no server interpolation found.** `driver-app/utils/tripLocationRecorder.ts` copies `location.coords.accuracy`, and `backend/utils/breadcrumbs.py` persists the submitted accuracy and source without computing a replacement. The unusual floating-point values therefore originated in the device/native location result or client payload, not a server route backfill.

Those values alone do not establish spoofing or synthetic GPS. Retain them as diagnostic evidence and correlate them with recording-session ID, point source, timestamps, OS location mode, and the installed build before proposing a code change.

### F6 — driver login received refresh tokens marked `audience = rider`

**Status: confirmed taxonomy/policy gap; exploitability not established.** The generic `/auth/verify-otp` path used by the driver app issues `audience="rider"`, while the dedicated Firebase driver path issues `audience="driver"`. Refresh accepts either audience. This makes the database label unreliable for identifying the client surface and weakens intended audience separation.

Do not change the generic endpoint based on user role alone: Spinr users can be both riders and drivers, and the backend trust model re-reads roles. The follow-up design should bind an authenticated client/surface claim to issuance and enforcement, preserve existing sessions during a transition window, and add allowed/denied auth tests before changing production behavior.

## Implementation plan

- [x] Preserve valid mobile credentials when secure storage reads are temporarily unavailable; test iOS, Android, retry, confirmed absence, rejected token, and refresh races. Commit: `b4f279e13`.
- [x] Require durable refresh-token persistence before publishing an authenticated session; keep local logout teardown running when marker/cache cleanup fails. Commit: `eaa90b29c`.
- [ ] Add the revoked-token plus failed-marker-write regression and ensure auth initialization always settles its loading flags. Commit as an auth-only logical change with an updated impact log.
- [ ] Finish migration 419 review, add its Change Impact & Risk entry, run the disposable PostgreSQL contract test and focused backend insurance tests, then commit the three-file insurance change.
- [ ] Validate iOS crash capture in a real production-profile build: DSN presence, native integration, debug-symbol upload, release/dist mapping, watchdog/app-hang settings, and a controlled non-production native crash. Record results; do not intentionally crash a live driver's production session.
- [ ] Resolve the installed Expo update ID to a source commit and repeat a monitored ride while collecting device diagnostics. Use the resulting native event to decide whether map rendering, thermal pressure, location services, or OS termination is causal.
- [ ] Obtain a product decision for GPS-starved settlement. If behavior changes, implement it behind the existing settings model with Decimal-only calculations, mock-Supabase dry runs, Stripe idempotency checks, receipt/incentive coverage, and a concrete before/after fare scenario.
- [ ] Design client-bound token audience issuance and enforcement with a compatibility window; add allowed and denied rider/driver tests before rollout.
- [ ] Decide whether to append an approved historical insurance correction for `SPR-BCPJPV`. Never mutate or delete the original period row.
- [ ] Run final focused tests, mobile production JS export/build checks, lint/type checks, migration review, money and security audits; update all Change Impact logs with exact results and known limits.
- [ ] Push `codex/test-ride-recovery-audit` and open a PR against `main`. The PR must remain unmerged until required checks and the live-testing release gates pass.

## Current artifacts

| Artifact | State |
|---|---|
| `.claude/plans/2026-09-13-test-ride-recovery.md` | Working checklist |
| `docs/change-log/2026-09-13-auth-storage-read-recovery.md` | Committed impact log |
| `docs/change-log/2026-09-13-auth-storage-write-failure.md` | Committed impact log |
| `backend/migrations/419_insurance_period_ride_identity.sql` | Prepared; local contract passed; review/commit pending |
| `backend/tests/sql/insurance_period_ride_identity.sql` | Prepared; demonstrates red against 253 and green against 419 |
| `docs/audit/2026-09-13-driver-app-mid-ride-process-death.md` | Original detailed investigation supplied as source; unchanged |

## Verification boundary

No production migration, deployment, TestFlight build, controlled native crash, live payment, or historical correction has been performed. The production database access in this investigation was read-only. Backend focused tests and the interrupted mobile export still need a clean final run before the PR is opened.
