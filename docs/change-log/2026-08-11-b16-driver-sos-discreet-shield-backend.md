# Change Impact & Risk Log

> Covers subtasks 1–4 of ACTION_ITEMS.md B16's approved implementation plan
> (the backend half). Frontend subtasks 5–12 land as further commits on the
> same branch/PR; this doc will be extended (not replaced) once the
> user-visible wiring subtask (11) merges. See the plan for full context.

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude |
| Surface(s) | backend |
| Domain (Sentry tag) | safety, rides |
| PR / commit link | branch `claude/b16-driver-sos-discreet-shield` |
| Related issue or gap ID | `ACTION_ITEMS.md` B16 (driver SOS discreet-hold-shield UX) |

## 1. Issue / gap identified

B16: the shipped driver SOS button is the design sketch's own **rejected** variant — one persistent red button, interruptive `Alert.alert()` on success, visible to any in-vehicle passenger. Product confirmed the winning "Discreet Hold Shield" design is still wanted for the driver surface; this backend increment lays the groundwork the frontend needs: per-contact SOS status, driver access to the trip share link, and a dark-launch rollout flag.

## 2. Root cause

The driver and rider apps share one `SOSButton` component with no discreet path, and `trigger_emergency`'s response/authorization surface was built rider-first — it never returned per-contact delivery status (only an aggregate count) and `GET /{ride_id}/share` never considered a driver caller.

## 3. Fix / remediation

Four small, additive backend changes (subtasks 1–4 of the approved plan):
1. `trigger_emergency` now returns a `contacts: [{id, name, notified}]` array (built from data already computed, previously discarded) alongside the unchanged `contacts_notified` count.
2. `GET /{ride_id}/share` now also authorizes the ride's assigned driver, not just the rider (still 403s any non-participant) — needed for the upcoming Safety overlay's "Share Live Trip Link" action.
3. `driver_discreet_sos_enabled: bool = False` added to `AppSettings`/`SettingsUpdateRequest` — the dark-launch gate for the frontend rollout.
4. The flag is exposed via `GET /settings` (same spot `track_base_url` is exposed for the identical "mobile needs a dark-launched value without a rebuild" reason).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** No existing response field was removed or renamed; the only behavior change is `GET /{ride_id}/share` now accepting one more caller identity (the assigned driver) — a genuine non-participant still gets 403 (regression-tested).
- **Other readers of `trigger_emergency`'s response:** none found beyond `SOSButton.tsx` (rider-app + driver-app), which only reads implicitly via a resolved promise and doesn't inspect response fields today — adding `contacts` is additive and inert until a caller reads it (the upcoming `SafetyShield`/`SafetyOverlay`).
- **Other readers of `GET /{ride_id}/share`:** `rider-app/app/ride-in-progress.tsx`'s `handleShareTrip` — unaffected, still rider-authorized as before.
- **Other readers of `GET /settings`:** every rider-app and driver-app screen that reads public settings (Google Maps key, Stripe publishable key, track base URL) — unaffected, new field is additive.
- **No ride-state-machine, WebSocket, or money-path changes.**

## 5. User-experience effect

None yet — `driver_discreet_sos_enabled` defaults `False` and nothing in the shipped mobile apps reads it or the new `contacts` field until the (not-yet-merged) frontend wiring subtask lands. This backend increment is not itself user-visible.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/safety.py` | Added `contacts` array to `trigger_emergency`'s response | Per-contact "✓ Notified" data for the Safety overlay |
| `backend/routes/rides/sharing.py` | `GET /{ride_id}/share` now also authorizes the assigned driver | Driver-side "Share Live Trip Link" |
| `backend/schemas.py` | Added `driver_discreet_sos_enabled: bool = False` | Rollout flag schema |
| `backend/routes/admin/settings.py` | Added matching `Optional[bool]` field to `SettingsUpdateRequest` | Admin can set the flag |
| `backend/routes/settings.py` | Exposed the flag on `GET /settings` | Mobile apps can read the flag |
| `backend/tests/test_p2_sos.py`, `test_coverage_rides.py`, `test_driver_discreet_sos_flag.py` (new), `test_public_settings.py` (new) | Test coverage for all of the above | — |

## 7. Before / after

```python
# Before (routes/rides/sharing.py::get_share_trip_link)
if ride.get("rider_id") != current_user["id"]:
    raise HTTPException(status_code=403, detail="Not authorized to share this ride")
```
```python
# After
is_rider = ride.get("rider_id") == current_user["id"]
driver = (lambda _r: _r[0] if _r else None)(
    await _deps.db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
)
is_driver = driver and ride.get("driver_id") == driver["id"]
if not (is_rider or is_driver):
    raise HTTPException(status_code=403, detail="Not authorized to share this ride")
```

## 8. Rollback plan

`git-revert-safe` for all four subtasks — no migration, no data mutation. Reverting removes the new response field, the driver-share authorization, and the flag schema; nothing durable depends on any of them yet since no shipped client reads them.

## 9. Verification performed

- [x] Automated tests run: `test_p2_sos.py` (18/18), `test_e2e_sos_flow.py`/`test_sos_paging.py`/`test_sos_expired_token.py` (24/24), `test_coverage_rides.py` (172/172), `test_driver_discreet_sos_flag.py` (4/4, new), `test_public_settings.py` (3/3, new), `test_admin_settings_lms_gate.py`/`test_admin_settings_company_logo.py`/`test_admin_settings_payment_credential_gate.py` (42/42) — all via `pytest ... -v --no-cov`.
- [ ] Manual repro against staging/real Supabase — not done; unit-tested against `mock_supabase_client`-style fixtures per this repo's convention.
- [x] Blast-radius grep performed: confirmed the only readers of the touched response fields/endpoints (listed in §4).
- [x] Reviewed against relevant `CLAUDE.md` conventions: additive-not-destructive, dark-launch flag via the existing `app_settings` pattern, PIPEDA (no PII added to logs).
- [x] Feature-flagged: `driver_discreet_sos_enabled`, default off.

**What was NOT verified:**
- No real Twilio SMS delivery testing (mocked, same boundary the existing SOS tests already have).
- No real Supabase/staging repro.
- This doc covers subtasks 1–4 only; the frontend subtasks (5–12) and their own verification will extend this document before the PR is marked ready for review.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data footprint)
- [x] Blast radius is stated, not assumed (isolated; enumerated in §4)
- [x] No silent behavior change to an already-shipped flow — the one auth change (driver can now fetch share link) is additive-permission, not a restriction, and nothing shipped reads the new fields yet
