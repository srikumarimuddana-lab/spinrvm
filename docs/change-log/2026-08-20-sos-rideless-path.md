# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | `agents/pipeline.workflow.js` Stage 8 (Change Review), reviewed/pushed by the session on behalf of vikas@ngitservices.com |
| Surface(s) | backend, rider-app, shared (library code consumed by rider-app and driver-app) |
| Domain (Sentry tag) | safety |
| PR / commit link | https://github.com/srikumarimuddana-lab/spinrvm/pull/4300 |
| Related issue or gap ID | `ACTION_ITEMS.md` B15(c) |

## 1. Issue / gap identified

A rider or driver who feels unsafe while *not* on an active ride (pre-booking, or after a ride has ended) has no in-app SOS path today — `trigger_emergency` requires a real `ride_id` and 404s without one, so the home-screen `SOSButton` falls into an `if (!rideId)` block that only tells the user to call 911 directly.

## 2. Root cause

`trigger_emergency` (`backend/routes/rides/safety.py`) was designed ride-scoped from the start: it takes `ride_id` as a required path parameter, derives the caller's role (`is_rider`/`is_driver`) from ride membership rather than from the user's own account, and links the resulting `safety_incidents` row to that ride. No urgent-alert side-effect bundle (SMS to emergency contacts, on-call paging, admin WS broadcast) previously existed for a ride-less trigger — the only ride-less path, `POST /safety/report`, is a deliberately non-urgent report endpoint with none of those side effects.

## 3. Fix / remediation

Added a new sibling endpoint, `trigger_emergency_rideless` on `POST /rides/emergency`, that duplicates `trigger_emergency`'s full side-effect bundle (`safety_incidents` insert with `ride_id=NULL`, admin WS broadcast, `notify_safety_team`, `page_sos_on_call`, confirmation push, emergency-contact SMS with corrected non-ride-specific copy) rather than modifying or refactoring the existing endpoint. Role is derived from the caller's own `is_driver` account flag instead of ride membership, since there is no ride. The endpoint is dark-launched behind a new `AppSettings.rideless_sos_enabled` flag (migration `353_rideless_sos_enabled_flag.sql`, default `false`), checked server-side first and fail-closed (404 when off) — not just gated by the client not calling it. Rider-app (via `RiderSOS`/`SOSButton`/`rideStore.ts`/`_layout.tsx`) was wired to call the new endpoint when the flag is on; driver-app and admin-dashboard were confirmed untouched. **The flag ships off in this commit and nothing in the diff turns it on anywhere.**

## 4. Risk & impact on existing functionality

- **`trigger_emergency` itself has a zero-line diff** — confirmed directly by reading the full function at QA and Security stages, not just trusting Development's claim. The existing in-ride SOS path (the one actually exercised by live users today) is unmodified. Isolated.
- **`app_settings`/`settings` table and `AppSettings` schema** — new boolean column with a safe default. Every other reader (the 18 background loops in `core/lifespan.py`, dispatch, surge engine, corporate loops) reads keys it already knows about via `.get(key, default)` and is blind to an unrelated new key. This exact addition shape has shipped before (`driver_discreet_sos_enabled`, `idle_location_v2_enabled`, others) with no regression. Isolated.
- **`safety_incidents.category`** — new free-text value `sos_button_rideless`; confirmed no CHECK/enum constraint on that column. Other writers/readers checked directly: `backend/features.py`, `utils/safety_checkin_loop.py`, `utils/safety_paging.py`, `routes/admin/settings.py`, `routes/admin/safety.py`, `routes/safety.py`, the admin Safety-queue UI, and the admin manual-incident form — none branch on the set of valid category values, and the admin UI already renders a `—` placeholder for a falsy `ride_id` (proven by the pre-existing `/safety/report` path already producing `ride_id=NULL` rows). Isolated.
- **Migration-315 `sos_idempotency_key` unique index** — confirmed to have no `ride_id` component (`(reported_by_user_id, sos_idempotency_key) WHERE sos_idempotency_key IS NOT NULL`), so it is reused unmodified with no NULL-uniqueness gotcha. Isolated.
- **`shared/api/client.ts`'s `isSosUrl` regex — the single genuinely shared/security-relevant touch in this diff.** Widened from `/^\/rides\/[^/]+\/emergency$/` to `/^\/rides\/(?:[^/]+\/)?emergency$/` to keep the new bare path exempt from the 401-refresh interceptor (required — SOS must never be gated behind a token refresh). Genuinely shared code, but its blast radius was checked directly: `isSosUrl` is a private, non-exported `const` used only inside this one file's `handleApiError`; its current beneficiaries (rider-app's `RiderSOS`/`rideStore.triggerEmergency`, driver-app's `useDriverSafetyTrigger.ts`) are unaffected — still matches. No other route in the codebase is shaped `/rides/.../emergency` or `/rides/emergency`, so the exemption set grows by exactly the one new real endpoint.
- **`shared/components/SOSButton.tsx`** — two new optional props (`ridelessSosEnabled`, `onTriggerRideless`), additive. Every current mount site was enumerated: driver-app's direct mount and three of rider-app's four `RiderSOS`-mounting screens pass neither prop and are confirmed byte-identical.
- **Blast radius, stated explicitly: cross-surface but bounded.** Backend (new route + flag plumbing) + shared library (`isSosUrl`, `SOSButton`) + rider-app (`RiderSOS`, `_layout.tsx`, `(tabs)/index.tsx`, `rideStore.ts`). Driver-app and admin-dashboard were grepped independently in three separate stages for every new symbol name — zero hits each time.
- **No interaction with money/wallet deltas, Stripe, or the ride state machine, or insurance-period classification** — confirmed by grep and by design (no `ride_id`, no ride-state transition).
- **Real risk worth naming: fraud/abuse-surface shift.** Triggering the full urgent-alert side-effect bundle previously required an active ride; the new endpoint only requires being logged in, bounded by the same per-user 20/minute rate limit `trigger_emergency` already uses. Inherent to the feature's purpose and reviewed/accepted by Security — but a genuine widening of who can cause the side-effect bundle to fire, which is exactly why the flag must stay off until Trust & Safety has assessed triage readiness (see below).
- **No dedicated per-endpoint rate-limit test** for `POST /rides/emergency` — a pre-existing gap shared with the sibling `trigger_emergency` test file, not new to this change but not closed by it either.

