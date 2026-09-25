# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (claude-sonnet-5), on behalf of ittalenthire.ca@gmail.com |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/fix-admin-assign-push-payload` (not pushed/opened as a PR — see task instructions) |
| Related issue or gap ID | ROADMAP N13 (`docs/audit/clean-sheet/ROADMAP.md`), finding SKB-001 (`docs/audit/clean-sheet/02-findings/support-kb.md`) |

## 1. Issue / gap identified

When an admin directly assigns a ride to a driver (`POST /api/admin/rides/create` with `driver_id`, in `backend/routes/admin/rides.py`), the FCM push notification sent to the driver's phone carries the ride's precise pickup/dropoff latitude/longitude and the rider's star rating in cleartext in the `data` payload, with no way to turn this off — unlike the normal auto-dispatch offer path (`backend/routes/rides/matching.py`), which has had a settings-gated `minimal_fcm_offer_payload_enabled` flag (migration 424) able to strip the same fields since PR #5382, currently defaulted off in production.

## 2. Root cause

`admin_create_ride` builds its FCM push independently of `matching.py`'s batch-dispatch path (three separate FCM-payload builders exist for the `new_ride_assignment` event type, per prior C113 blast-radius findings). When the `minimal_fcm_offer_payload_enabled` flag was added to `matching.py`, the admin-direct-assignment path was never updated to match — the closing note on the earlier C112 rider_name fix said this residual gap would be tracked as a new ACTION_ITEMS entry, but that entry was never actually filed (the "C114" label it promised was independently reused for an unrelated finding).

## 3. Fix / remediation

Extended `admin_create_ride`'s FCM push builder to read the same `minimal_fcm_offer_payload_enabled` `app_settings` flag `matching.py` already reads (via the `_admin_settings` fetch already present at this call site). When the flag is `True`, `pickup_lat`, `pickup_lng`, `dropoff_lat`, `dropoff_lng`, and `rider_rating` are excluded from the FCM `data` dict (same field set as `matching.py`'s `_FCM_EXCLUDE` addition — this path's payload has no `pickup_nav_lat/lng` or `stops` keys to strip), and an `offer_minimal: "true"` marker is added, matching `matching.py`'s own marker byte-for-byte. When the flag is `False` (today's production default), behavior is unchanged.

**Decision: gated, not unconditional.** Before implementing, I searched driver-app's push handling (see §4) and found the background/killed-app FCM handler (`driver-app/services/backgroundMessaging.ts`) keys off `data.type === 'new_ride_assignment'` generically — it does not distinguish an admin-direct-assignment push from a batch-dispatch offer push — and reads `pickup_lat`/`pickup_lng`/etc. straight off the FCM `data` payload *unless* `data.offer_minimal === 'true'` tells it to refetch via the authenticated `GET /drivers/rides/{ride_id}/offer` endpoint instead. That endpoint (`backend/routes/drivers/ride_reads.py`'s `get_ride_offer`) already has an explicit `is_direct_assigned` branch built specifically to serve the admin-assignment shape (no `ride_offers` row, `rides.driver_id` + `driver_assigned` status instead) — so the refetch fallback is proven to work for this exact call site, not just the auto-dispatch one.

Given that, stripping the fields unconditionally (without the `offer_minimal` marker) would have been a real regression: the client's `isMinimalOffer` check would stay `false`, it would read `data.pickup_lat` from a payload where that key no longer exists, and `toNum(undefined) ?? 0` would silently default the driver's background offer card to a `(0, 0)` map pin — the exact failure mode a comment in `backgroundMessaging.ts` already calls out by name for the batch-dispatch case. Gating behind the flag (and setting the marker only when it fires) reuses the client's already-built, already-tested refetch path instead of introducing a payload shape the client has never seen.

