# Change Impact & Risk Log — SOS / share-trip safety fixes

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-16 |
| Author | Claude Code (session: SOS options analysis) |
| Surface(s) | backend, rider-app, driver-app, shared |
| Domain (Sentry tag) | `safety` |
| PR / commit link | branch `claude/sos-options-analysis-t060ph` — commits `cf96b2a`, `6e5db54`, `f27aaa9`, `7059eeb`, `8baddc3`, `772b45f` |
| Related issue or gap ID | Findings S1, S2, S4, S7, F1, F3, F4 in `docs/proposals/2026-08-16-safety-toolkit-gap-analysis.md`; relates to ACTION_ITEMS B15(c), B16 |

## 1. Issue / gap identified

A review of the SOS surface (prompted by a comparison against Uber's Safety Toolkit) found seven
defects in already-shipped safety code. Four affect a control live to every rider today:

- **S1** — the SOS success dialog claimed emergency contacts were notified on any HTTP 200, including
  when every SMS failed and when the rider had **no contacts saved**.
- **S2** — `SOSButton` (3 attempts) wrapped `rideStore.triggerEmergency` (3 attempts), so one press
  could issue **up to 9 POSTs**, each inserting a `safety_incidents` row and re-sending "URGENT" SMS.
- **S4** — the rider home screen carried an unreachable `Linking.openURL('tel:911')` branch.
- **S7** — the SOS label rendered only at `size="large"`; every map placement uses `small`, so the
  most visible instance was an unlabelled shield glyph.
- **F1** — the driver Safety overlay's "Share Live Trip Link" fetched the URL and discarded it.
- **F3** — `GET /rides/{id}/share` minted tokens without `shared_trip_token_created_at`, and
  `track_shared_ride` only expires when that field is set → **links from the primary share path
  never expired**.
- **F4** — `domain-safety.md` documented four emergency-contact behaviours and three night-ride
  protections that do not exist in code.

## 2. Root cause

- **S1/S2**: both layers were typed `Promise<void>`. The response — which has always carried
  `contacts[]`, `contacts_notified` and `notification_warning` — was discarded at each layer, and
  each layer independently owned a retry ladder because neither knew the other did.
  Not a new regression: `driver-app/hooks/useDriverSafetyTrigger.ts` already carried a comment
  warning about the 9-POST multiplication. B16 routed *around* the bug rather than fixing it.
- **S4**: defensive coding against a case `SOSButton` already handles internally.
- **F1**: an `await` whose result was never bound.
- **F3**: two writers for one field; only one of them stamped the companion timestamp, and the
  reader treated "absent" as "no policy" rather than "unknown".
- **F4**: doc written to intent, never reconciled with implementation.

## 3. Fix / remediation

New `shared/types/safety.ts` pins the SOS response contract and exposes `deriveContactOutcome()`,
which maps a response to one of four states and **fails toward the weaker claim** — a void result or
a deduplicated replay yields `unknown`, never "notified". The success copy branches on it across all
four locale files. The retry ladder now lives only in `SOSButton`. Migration 315 adds a nullable
`sos_idempotency_key` plus a partial UNIQUE index, checked before the insert and before any
notification fires, so a replay produces zero side effects. The dead 911 branch is deleted, the map
button shows its wordmark when idle, the driver share button opens the real OS share sheet, and the
GET share path stamps (and backfills) the token timestamp.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface (backend + both apps + shared).** Enumerated by grep, not assumed:

| Shared thing changed | Every other consumer found | Assessment |
|---|---|---|
| `SOSButton.tsx` | rider: `(tabs)/index.tsx`, `ride-in-progress.tsx` (×2), `driver-arriving.tsx`, `driver-arrived.tsx`; driver: `driver/(tabs)/index.tsx`, `_layout.tsx` | `onTrigger` widened to `Promise<SOSTriggerResult \| void>` — a **widening**, so every existing caller still typechecks. Driver callers return void → they get `unknown` copy, which is correct (they cannot report contact status). |
| `rideStore.triggerEmergency` | the five rider screens above | Return type changed `void` → `SOSTriggerResult`; no caller destructured the old void, verified by tsc across both apps. |
| `POST /rides/{id}/emergency` | `rideStore.triggerEmergency`, `useDriverSafetyTrigger`, driver `(tabs)/index.tsx` legacy onTrigger | New request field is **optional**; a client that omits it gets exactly the previous behaviour. |
| `safety_incidents` table | `routes/safety.py`, `routes/admin/safety.py`, `utils/safety_checkin_loop.py`, `services/zoho_desk_integration.py` | Column is additive + nullable; no other writer sets it, no reader selects `*` into a fixed schema. Admin queue unaffected. |
| `GET /rides/{id}/share` | rider `ride-in-progress.tsx` (×2), `ai-assistant.tsx`, `ride-tracking-webview.tsx`; shared `SafetyOverlay.tsx` | Response shape unchanged; only an extra DB write on mint/backfill. |
| `track_shared_ride` | public tracking page, `admin-dashboard/src/app/track/[rideId]` | Behaviour change on **one** path only: legacy NULL-timestamp token on a *terminal* ride now 404s. |

**Background loops / state machine / money:** none touched. No ride-state transition, no wallet or
Stripe path, no dispatch code. `safety_checkin_loop` writes `safety_incidents` but never sets the
new column, so it is unaffected.

**What could regress:**
- A rider mid-trip holding a share link created before this deploy keeps working while the ride is
  live, and expires once the ride ends. This is the intended, deliberately conservative direction.
- If `contacts_notified` were ever wrong server-side, riders would now see it. That is the point,
  but it does mean a latent backend bug becomes user-visible rather than silently masked.

## 5. User-experience effect

**Rider — visible, and visible mid-session** (someone already on a ride who presses SOS):
- Success dialog now tells the truth. A rider whose contacts could not be reached is told to call
  them directly instead of being falsely reassured. A rider with no contacts saved is told so.
- The map SOS control reads "SOS" instead of an unlabelled shield.

**Driver — no change on the default path.** All driver-facing changes (`SafetyOverlay`,
`SafetyShield`) sit behind `app_settings.driver_discreet_sos_enabled`, still `False`. Nobody has
seen the dead share button in production, and it must not be enabled until this ships.

**Copy review:** the three new strings are specific, non-technical and actionable ("We could NOT
reach your emergency contacts — please call them directly"). They never claim to replace 911, per
`domain-safety.md`. Translated in `en-CA`/`fr-CA` (rider) and `en`/`fr` (driver).

**Not feature-flagged, deliberately.** Gate 3 asks for a flag on user-visible non-trivial change.
Applied here that would mean keeping a false safety claim live behind a flag. The change is strictly
corrective — it narrows a claim to what the response supports, adds no new UX surface, and cannot
reject previously-valid input. Flag-gating a truthfulness fix was judged the worse option; recorded
here rather than left silent.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/types/safety.ts` | **new** — response contract + `deriveContactOutcome()` | Single source of truth for what the SOS response supports |
| `shared/components/SOSButton.tsx` | Response consumed, outcome-branched copy, per-press idempotency key, label at small size | S1, S2, S7 |
| `shared/components/SafetyOverlay.tsx` | Share sheet + loading/failed states, timer cleanup | F1 |
| `rider-app/store/rideStore.ts` | Inner retry ladder removed; returns response; forwards key | S2 |
| `rider-app/app/(tabs)/index.tsx` | Dead auto-dial branch removed | S4 |
| `driver-app/hooks/useDriverSafetyTrigger.ts` | Sends its own per-trigger key | S2 |
| `backend/migrations/315_safety_incidents_idempotency_key.sql` | **new** — nullable column + partial UNIQUE index | S2 |
| `backend/routes/rides/safety.py` | Optional `idempotency_key`; dedup before insert and before any notification | S2 |
| `backend/routes/rides/sharing.py` | GET stamps + backfills timestamp; NULL treated as expired once terminal | F3 |
| `rider-app/i18n/{en-CA,fr-CA}.json`, `driver-app/i18n/{en,fr}.json` | 3 new keys each, purely additive | S1 |
| `.claude/context/domain-safety.md` | Seven false claims corrected | F4 |
| tests (7 files) | New + updated coverage | — |

## 7. Before / after

```python
# Before — routes/rides/safety.py: every retry inserted and re-alerted
incident = {...}
await _deps.db_supabase.insert_one("safety_incidents", incident)
# ... admin WS, safety email, on-call page, SMS to every contact
```

```python
# After — a replay returns the original and fires nothing
if body.idempotency_key:
    _prior = await _deps.db_supabase.get_rows(
        "safety_incidents",
        {"reported_by_user_id": current_user["id"],
         "sos_idempotency_key": body.idempotency_key}, limit=1)
    if _prior:
        return {"success": True, "incident_id": _prior[0].get("id"), "duplicate": True, ...}
```

```ts
// Before — shared/components/SOSButton.tsx: claimed contacts were notified, always
await onTrigger(rideId, lat, lng);
backendOk = true;
// ...
Alert.alert(t('sos.alert_title'), t('sos.alert_msg'));  // "...and your emergency contacts"
```

```ts
// After — the claim is derived from what actually happened
const result = await onTrigger(rideId, lat, lng, idempotencyKey);
contactOutcome = deriveContactOutcome(result ?? null);
// ...
showSuccessAlert(contactOutcome);  // one of 4 messages
```

## 8. Rollback plan

| Change | Rollback without a second deploy |
|---|---|
| Driver overlay changes (F1) | **Flag: `app_settings.driver_discreet_sos_enabled` → `False`.** Already off; nothing to do unless it was enabled. |
| Migration 315 | Rollback SQL is in the migration header: `DROP INDEX idx_safety_incidents_sos_idem; ALTER TABLE safety_incidents DROP COLUMN sos_idempotency_key;`. Safe at any time — nullable, nothing joins on it, endpoint treats a missing key as pre-migration behaviour. |
| Backend dedup + share expiry | `git revert` is genuinely sufficient: no money, no ride state, no wallet delta, and nothing is written that a revert would strand. The only data written is `sos_idempotency_key` (ignored once the column is dropped) and `shared_trip_token_created_at` (a real timestamp that stays correct either way). |
| Rider copy / label | Code revert + app release. **This is the weak point**: it is a mobile bundle, so it cannot be rolled back without an OTA/store release. Accepted because the change is corrective and its failure mode is cosmetic — the worst case is the copy reading oddly, not a safety action failing. |

**Data-level note:** the F3 backfill writes a real `shared_trip_token_created_at` to rides that had
none. A revert leaves those timestamps in place, which is harmless (the pre-fix reader simply used
them). No remediation needed.

## 9. Verification performed

- [x] **Automated tests run.** Backend: 271 passing across `test_p2_sos` (22, incl. 4 new
      idempotency), `test_e2e_sos_flow`, `test_sos_expired_token`, `test_sos_paging`,
      `test_safety_checkin_loop`, `test_admin_safety_incidents`, `test_driver_discreet_sos_flag`,
      `test_coverage_rides`; plus 246 across the sharing suites incl. 7 new in
      `test_share_token_expiry`. Frontend: rider-app 15 suites / 148 tests (6 new
      `deriveContactOutcome`); driver-app 18 suites / 113 tests (2 new share-sheet, 1 new
      key-reuse). `tsc --noEmit` clean on both apps. `ruff check` + `ruff format --check` clean.
- [x] **Regression tests proven to fail against the old code.** The two F1 share tests were run
      against the pre-fix component via `git stash` → 2 failed / 5 passed, then pass after. They
      test the fix, not current behaviour.
- [x] **Blast-radius grep performed.** Searched: `SOSButton`, `triggerEmergency`, `SafetyOverlay`,
      `SafetyShield`, `/share`, `shared_trip_token`, `safety_incidents`, `regulatory_authority`,
      `license_class`, `required_documents`. Results tabulated in §4.
- [x] **Reviewed against CLAUDE.md conventions**: no float arithmetic (no money touched); PIPEDA —
      the dedup log line carries IDs only, no GPS/PII; error handling — the dedup lookup failure
      logs `logger.error` with the exception and falls through rather than silently swallowing;
      migration conventions — additive, nullable, rollback in header, prefix-unique (315).
- [ ] **Manual repro in staging — NOT DONE.** No staging environment was exercised in this session.
- [ ] **Production build — NOT RUN.** `tsc --noEmit` and jest only; no `expo export` / EAS build.
      Per CLAUDE.md this is explicitly *not* equivalent to a production build, so it is recorded as
      not done rather than implied.
- [x] **Feature-flag decision recorded** — see §5 for why the rider copy fix is not flagged.

## 10. What was NOT verified

Stated explicitly rather than left to silence:

- **Migration 315 has not been applied to any database.** The UNIQUE index is unproven against real
  data. If duplicate `(reported_by_user_id, sos_idempotency_key)` rows somehow existed the index
  creation would fail — they cannot, since the column is new and all-NULL, but the migration has
  still never actually run.
- **No device or simulator run.** The new small-size SOS label, the four dialog variants, and the
  driver share sheet were never seen rendered. This repo has **no visual-regression or snapshot
  tooling for any surface**, so this is reasoned, not screenshotted — a standing gap, not a
  one-off omission.
- **No live-backend or real-Twilio exercise.** Every contact-notification path is mocked. The
  `contacts_notified` values driving the new copy have only been observed from fixtures.
- **F3 blast radius is unquantified.** Nobody has counted how many production `rides` rows carry a
  `shared_trip_token` with a NULL `shared_trip_token_created_at`. That count is the real exposure
  and **should be queried before deploy**.
- **The 24h-vs-2h share-window disagreement is documented, not resolved.** Choosing the correct
  window is a product decision that has not been made.
- **One flaky test observed.** `driver-app SafetyOverlay.test.tsx` failed once on a cold run and did
  not reproduce across repeated warm runs with or without these changes. Not diagnosed.
- **Orphaned test directory found, not fixed.** `shared/**/__tests__` is executed by **no** runner —
  `jest --listTests` returns zero `shared/` matches in both apps, so the pre-existing
  `shared/api/__tests__/client.sos.test.ts` has never run in CI. The new `deriveContactOutcome`
  tests were relocated into `rider-app/__tests__/` to guarantee execution. Wiring up `shared/`
  is left as a separate change.
