# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (spinr session) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/c112-admin-assign-fcm-pii-filter` (see PR) |
| Related issue or gap ID | ACTION_ITEMS.md C112 |

## 1. Issue / gap identified

`backend/routes/admin/rides.py`'s `admin_create_ride` (the admin-direct-assignment path — an
admin manually assigns a ride to a specific driver) sends its FCM push to the driver with zero
PII filtering: `rider_name` (the rider's full first+last name, falling back to their raw email
or phone number via `_user_display_name` if both name fields are blank) rode in the FCM `data`
payload in cleartext, via Google/Apple push infrastructure, for every admin-direct-assigned ride.

## 2. Root cause

`backend/routes/rides/matching.py`'s normal auto-dispatch path already excludes `rider_name`
from its own FCM `data` payload via a local `_FCM_EXCLUDE` set (added for a prior item, C5).
`admin_create_ride` builds a separate, independent `dispatch_payload`/push and was never updated
to apply the same exclusion when that fix landed on the sibling path.

**Correction to this task's original brief:** the brief that assigned this fix additionally
claimed `matching.py`'s `_FCM_EXCLUDE` gates `pickup_lat`/`pickup_lng`/`dropoff_lat`/`dropoff_lng`/
`rider_rating` behind a `minimal_fcm_offer_payload_enabled` flag from "PR #5382," and that
ACTION_ITEMS.md already had an OPEN `### C112` entry describing this bug. Neither is true: a full
repo grep (`backend/`, `docs/`, migrations, git log for `matching.py`) found zero references to
`minimal_fcm_offer_payload_enabled` or PR #5382, and ACTION_ITEMS.md had no C112 entry at all
before this change (highest existing item was C111, duplicate-numbered by two parallel sessions).
`matching.py`'s real, current `_FCM_EXCLUDE` unconditionally excludes only 4 fields:
`service_area_polygon`, `planned_route_polyline`, `rider_profile_image`, `rider_name` — precise
coordinates and `rider_rating` are sent **unfiltered** by `matching.py` today too. This fix is
scoped to what's actually real: bringing `admin_create_ride` to parity with `matching.py`'s
*actual* unconditional `rider_name` exclusion. It does not add new flag-gated coordinate/rating
stripping to either path, since no such flag exists to reuse and inventing one for only one call
site would leave the two paths out of parity with each other rather than closing the gap.

## 3. Fix / remediation

