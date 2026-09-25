# Change Impact & Risk Log — countdown auto-decline counts as a missed offer

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code session, for the founder |
| Surface(s) | backend, driver-app |
| Domain (Sentry tag) | dispatch |
| PR / commit link | srikumarimuddana-lab/spinrvm#5776 — commits `bfcbd0e` (backend), `2785e8b` (driver-app), settings `e231a0a` + `110269c` |
| Related issue or gap ID | Phase 0 of `.claude/plans/2026-09-25-dispatch-reoffer-and-search-window.md` |

## 1. Issue / gap identified

A driver who leaves the driver app open and walks away is never taken
offline for missed offers. Found while planning the Saskatoon low-supply
dispatch changes (code reading, not a bug report).

## 2. Root cause

When an offer's countdown reaches 0 with the app in the foreground,
`driver-app/store/driverStore.ts` `setCountdown` calls `declineRide()`
itself with no reason. `decline_ride` (`backend/routes/drivers/ride_flow.py`)
records a real decline and calls `reset_miss_streak`. The miss streak that
drives `auto_offline_miss_threshold` (default 3) therefore goes back to 0 on
every expiry. Auto-offline only worked when the app was backgrounded or
killed, because then the server's own expiry ran instead.

## 3. Fix / remediation

- The driver app now sends `{"reason": "offer_expired"}` on the countdown
  auto-decline. The Decline button, the notification Decline action and the
  Android Auto Decline button are unchanged (plain decline, no body).
- New setting `settings.offer_expired_decline_as_miss_enabled` (migration
  466, default **false**, writable via `PUT /api/admin/settings`).
- With the flag on, `decline_ride` returns 200
  `{"success": true, "outcome": "left_to_expire", "already_resolved": false}`
  when the reason is `offer_expired`, the driver is not the ride's assigned
  driver, and the driver holds a `pending` `ride_offers` row for the ride.
  It does nothing else. The offer stays pending until `expires_at`, and the
  existing server expiry path processes it as a miss: `_batch_offer_timeout_handler`
  / `utils/offer_expiry_reaper.py` → `process_expired_offer` (legacy) or
  `expire_offer_v2` → `resolve_driver_offer('expire')` (v2).
- Why not call the expiry code directly from the route: the v2 RPC refuses
  `expire` before `expires_at` (`OFFER_NOT_EXPIRED`, migration 460), and a
  phone's countdown can reach 0 slightly early. Leaving the offer to the
  server avoids an early expiry and reuses the one idempotent path.

Alternative considered: have the driver app stop auto-declining and let the
server expire it. Rejected: older builds would still auto-decline, and the
app would lose its immediate "offer expired" feedback (G10).

## 4. Risk & impact on existing functionality

Blast radius: dispatch, cross-surface (backend + driver-app), flag-gated.

- `decline_ride` callers: the driver app store `declineRide` (offer card
  button, countdown), `services/backgroundMessaging.ts` `_declineHeadless`
  (notification action), `lib/androidAuto/register.ts` (Android Auto). Only
  the countdown path sends `offer_expired`.
- `reset_miss_streak` callers: `ride_flow.py` accept (~446) and decline,
  `routes/drivers/status.py` go-online/offline (~203, ~1350). Only the
  decline call is skipped, and only for this reason with the flag on.
- Expiry path reused, not modified: `process_expired_offer`,
  `_batch_offer_timeout_handler`, `offer_expiry_reaper` (10 s loop, runs on
  every replica, restart-safe), `expire_offer_v2`.
- Not changed: assigned drivers (admin direct-assignment / single-offer,
  `is_assigned`) keep the decline path, because that path is what reverts the
  ride to `searching`. Requests with no pending offer (server already
  expired it) fall through to today's handling, including today's 403.
- Driver availability: the driver stays claimed (`is_available` false,
  Period 2 open) from the app's auto-decline until the server expiry at
  `expires_at`. That is normally under a second, bounded by the offer
  timeout, and the same state the driver was already in.
- Ride state machine: no new transition. The ride stays `searching` and is
  re-dispatched by the expiry path, as today for a backgrounded app.
- Money: none.
- Audit: no `ride_declined` audit row is written for these. The expiry path
  records the offer as `expired`. Admin analytics that count declines will
  show fewer declines and more expiries once the flag is on — that is the
  correct classification.