**Alternative considered:** strip unconditionally, as the audit finding's own primary recommendation suggested (on the theory that "the admin panel doesn't need a background-refetch fallback the way the driver app's offer-timeout path does"). Rejected — that theory doesn't hold: the driver-app's FCM handler doesn't special-case the admin path at all, so it inherits the exact same refetch-vs-default-to-(0,0) behavior as the auto-dispatch path, and the client-side fallback already exists and is exercised by `driver-app/__tests__/services/backgroundMessaging.android.test.ts`. Gating wins on cost (reuses proven code, zero new client logic) and risk (no chance of an unhandled-field regression) at the cost of leaving the fix "off" until an operator flips the flag — an acceptable trade since SKB-002 already documents the flag shipping off by design pending driver-app rollout confirmation, and this fix rides the same switch rather than adding a second one.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the admin-direct-assignment push-build block in `admin_create_ride`.** Grepped `backend/routes/admin/` for `dispatch_payload` and `_ADMIN_FCM_EXCLUDE` — no other admin endpoint builds this payload shape or reads this exclusion set.
- **`routes/rides/matching.py` (normal auto-dispatch path) was deliberately NOT changed.** It already has its own, independent flag check (`_minimal_offer_payload = bool(app_settings.get("minimal_fcm_offer_payload_enabled", False))`) at the point it builds its own FCM payload — reusing the *same* `app_settings` key from a second call site is consistent behavior, not a shared code path, so there was nothing to touch there for consistency. Confirmed via `grep -n "minimal_fcm_offer_payload_enabled" backend/routes/rides/matching.py` (line 1652, unmodified) and a full diff review of that file (empty).
- **Driver-app consumer, verified in detail** (see §3): `driver-app/services/backgroundMessaging.ts`'s background/killed-app FCM handler (function registered via `registerBackgroundMessageHandlers`) is the only place in `driver-app/` that reads `new_ride_assignment` FCM `data` fields — confirmed by grepping `driver-app/` for `pickup_lat` reads outside WS-payload/API-response consumers; the foreground app renders offers from the WebSocket `dispatch_payload` (untouched by this change, same as it always was) or from `GET /drivers/rides/{ride_id}/offer`, never from the FCM `data` object directly. `GET /drivers/rides/{ride_id}/offer` (`backend/routes/drivers/ride_reads.py:276+`) is the shared re-fetch endpoint both the auto-dispatch and admin-direct-assignment minimal-payload paths rely on; it was already built to serve both shapes (`is_direct_assigned` branch), so no change was needed there either.
- **No other reader of `dispatch_payload` or the admin push's FCM `data` dict exists** — the WS message (`manager.send_personal_message`) is a separate object built from the same `dispatch_payload` dict but is never filtered, so it is unaffected by either exclusion set at any point.
- **Money/state-machine impact: none.** This change only removes fields from an outbound push payload; it does not touch `rides.status`, driver claim/offer logic, insurance-period writes, or any Decimal/money path.
- **Dry run (flag ON scenario, `mock_supabase_client`/`AsyncMock` fixtures):** admin assigns ride to `drv-1`, rider rating `4.8`, pickup `(50.4, -104.6)` → FCM `data` dict has no `pickup_lat`/`pickup_lng`/`dropoff_lat`/`dropoff_lng`/`rider_rating`/`rider_name` keys and carries `offer_minimal: "true"`; WS payload to the same driver still carries `pickup_lat: 50.4` and `rider_rating: 4.8` in full. See new test `test_create_ride_dispatch_push_minimal_flag_strips_coordinates_and_rating`.

## 5. User-experience effect