Added a local `_ADMIN_FCM_EXCLUDE = {"rider_name"}` set in `admin_create_ride`, applied to the
dict comprehension that builds the FCM push's `data` payload — mirroring `matching.py`'s existing
pattern (a local exclusion set, not a module constant, matching that file's own style). The
WebSocket message to the driver (`manager.send_personal_message`) is a separate transport with no
third-party transit and is unchanged — it still carries `rider_name` for the driver's in-app
offer panel, exactly as `matching.py`'s WS message does for the auto-dispatch path.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `dispatch_payload` in `admin_create_ride` has exactly one reader
  besides the WS send — the FCM push dict comprehension being changed here. Grepped
  `backend/routes/admin/rides.py` for `dispatch_payload` — no other call sites.
- **Consumer verified:** `driver-app/services/backgroundMessaging.ts`'s `offerDisplayDataFromFcm`
  (the shared FCM-data parser for both the killed/background handler and the foreground listener)
  reads `data.rider_name` with no required-field assumption — a missing key simply yields
  `undefined`, same as every other optional field it reads. This exact field has been absent from
  `matching.py`'s FCM payload in production for the life of that exclusion (C5) with no reported
  driver-app issue, so removing it from this second call site carries the same, already-proven-safe
  risk profile.
- **Third sibling found, not touched:** blast-radius grep for other `new_ride_assignment` FCM
  payload builders (done as part of this fix's `/code-review` pass) found a third one —
  `backend/routes/notifications.py`'s `admin_debug_ride_offer` (an admin-only diagnostic endpoint)
  — which hardcodes `"rider_name": "Debug Rider"` (a literal, not real rider data, so no live leak)
  but carries a comment that inaccurately claims parity with the live dispatch path's exclusions.
  Filed as a new, separate, OPEN item (ACTION_ITEMS.md C113) rather than folded into this fix, to
  keep this PR to one logical change — it touches a diagnostic tool, not a live-rider-data path,
  and needs its own review of whether/how to harden it.
- No ride-state, money, or insurance-period code path is touched. No other reader of
  `admin_create_ride`'s dispatch payload exists.

## 5. User-experience effect

None visible to riders, drivers, or admins. The driver's in-app offer panel (fed by the WS
message) is unchanged and still shows the rider's name. The only change is what's no longer
present in the OS push notification's underlying data payload (which was never rendered to the
driver as visible text anyway — the push title/body are server-built strings, `"New ride
request"` / `"{pickup} → {dropoff}"`, with no `rider_name` interpolation on either path).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/rides.py` | Added `_ADMIN_FCM_EXCLUDE = {"rider_name"}` and applied it when building the FCM push `data` dict in `admin_create_ride` | Stop sending the rider's full name (or raw email/phone fallback) through third-party push infrastructure in cleartext |
| `backend/tests/test_admin_rides_coverage.py` | Added 3 tests: `rider_name` excluded (name case), excluded (email-fallback case), and unchanged fields (coordinates/rider_rating) still present | Regression coverage for the fix and an explicit record of what was deliberately left unchanged |
| `docs/change-log/2026-09-14-admin-assign-fcm-pii-filter.md` | This file | Mandatory Change Impact Log for a live-tested rides/dispatch surface |
| `ACTION_ITEMS.md` | Added C112 (this fix, closed) and C113 (the `notifications.py` sibling finding, open) | Tracking — C112 did not previously exist despite the assigning brief's claim that it did |

## 7. Before / after

```python
# Before
await send_push_notification(
    driver["user_id"],
    "New ride request",
    f"{ride_doc['pickup_address']} → {ride_doc['dropoff_address']}",
    {k: str(v) for k, v in dispatch_payload.items() if v is not None},
    priority="dispatch",
    target_app="driver",
)
```

```python
# After
_ADMIN_FCM_EXCLUDE = {"rider_name"}
await send_push_notification(
    driver["user_id"],
    "New ride request",
    f"{ride_doc['pickup_address']} → {ride_doc['dropoff_address']}",
    {
        k: str(v)
        for k, v in dispatch_payload.items()
        if v is not None and k not in _ADMIN_FCM_EXCLUDE
    },
    priority="dispatch",
    target_app="driver",
)
```

## 8. Rollback plan

No feature flag — this is an unconditional bug fix removing PII from a third-party channel, not a
new behavior needing a staged rollout (matching how `matching.py`'s own equivalent exclusion,
C5, shipped unconditionally). Rollback is `git revert` of this commit: it touches only what's
built in-process for one outbound push call, writes nothing to the database, and reverting simply
restores the prior (already-live, already-accepted-risk) behavior — there is no live-data state
to reconcile, unlike a Stripe charge or wallet delta.

## 9. Verification performed

- [x] Automated tests run (unit): `pytest backend/tests/test_admin_rides_coverage.py` (179 passed,
  including 3 new tests), `pytest backend/tests/test_admin_rides_cancel_state.py
  backend/tests/test_admin_rides_read_endpoints_coverage.py` (no regression),
  `pytest backend/tests/test_dispatch_notify_loop_branches.py backend/tests/test_loguru_call_conventions.py`
  (sibling-path and logging-convention regression check, 16 passed)
- [ ] Manual repro steps followed in staging — not done; no staging Supabase/FCM credentials
  available in this environment (see "What was NOT verified")
- [x] Blast-radius grep performed: searched `dispatch_payload` in `routes/admin/rides.py`
  (single use site), and `new_ride_assignment` across `backend/routes/` (found the two other
  builders — `matching.py`, already correct; `notifications.py`, filed as C113)
- [x] Reviewed against relevant CLAUDE.md convention(s): PIPEDA (no full name/raw email/phone in
  a third-party-transiting payload), surgical-changes (single exclusion, no unrelated cleanup),
  adversarial review (gate #10 — ran `/code-review` at high effort; see finding on C113 above)
- [x] Feature-flagged if user-visible and non-trivial, or justified why not: not user-visible
  (see §5); no flag, matching the sibling fix's own unconditional rollout (§8)
- [x] `ruff check` and `ruff format --check` run on both touched Python files — clean

## 10. What was NOT verified

- **Not tested against live Supabase or live FCM/Firebase** — only `mock_supabase_client`-style
  mocks via the existing `test_admin_rides_coverage.py` fixture pattern. No real device or real
  Firebase project was used to confirm the push actually renders correctly with the field absent.
- **Native killed-app FCM execution was not exercised** — this is the same caveat the sibling
  `matching.py` exclusion (C5) already carries for its own `rider_name` removal; this fix removes
  the same field via the same mechanism, so it inherits the same unverified-on-a-real-device
  status, not a new one. (This caveat applies to `rider_name` specifically; this fix does not
  touch the flagged coordinate/rating path, since no such flag exists — see §2.)
- **`routes/notifications.py`'s `admin_debug_ride_offer` was not fixed** — found during this
  fix's blast-radius check, filed separately as ACTION_ITEMS.md C113 (open), not touched here to
  keep this change to one logical scope.