## 5. User-experience effect

**Nobody sees a difference today.** The flag defaults to `false` in the migration, schema, admin PATCH model, public `/settings` response, and rider-app Context's initial state — every read resolves to false, and no line in the diff sets it to true. If/when a human later enables it: a rider on the home screen with no active ride gains a working SOS button in place of today's "Call 911 directly" block — additive to the home screen only, not a modification of any in-progress-ride SOS behavior, and not visible mid-session to anyone already using an existing SOS surface. **Copy status:** the SMS body sent to emergency contacts is a labeled DRAFT, corrected from the in-ride endpoint's false-for-this-case wording, but **not yet reviewed or approved by Product + Trust & Safety** — a required sign-off before the flag may be enabled anywhere. Driver-app and admin-dashboard: no UX change, confirmed no code touched.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/353_rideless_sos_enabled_flag.sql` | New migration: `ADD COLUMN rideless_sos_enabled BOOLEAN NOT NULL DEFAULT false` on `settings` | New dark-launch flag, additive column (renumbered from 350 → 353 to resolve a cross-PR numbering collision found by CI after other work landed on `main` while this branch was in flight) |
| `backend/schemas.py` | Added `rideless_sos_enabled: bool = False` to `AppSettings` | Schema parity with the new column |
| `backend/routes/admin/settings.py` | Added `rideless_sos_enabled: Optional[bool] = None` to the admin PATCH model | Lets the flag be toggled without redeploy |
| `backend/routes/settings.py` | `get_public_settings()` now returns the new flag | Client needs to read the flag value |
| `backend/routes/rides/safety.py` | New `RidelessEmergencyRequest` model + `trigger_emergency_rideless` on `POST /emergency` | The new endpoint itself; `trigger_emergency` unchanged |
| `backend/routes/rides/__init__.py` | Added two new names to the existing import tuple | Route mounting |
| `backend/tests/test_sos_rideless.py` | New, 9 cases | Coverage for the new endpoint |
| `shared/api/client.ts` | Widened `isSosUrl` regex to also match the bare `/rides/emergency` path | Keeps new endpoint exempt from 401-refresh interceptor |
| `shared/components/SOSButton.tsx` | Added two optional props, extended retry-ladder branch | Lets the button call the new path when enabled |
| `rider-app/components/RiderSOS.tsx` | Forwards the two new optional props | Real integration point (home screen mounts this, not `SOSButton` directly) |
| `rider-app/app/_layout.tsx` | New `RidelessSosEnabledContext`, extended existing `/settings` fetch | Threads the flag value down to the home screen |
| `rider-app/app/(tabs)/index.tsx` | Consumes the new context, passes new props into `<RiderSOS>` | Only site that gets the new capability |
| `rider-app/store/rideStore.ts` | New `triggerRidelessEmergency` action | Client call to the new endpoint |
| `rider-app/__tests__/SOSButton.test.tsx` | Extended with 4 new cases | Coverage for the new button branch |
| `rider-app/store/__tests__/rideStore.sos.test.ts` | Extended with 4 new cases | Coverage for the new store action |
| `agents/runs/sos-rideless-path/*.md` | Pipeline paper trail | Required by `agents/PIPELINE_DESIGN.md` |