- **Rider:** no change — this is a driver-facing push shape change only.
- **Driver, flag off (today's production default): zero visible change.** Byte-for-byte identical push payload to before this fix.
- **Driver, flag on (not live yet — same rollout gate as SKB-002):** the visible notification text (title "New ride request", body `"{pickup} → {dropoff}"`) is unchanged — only the underlying FCM `data` payload's field set changes. If the driver is backgrounded/app-killed when the push arrives, the app now performs one extra authenticated network round-trip (`GET /drivers/rides/{ride_id}/offer`, 5s client-side timeout) before rendering the local offer notification with the precise pickup pin and rider rating; on a slow/failed fetch it falls back to a degraded notification (address labels and fare still present; precise pin and rating temporarily absent) rather than showing nothing, matching the already-shipped auto-dispatch behavior under the same flag. This is not a new UX pattern for drivers — it is the same code path (`backgroundMessaging.ts`) they already experience for batch-dispatched offers once the flag is on; this change only makes admin-assigned rides behave the same way.
- **Not visible mid-session** to anyone already using the app — this only affects the moment a new admin-assigned ride offer arrives.
- **No copy/notification-text change.**

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/rides.py` | Admin-assign FCM push now reads `minimal_fcm_offer_payload_enabled` from the already-fetched `_admin_settings`; when `True`, adds `pickup_lat`/`pickup_lng`/`dropoff_lat`/`dropoff_lng`/`rider_rating` to the FCM exclusion set and sets `offer_minimal: "true"` on the push data. WS message and default (flag-off) behavior unchanged. | SKB-001/N13 — close the PII gap this admin path had with no way to turn it off, consistent with the auto-dispatch path's existing flag. |
| `backend/tests/test_admin_rides_coverage.py` | Updated the flag-off test's docstring (the "no such flag exists" premise it documented is now false) and added a new flag-on regression test asserting the five fields are excluded, `rider_name` stays excluded, and `offer_minimal` is set; WS payload still carries full detail either way. | Regression coverage for the new gated behavior, per CLAUDE.md testing conventions. |

## 7. Before / after

```python
# Before
_ADMIN_FCM_EXCLUDE = {"rider_name"}
await send_push_notification(
    driver["user_id"],
    "New ride request",
    f"{ride_doc['pickup_address']} → {ride_doc['dropoff_address']}",
    {k: str(v) for k, v in dispatch_payload.items() if v is not None and k not in _ADMIN_FCM_EXCLUDE},
    priority="dispatch",
    target_app="driver",
)
```

```python
# After
_ADMIN_FCM_EXCLUDE = {"rider_name"}
_admin_minimal_offer_payload = bool(_admin_settings.get("minimal_fcm_offer_payload_enabled", False))
if _admin_minimal_offer_payload:
    _ADMIN_FCM_EXCLUDE = _ADMIN_FCM_EXCLUDE | {
        "pickup_lat", "pickup_lng", "dropoff_lat", "dropoff_lng", "rider_rating",
    }
_admin_fcm_data = {
    k: str(v) for k, v in dispatch_payload.items() if v is not None and k not in _ADMIN_FCM_EXCLUDE
}
if _admin_minimal_offer_payload:
    _admin_fcm_data["offer_minimal"] = "true"
await send_push_notification(
    driver["user_id"],
    "New ride request",
    f"{ride_doc['pickup_address']} → {ride_doc['dropoff_address']}",
    _admin_fcm_data,
    priority="dispatch",
    target_app="driver",
)
```

## 8. Rollback plan

No migration, no new flag introduced — this change reads the existing `minimal_fcm_offer_payload_enabled` `app_settings` row that `matching.py` already governs. Since that flag defaults to `False` in production, this change ships **dark by default**: nothing changes for any driver until an operator flips the flag on, at which point both the auto-dispatch and admin-assign paths change together. If the flag is ever turned on and something goes wrong specifically with the admin-assignment shape, flip `minimal_fcm_offer_payload_enabled` back to `False` in `app_settings` via the admin dashboard (no redeploy, no code revert) — this reverts both paths to full-payload behavior simultaneously, which is the same rollback lever SKB-002 already documents for the auto-dispatch path. A `git revert` of this commit is also safe on its own (this change touches no persisted rows, wallet deltas, or ride state), but is not needed for the live-behavior rollback described above.

## 9. Verification performed

- [x] Automated tests run (unit): `cd backend && python -m pytest -o addopts="" -q -p no:cacheprovider tests/test_admin_rides_coverage.py tests/test_minimal_fcm_offer_payload_flag_settings.py tests/test_admin_rides_read_endpoints_coverage.py tests/test_admin_rides_cancel_state.py tests/test_admin_scheduled_ride_config.py tests/test_offer_notification_deadline.py tests/test_p3_push_notifications.py tests/test_notification_throttle.py tests/test_driver_status_notifications.py` — all 230 passed (12 in `TestAdminCreateRide` alone, including the 2 new/updated ones).
- [x] `ruff check backend/routes/admin/rides.py backend/tests/test_admin_rides_coverage.py` — clean.
- [x] `ruff format --check backend/routes/admin/rides.py backend/tests/test_admin_rides_coverage.py` — clean, no changes needed.
- [x] Manual repro steps followed (via mocked fixtures, no staging environment available in this session): flag-off dry run (existing test) and flag-on dry run (new test) both traced through `admin_create_ride` end to end with `mock_supabase_client`/`AsyncMock` FCM and WS sends — see §4 dry-run summary.
- [x] Blast-radius grep performed (list what was searched): `dispatch_payload` + `_ADMIN_FCM_EXCLUDE` across `backend/routes/admin/`; `minimal_fcm_offer_payload_enabled` across `backend/routes/`, `backend/schemas.py`; `pickup_lat`/`new_ride_assignment`/`offer_minimal` across `driver-app/services/`, `driver-app/store/`, `driver-app/lib/androidAuto/` (see §4 and the final report's file:line findings).
- [x] Reviewed against relevant CLAUDE.md conventions: PIPEDA (raw GPS/rating not in payload when flag on), background-task/flag-gating pattern (`app_settings`, no redeploy needed), "no silent behavior change" (flag-off path byte-for-byte unchanged, proven by keeping the old assertion test).
- [x] Feature-flagged: reuses `minimal_fcm_offer_payload_enabled` rather than introducing a second flag for the same class of data (see §3 decision).
- [ ] Manual staging check — **not performed**; no staging/live environment access in this session (see §10 below).

## 10. Sign-off / What was NOT verified

- **Not exercised against a real Supabase, real FCM, or a real driver-app build** — only `mock_supabase_client`/`AsyncMock`-mocked backend unit tests were run. FCM delivery semantics (payload size, `data`-vs-`notification` block handling on real Android/iOS OS versions) were reasoned about from existing code comments, not observed live.
- **No production build (`expo`/EAS build or equivalent) of driver-app was run.** This task's code changes are backend-only (`backend/routes/admin/rides.py`); no driver-app files were modified. driver-app has no visual regression tooling at all (per CLAUDE.md's mandatory disclosure for rider-app/driver-app), and this change makes no driver-app UI change regardless — the client-side behavior this fix relies on (`backgroundMessaging.ts`'s `isMinimalOffer`/`_fetchOfferHeadless` path and `GET /drivers/rides/{ride_id}/offer`'s `is_direct_assigned` branch) was reasoned about by reading the existing, already-shipped, already-tested code and its own test file (`driver-app/__tests__/services/backgroundMessaging.android.test.ts`), not by running or screenshotting the driver app.
- **The flag itself was not flipped on anywhere** — this change only extends what happens *if* an operator turns `minimal_fcm_offer_payload_enabled` on; per SKB-002, whether it is safe to do so in production (driver-app installed-base coverage of the refetch handler) is a separate, still-open verification this task does not close.
- **`ruff` was run only on the two files touched**, not the full repo lint pass.
- [x] Rollback plan is concrete and testable (flip the existing `app_settings` flag — no code revert needed for the live-behavior case)
- [x] Blast radius is stated, not assumed (isolated to the one admin-path push-build block; `matching.py` explicitly confirmed unmodified and independently flag-gated)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states the flag-off no-op and the flag-on background-refetch UX explicitly)