## 5. User-experience effect

- **Drivers**, flag on, new build: a driver who misses 3 offers in a row
  with the app open is now taken offline and gets the existing "You're now
  offline" push and in-app notice. Visible mid-session to online drivers the
  moment the flag is turned on. This is the intended ghost-driver protection.
- Acceptance rate: unchanged in effect (a decline and a miss both count as
  not accepted).
- Older driver-app builds keep today's behaviour (no reason sent) until they
  update. `settings.min_driver_app_version` exists if a forced update is wanted.
- Riders: none directly. Fewer ghost drivers means fewer wasted 15 s rounds.
- No copy changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/466_settings_offer_expired_decline_as_miss.sql` | New settings column, default false | Flag |
| `backend/routes/admin/settings.py` | `offer_expired_decline_as_miss_enabled` field | Admin can set the flag |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | Column added to the snapshot | Drift guard |
| `backend/routes/drivers/ride_flow.py` | `_offer_expired_as_miss_enabled()` + early return in `decline_ride`; two comments updated | The fix |
| `backend/tests/test_decline_offer_expired_as_miss.py` | New: 6 cases | Coverage |
| `driver-app/store/driverStore.ts` | Countdown auto-decline passes `'offer_expired'` | Signal the backend |
| `driver-app/store/__tests__/driverStore.test.ts` | 2 new cases | Coverage |

## 7. Before / after

```ts
// Before — driver-app/store/driverStore.ts setCountdown
get().declineRide(incoming.ride_id).catch(console.log);
// After
get().declineRide(incoming.ride_id, 'offer_expired').catch(console.log);
```

```python
# Before — decline_ride: every decline, including the countdown one
await update_acceptance_rate(driver["id"], accepted=False)
await _deps.release_driver_and_close_period(driver["id"], reason="offer_declined", ride_id=ride_id)
await reset_miss_streak(driver["id"])

# After — flag on, reason "offer_expired", driver holds a pending offer
if reason == "offer_expired" and not is_assigned and await _offer_expired_as_miss_enabled():
    pending_offer = await db_supabase.get_rows("ride_offers", {..., "status": "pending"}, columns="id", limit=1)
    if pending_offer:
        return {"success": True, "outcome": "left_to_expire", "already_resolved": False}
# otherwise unchanged
```

## 8. Rollback plan

`UPDATE settings SET offer_expired_decline_as_miss_enabled = false;` (or
`PUT /api/admin/settings`). The settings cache is 60 s, so it takes effect
within a minute, no deploy. With the flag off the backend treats
`offer_expired` like any other decline — exactly today's behaviour — so the
driver-app change is harmless. Drivers already taken offline under the flag
just tap Go Online. No data to repair: offer rows are correctly `expired`.
Column drop SQL is in the migration header.

## 9. Verification performed

- [ ] Automated tests run — **not run.** pytest and jest cannot be installed
      in this sandbox (the network policy blocks pypi.org and
      registry.npmjs.org). Tests were written and will run in CI on #5776.
- [x] `ruff check`, `ruff format`, `python -m py_compile` on changed backend files.
- [ ] Manual repro in staging — not done.
- [x] Blast-radius grep: `decline_ride` / `declineRide` callers,
      `reset_miss_streak` callers, `offer_skip` writers/readers,
      `process_expired_offer` / `expire_offer_v2` / reaper, the
      `resolve_driver_offer` expire branch (migration 460).
- [x] Reviewed against CLAUDE.md: state machine (no transition added),
      insurance periods (Period 2 closed by the expiry path), observability
      (info log on the new branch; settings read failure logged at error).
- [x] Feature-flagged, default off.
- [ ] `spinr-dispatch-reviewer` and `spinr-insurance-period-auditor` — running
      on the diff at time of writing; this entry is updated with their findings.
- No production build of driver-app was run (no node_modules available).

## What was NOT verified

- No test was executed locally, backend or driver-app.
- Not run against real Supabase or on a device. The timing claim ("normally
  under a second" between the app's auto-decline and server expiry) is
  reasoned from the code, not measured.
- driver-app has no visual-regression tooling; this change has no UI change.
- How many live drivers run a build old enough to not send the reason is unknown.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
