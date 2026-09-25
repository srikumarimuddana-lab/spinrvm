# Change Impact & Risk Log — rider "No drivers available right now" ending

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (session_01QsLkWrRTmQ71WT3t75TCAu) |
| Surface(s) | backend, rider-app |
| Domain (Sentry tag) | rides / dispatch |
| PR / commit link | Branch `worktree-agent-a769f5d827a483b09`: `0a98e4e` (backend payload), `11dfd60`, `b34153c`, `bbc3e36`, `29b1346`, `de77eb3` (rider-app). Not pushed. |
| Related issue or gap ID | Phase 3 of `.claude/plans/2026-09-25-dispatch-reoffer-and-search-window.md` |

## 1. Issue / gap identified

When an on-demand ride is auto-cancelled because no driver accepted, the rider
is sent to home with at most a generic "Ride Cancelled" toast and no next step.
While searching, the `driver_timeout` toast ("The driver did not respond in
time. Finding another driver…") fires every ~15 s in batch dispatch and reads
as failure.

## 2. Root cause

- The rider app had no way to tell this cancel from any other. The DB row gets
  `cancellation_type = 'no_drivers_found'`, but `ride_search_timeout`'s rider
  `ride_cancelled` WS message carried only display text in `reason`, and
  neither sender (`ride_search_timeout`, `stuck_ride_sweeper`) put the cause on
  the push data. The app's `ride_cancelled` handler, the searching screen's
  status effect and the push-tap router all just `clearRide()` and
  `router.replace('/(tabs)')`.
- On foreground resume, `/rides/active` returns only "not active", and
  `fetchActiveRide()` clears the local ride without saying why.
- `driver_timeout` is sent once per expired offer round
  (`matching.py` batch handler, `ride_flow.py`, `auth.py`), and the app toasted
  every one.

## 3. Fix / remediation

Backend (additive only): `cancellation_type: "no_drivers_found"` added to the
rider `ride_cancelled` WS message, the `ride_status_changed` broadcast and the
push data in `ride_search_timeout`, and to the WS message and push data in
`stuck_ride_sweeper`. No existing key or copy changed.

Rider app:
- New root-mounted `NoDriversSheetHost` (reuses `ConfirmSheet`) shows
  "No drivers available right now" with **Try again**, **Schedule for later**
  and **Not now**. The rider is still sent home underneath, so dismissing it
  leaves them where the old flow did.
- The sheet is raised from every path the cancel can arrive by, each behind
  the existing on-screen-ride guard:
  - `ride_cancelled` WS: after `shouldLeaveScreenForRideCancelled` (the
    2026-09-23 cancel latch). Replaces the toast for this cause only.
  - `ride_status_changed` WS: carries `cancellation_type` onto the local ride
    so the searching screen's status effect can raise it if this event wins.
  - Searching screen (`driver-arriving`) status effect: only runs for
    `currentRide.id === rideId`; raises it when the polled ride is cancelled
    with `no_drivers_found`.
  - Push tap: after `shouldLeaveScreenForRideCancelled`.
  - Foreground resume: snapshots the searching ride before
    `fetchActiveRide()`, then if the server says "not active" does one
    `GET /rides/{id}` and raises it only for `no_drivers_found`.
- `store/noDriversStore.ts` shows one prompt per ride id; a duplicate signal
  for the same ride (WS + push + poll) or a late one after dismissal is a no-op.
- **Try again**: restores the same pickup/dropoff into the booking draft (keeps
  the draft, stops and vehicle choice if it still matches the ride; otherwise
  rebuilds pickup/dropoff from the ride), resets the pickup time to Now, and
  calls `clearEstimates()` — so `ride-options` fetches a **fresh quote** on
  mount and the booking sends that fresh quote's `estimate_token`. Nothing is
  booked automatically; the existing surge confirm sheet on `ride-options`
  still applies.
- **Schedule for later**: same draft preparation, then `ride-options` opens its
  existing `SchedulePicker` once on arrival (one-shot store flag).
- `driver_timeout`: toast removed; the refetch stays. The searching screen
  already shows the steady "Looking for a driver" state, so no new copy was
  needed.
- Strings: `ride.no_drivers_*` in `rider-app/i18n/en-CA.json` and `fr-CA.json`
  (the files the `en`/`fr` lookups read first; `es`/`zh` fall back to en-CA).

Alternative considered: keep the rider on the searching screen with the sheet
over the map. Rejected: after `clearRide()` that screen renders its searching
animation with no ride behind it, and keeping the ride would fight the cancel
latch and `fetchRide`'s cleared-ride guard. Sheet-over-home keeps every
existing navigation and store transition unchanged and only adds an overlay.

## 4. Risk & impact on existing functionality

Blast radius: backend payload is additive (cross-surface only in that the
admin monitoring socket also receives the extra field); rider-app change is
single-surface. No ride state transition, no DB write, no money path changed.

- **Backend payload consumers**: rider `ride_cancelled` WS and push → rider-app
  only. `ride_status_changed` from `broadcast_ride_status` goes to the rider and
  to admins (`admin-dashboard/src/hooks/use-monitoring-socket.ts`,
  `dashboard/monitoring/page.tsx`) — an extra key on an object those files
  read by name; no driver connection receives it (`driver_user_id` not passed).
  Driver-app `ride_status_changed` consumers (`useDriverDashboard.ts`,
  `_layout.tsx`, `driverStore.ts`) never get this event.
- **`useRiderSocket`** is used by `app/_layout.tsx`, `app/ai-assistant.tsx`,
  `app/ride-status.tsx`, `app/(tabs)/index.tsx` (comment reference) and
  `store/rideStore.ts` (comment). Changed cases: `ride_cancelled` (only the
  `no_drivers_found` branch differs), `driver_timeout` (toast removed),
  `ride_status_changed` (extra only gains `cancellation_type` for
  `cancelled` + `no_drivers_found`; `version` handling unchanged).
- **Cancel latch (2026-09-23 rebook fix)**: every raise point runs after the
  existing guard (`shouldLeaveScreenForRideCancelled` or
  `currentRide.id === rideId`). A late `ride_cancelled` for an older ride still
  does nothing (Jest-pinned). `resetBookingDraft`, `_clearedRideId`,
  `_clearEpoch` and `fetchRide`'s cleared-ride guard are untouched.
  `prepareRebookDraft` calls `clearRide()` only when `currentRide` is the
  cancelled ride itself.
- **Rider-initiated cancels**: `cancellation_type` is `rider_cancel`; no path
  raises the sheet (Jest-pinned for the socket and the searching screen).
- **`fetchActiveRide`** is not modified. The resume path adds one
  `GET /rides/{id}` only when a searching/driver_assigned ride was on screen
  and the server no longer reports it active.
- **`ride-options`**: one mount-only effect that does nothing unless the
  one-shot flag is set. Existing tests that mock `expo-router` without
  `useLocalSearchParams` are unaffected (a store flag was used instead of a
  route param for that reason).
- **`ConfirmSheet`**: unchanged; one more root instance. Its buttons have no
  `accessibilityRole` (pre-existing), which this sheet inherits.
- **Stripe**: the booking-time auth hold is already released on auto-cancel
  (`release_open_hold` in both senders, unchanged). Try again creates a **new**
  ride with a **new** hold through the normal booking path; no new money path.
- **Insurance periods / dispatch loops**: untouched.

## 5. User-experience effect

- Rider only. Visible when a search ends with no driver: instead of a toast and
  home, the rider sees "No drivers available right now — We couldn't find a
  driver for this trip. Try again to see an updated price, or schedule it for
  later." over home.
- During searching the rider no longer gets a toast every ~15 s; the searching
  screen's copy is unchanged.
- The existing push notification copy ("Ride Cancelled ❌ … Please try
  again." / "No drivers available …") is unchanged; a foregrounded rider may
  see that banner and the sheet together, as they previously saw the banner
  and the toast together.
- Mid-session: only riders on an older app build during the backend deploy
  see nothing new (they ignore the extra field). Riders on the new build need
  the new backend fields for the WS/push paths; the sweeper's WS path and the
  poll/resume paths (DB `cancellation_type`, already set today) work even
  before the backend change deploys.
- Copy reviewed for tone: specific, non-technical, tells the rider what to do.
  It deliberately does not say "you were not charged": the hold release is
  best-effort and a pending authorization can show on a statement for days.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/matching.py` | `cancellation_type` on rider WS, status broadcast and push data in `ride_search_timeout` (payload section only) | App needs a machine-readable cause |
| `backend/utils/stuck_ride_sweeper.py` | `cancellation_type` on WS and push data (payload section only) | Same payload as the timer, by design |
| `backend/tests/test_no_drivers_cancel_payload.py` | New | Pins both senders |
| `rider-app/utils/noDriversSignal.ts` (+test) | New `isNoDriversCancellation` | One definition of "this cancel" |
| `rider-app/store/noDriversStore.ts` (+test) | New prompt store, `offerNoDriversPrompt`, `prepareRebookDraft`, `raiseNoDriversPromptAfterResume` | Shared by all raise paths and the host |
| `rider-app/store/rideStore.ts` | `Ride.cancellation_type?` type field only | Typed access to a field the API already returns |
| `rider-app/components/NoDriversSheetHost.tsx` (+test) | New root host using `ConfirmSheet` | The sheet |
| `rider-app/i18n/en-CA.json`, `fr-CA.json` | `ride.no_drivers_*` | Copy |
| `rider-app/hooks/useRiderSocket.ts` (+ new test) | `ride_cancelled` no-drivers branch, `driver_timeout` toast removed, `ride_status_changed` extra | Raise path + calm searching |
| `rider-app/app/_layout.tsx` | Mount host; push-tap and foreground-resume raise paths | Raise paths |
| `rider-app/app/driver-arriving.tsx` (+test cases) | Status effect raises the prompt for `no_drivers_found` | Poll / status-event raise path |
| `rider-app/app/ride-options.tsx` | Mount-only effect opens `SchedulePicker` when requested | Schedule for later |

## 7. Before / after

```ts
// Before — hooks/useRiderSocket.ts, ride_cancelled (after the latch guard)
showToast('Ride Cancelled', cancelMessages[data.reason] || 'Your ride has been cancelled.', ...);
clearRide();
router.replace('/(tabs)');

// Before — driver_timeout
showToast('Driver Unavailable', 'The driver did not respond in time. Finding another driver…', 'info');
if (rideId) fetchRide(rideId);
```

```ts
// After — ride_cancelled (after the same latch guard)
const showedNoDrivers = isNoDriversCancellation(data) && offerNoDriversPrompt(rideState.currentRide);
if (!showedNoDrivers) showToast(/* unchanged */);
clearRide();
router.replace('/(tabs)');

// After — driver_timeout
if (rideId) fetchRide(rideId);
```

```python
# Before — ride_search_timeout push data
{"type": "ride_cancelled", "ride_id": r_id, "is_auto": "true"}
# After
{"type": "ride_cancelled", "ride_id": r_id, "is_auto": "true", "cancellation_type": "no_drivers_found"}
```

Concrete scenario: a rider in Saskatoon books a trip, the 10 drivers in range
decline or ignore it, and the timer cancels at 300 s. Before: toast "Ride Cancelled",
home. After: home with the sheet; Try again → ride-options for the same trip
with an empty fare list that reloads from `/rides/estimate`; if surge is now
1.5×, the card shows it and the existing surge confirm sheet appears before
booking; booking creates a new ride and a new card hold.

## 8. Rollback plan

- **Backend**: nothing to roll back in data. The new key is ignored by older
  clients; removing it is a code revert with no data remediation.
- **Rider app**: there is **no runtime flag**. The task scope excluded
  `settings`/migrations, so the existing `app_settings` → `GET /settings`
  flag pattern (e.g. `RidelessSosEnabledContext`) could not be used. Rollback
  is republishing the previous JS bundle via EAS Update (`eas update:rollback`
  or re-publishing the prior update to the channel) — an OTA publish, not a
  store build, but it is still a deploy. **This departs from CLAUDE.md
  pre-merge gate 3 (feature-flag user-visible, non-trivial UX); a human should
  decide whether to add an `app_settings` flag before merging.**
- No ride state, Stripe or wallet data is written by this change, so no data
  remediation is needed in either direction.

## 9. Verification performed

- [x] Backend: `ruff check` and `ruff format --check` clean on the two changed
  files and the new test; `python -m py_compile` OK. **pytest was not run**
  (not installed in this sandbox); the new test file was written for CI.
- [ ] Rider-app Jest tests written (`utils/__tests__/noDriversSignal.test.ts`,
  `store/__tests__/noDriversStore.test.ts`,
  `components/__tests__/NoDriversSheetHost.test.tsx`,
  `hooks/__tests__/useRiderSocket.noDrivers.test.ts`, two cases in
  `__tests__/driverArrivingScreen.test.tsx`) but **not executed**:
  `node_modules` are not installed and cannot be (npm registry blocked).
- [x] TypeScript: only a syntax-level transpile of every changed file
  (`ts.transpileModule`, no type resolution) — **no typecheck** was possible
  without `node_modules`, and **no production build** (`expo export` / EAS
  build) was run.
- [x] Blast-radius greps: `ride_status_changed` in admin-dashboard and
  driver-app; `useRiderSocket` importers; `cancellation_type` in rider-app;
  `resetBookingDraft` callers; `driver_timeout` senders in backend;
  `ConfirmSheet` / `expo-router` mocks in ride-options tests.
- [x] Reviewed against CLAUDE.md: state machine (no transition added), money
  (no new money path; fresh quote before booking; surge shown before booking),
  PIPEDA (no new logging of addresses/coords), observability (no new logs).
- [ ] Feature flag: not added — see §8.
- [ ] `spinr-*` reviewer agents (dispatch, accessibility, design-consistency)
  were **not run**; no agent-dispatch tool was available in this session.

## What was NOT verified

- Nothing was executed: no pytest, no Jest, no typecheck, no build, no device.
- rider-app has **no visual-regression tooling**. The sheet's layout, dark
  mode, two filled action buttons stacked, and screen-reader behaviour were
  reasoned about from `ConfirmSheet`'s code, not screenshotted or tested with
  VoiceOver/TalkBack. The host test stubs `ConfirmSheet`, so it covers wiring
  and copy only, not the bottom-sheet chrome.
- The race order of `ride_cancelled` vs `ride_status_changed` vs poll vs push
  on a real device was reasoned about, not observed. The one-prompt-per-ride
  latch is what makes the order irrelevant.
- Not tested against live Supabase / a real WebSocket; the backend test uses
  patched `_deps`.
- `app/ride-status.tsx` (the AI-assistant booking path) has no `cancelled`
  handling of its own; it relies on the global `ride_cancelled` socket
  handler, which now raises the sheet. Its poll path was not changed.
- Pre-existing gap, narrowed but not closed: if the foreground-resume lookup
  fails (network), `fetchActiveRide()` has already cleared the ride, and the
  rider can be left on the searching screen with no ride behind it until they
  tap Cancel. This change did not introduce it.
- The now-unused `ride.driver_timeout` / `driver_timeout_msg` i18n keys were
  left in place (surgical change); they can be removed separately.