## 7. Before / after

```
# Before (shared/components/SOSButton.tsx, simplified)
if (!rideId) {
  showAlert("Emergency alert requires an active ride. Call 911 directly.");
  return;
}
// ...existing retry ladder calls onTrigger(rideId)
```

```
# After
const canTriggerRideless = !rideId && ridelessSosEnabled && !!onTriggerRideless;
if (!rideId && !canTriggerRideless) {
  showAlert("Emergency alert requires an active ride. Call 911 directly.");
  return;
}
// ...same retry ladder now calls onTriggerRideless() when canTriggerRideless,
// otherwise onTrigger(rideId) exactly as before
```

```
# Before (shared/api/client.ts)
const isSosUrl = (url: string) => /^\/rides\/[^/]+\/emergency$/.test(url);
```

```
# After
const isSosUrl = (url: string) => /^\/rides\/(?:[^/]+\/)?emergency$/.test(url);
```

## 8. Rollback plan

- **Primary path — flag is already off, no action needed.** `rideless_sos_enabled` defaults to `false` everywhere it's read, and nothing in this commit sets it to `true`. If it is later turned on and needs to come back off: `PATCH /api/admin/settings {"rideless_sos_enabled": false}` — no redeploy, matches this codebase's existing `app_settings`-in-DB dark-launch pattern. The endpoint re-checks the flag server-side first and 404s immediately, so flipping it off stops all side effects on the very next request.
- **If the migration itself needs to be undone:** `ALTER TABLE public.settings DROP COLUMN rideless_sos_enabled` — safe to drop, no other code path reads it, and while the flag stays off no production data depends on the column existing.
- **If the whole feature needs to be pulled:** `git revert` of the Development-stage commit is sufficient for the code — nothing in this diff writes to Stripe or a wallet balance. The one caveat: if the flag were ever turned on and real `safety_incidents` rows were inserted before a revert, those rows are safety records and must **not** be deleted (append-only, matches the regulatory retention rule) — this does not apply today since the flag has never been on.

## 9. Verification performed

- [x] Automated tests run — unit only. 103 backend tests (`test_sos_rideless.py` + 5 neighboring SOS/settings files) independently re-run at QA and Security stages; 22 rider-app Jest tests re-run at QA. No integration tests (real Supabase) and no e2e run.
- [ ] Manual repro steps followed in staging — **not done.** No staging deploy exists for this change.
- [x] Blast-radius grep performed — see section 4 above (`app_settings` readers, `safety_incidents.category` readers/writers, `isSosUrl` callers, `SOSButton` mount sites, driver-app/admin-dashboard symbol search).
- [x] Reviewed against relevant CLAUDE.md conventions — state machine (n/a, confirmed by grep), money (n/a, confirmed by grep), JWT trust model (verified: role derived from DB-backed `current_user["is_driver"]`, never a JWT claim), PIPEDA/PII (every `logger.*` call site and the WS broadcast payload reviewed; no raw PII). **Not explicitly reviewed:** RLS policy behavior for the new/adjacent tables, and whether any Sentry alert rule or Prometheus dashboard needs a new rule for the `sos_button_rideless` category.
- [x] Feature-flagged, user-visible and non-trivial — yes, `rideless_sos_enabled`, default off, fail-closed 404 server-side as defense in depth beyond the client gate.

### What was NOT verified

- No production build (`npm run build`/EAS equivalent) was run for rider-app or driver-app.
- No manual/simulator/device QA of the actual SOS button in either flag state — a real gap for a customer-facing safety control on a screen with no existing automated visual-regression tooling.
- No live Supabase — all tests mock `db_supabase`/`get_app_settings`/`send_sms`/`manager`. Migration 353 was never applied to any real database (no `DATABASE_URL` available in this environment).
- No live Twilio/FCM — SMS/push sends are mocked in every test; the DRAFT copy strings were reasoned about against `domain-safety.md`'s rules, not received on a real device.
- No dedicated rate-limit test against the new route specifically.
- **Whether the DRAFT SMS/push copy would pass real Product + Trust & Safety review, and whether the triage-runbook readiness question is resolved, are both open human questions no pipeline stage had standing to answer.**

## 10. Sign-off

- [x] Rollback plan is concrete and testable — flag flip (already off by default) plus a plain reversible `DROP COLUMN` migration rollback; no Stripe/wallet data touched.
- [x] Blast radius is stated, not assumed — see section 4; the one genuinely shared-code touch (`isSosUrl`) got explicit treatment across three independent pipeline stages.
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — section 5 states plainly that nobody sees any difference today and describes exactly what would change if a human later enables it.

---

**This entry documents pipeline-authored work awaiting human sign-off — see the PR description for the two specific open questions (SMS/push copy approval, triage-runbook readiness) that gate ever enabling `rideless_sos_enabled`.**
