# Change Impact & Risk Log — N10 batch 1: rider pushes missing `target_app="rider"`

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude Code (session_01Wk3M9NdQJWqgpATtogSjD8) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides, payments, dispatch |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | `ACTION_ITEMS.md` N10 |

## 1. Issue / gap identified

`backend/features.py::send_push_notification(user_id, title, body, data=None,
priority="normal", target_app=None)` reads a per-app FCM token column keyed
off `target_app`: `"rider"` → `fcm_token_rider`, `"driver"` → `fcm_token_driver`,
`None` → the legacy `fcm_token` column. Most rider-directed pushes in the
codebase omit `target_app="rider"` and so silently fall through to the legacy
column. This works today only because `routes/notifications.py`'s token
registration flow (`POST /notifications/register-token`, lines ~329-336) still
mirrors every token write onto **both** the legacy `fcm_token` column and the
per-app column (`fcm_token_rider` / `fcm_token_driver` / both, depending on
inferred/declared `client_type`). If that mirroring is ever removed or a
future registration path stops doing it, every un-annotated rider push would
silently stop resolving a token — a push that appears to succeed in code but
never reaches a device, with no error surfaced.

## 2. Root cause

`target_app` was added to `send_push_notification` later than most of its
call sites, which were written back when the function only read the single
legacy `fcm_token` column. Call sites were never swept to backfill the new
parameter; only new/recently-touched code (e.g. `routes/rides/chat.py`,
`utils/live_activity.py`, dispatch's driver-offer path) already passes it
explicitly.

## 3. Fix / remediation

Original finding scoped a ~30-call-site sweep across ~17 files (excluding
`backend/routes/rides/cancellation.py` and `backend/utils/receipt_email.py`,
both owned by parallel sessions this run). Per the repo's task-decomposition
convention (≤3-5 files per batch, see B22/A1c precedent), this session fixed
the clearest, least-ambiguous, unambiguously-rider-directed call sites first
and logged the rest as remaining scope (§ Remaining scope below) rather than
guessing on the ambiguous ones.

**10 call sites fixed, across 5 files**, adding `target_app="rider"` to an
already-rider-directed `send_push_notification(...)` call — no other argument
changed:

| File:line | Push | `user_id` argument resolves to |
|---|---|---|
| `backend/routes/rides/lifecycle.py:113` | "Ride Started! ▶️" | `ride.get("rider_id")` |
| `backend/routes/rides/matching.py:1347` | "Ride Cancelled ❌" (no-drivers-found auto-cancel) | `current_ride["rider_id"]` |
| `backend/utils/scheduled_rides.py:93` | "Still working on your scheduled ride" (escalated delay notice) | `rider_id` param of `_notify_schedule_delayed` |
| `backend/utils/scheduled_rides.py:102` | "Your scheduled ride is waiting" (routine delay notice) | `rider_id` param of `_notify_schedule_delayed` |
| `backend/utils/scheduled_rides.py:302` | "Your scheduled ride is on hold" (corporate policy re-check blocked) | `ride.get("rider_id")` |
| `backend/utils/scheduled_rides.py:492` | "Your scheduled ride is starting!" (dispatch fired) | `rider_id` (from claimed ride) |
| `backend/utils/scheduled_rides.py:537` | "Ride reminder - 10 minutes" | `ride.get("rider_id")` |
| `backend/utils/stuck_ride_sweeper.py:117` | "No drivers available" (stuck-ride sweep auto-cancel) | `rider_id` (from claimed ride) |
| `backend/services/payment_service.py:1355` | "Payment failed" (card declined, fresh-charge path) | `ride.get("rider_id")` |
| `backend/services/payment_service.py:1402` | "Payment failed" (generic charge failure, fresh-charge path) | `ride.get("rider_id")` |

Every one of these is unambiguous: the `user_id` argument is read directly
from `ride["rider_id"]`/`ride.get("rider_id")` or a function parameter that
is itself sourced the same way, in code paths gated to riders (e.g.
`routes/disputes.py::create_dispute` requires `ride.get("rider_id") ==
current_user["id"]` before a dispute can even be filed — but disputes.py
itself is **not** in this batch, see below).

**Files checked and found already correct (no change needed)** — confirms
the fix pattern is inconsistent, not uniformly missing:
- `backend/routes/rides/chat.py:192` — already computes `push_target_app`
  dynamically based on sender/recipient role and passes it through.
- `backend/routes/rides/matching.py:911` (driver offer push) and `:1071`
  (driver auto-offline) — driver-directed, out of N10's rider-specific scope;
  `:1071` is itself missing `target_app="driver"` but that is a distinct
  N10-adjacent driver-side gap, not touched here.
- `backend/routes/rides/lost_found.py:79` — driver-directed, already passes
  `target_app="driver"`.
- `backend/routes/lost_and_found.py` — every call site goes through a local
  `_push_safe(user_id, title, body, data, target_app)` wrapper that requires
  `target_app` as a mandatory positional/keyword argument at every call site.
- `backend/routes/notifications.py:108,232` — both are admin debug endpoints
  (`/test-push`, `/debug-ride-offer`) where the admin supplies an arbitrary
  `user_id`; correctly ambiguous, left alone (`:232` already passes
  `target_app="driver"` by design, mirroring the live dispatch path it's
  simulating).
- `backend/services/cancellation_service.py:206` — driver-directed
  (`driver_user_id`), out of N10's rider-specific scope.
- `backend/utils/live_activity.py:170` — already passes `target_app="rider"`.
- `backend/utils/stale_intent_reconciler.py:224` — driver-directed, already
  passes `target_app="driver"`.
- `backend/utils/marketing_push.py:48` — thin wrapper that forwards a
  caller-supplied `target_app`; correctly generic by design (CASL marketing
  gate, multi-role callers).
- `backend/utils/document_expiry.py:216,290` — both driver-directed
  (document/suspension notices), out of N10's rider-specific scope; `:216`
  is itself missing `target_app="driver"` (uses a bespoke `ACCOUNT_PRIORITY`
  push-opt-out bypass) — flagged, not fixed here.

## 4. Risk & impact on existing functionality

**This change is additive-safe by construction and cannot regress an
already-working push.** `features.send_push_notification`'s token resolution
for `target_app="rider"` reads `fcm_token_rider` **and falls back to the
legacy `fcm_token` column when `fcm_token_rider` is unset** (mirrors the
`target_app="driver"` → `fcm_token_driver` fallback documented in
`routes/notifications.py:174-177`). Concretely:

- If a rider has registered a token via the current `register-token` flow,
  both `fcm_token` and `fcm_token_rider` already hold the same value (the
  mirroring this whole item exists to stop depending on) — the fixed call
  sites resolve to the exact same token column value as before, just via an
  explicit column instead of the legacy fallback. No behavior change for any
  currently-active rider.
- If a rider somehow has only a legacy `fcm_token` (e.g. an old row from
  before the per-app columns existed and never re-registered) — the
  `target_app="rider"` fallback-to-`fcm_token` path still finds it. Still no
  regression.
- The only way this fix could ever *change* behavior is if `fcm_token_rider`
  and `fcm_token` diverge (e.g. a user re-registers on driver app only, so
  `fcm_token` moves to the driver's token but `fcm_token_rider` still holds
  the last real rider token) — in that specific case the fix is a **bug fix**
  (delivering to the correct rider device instead of whatever device most
  recently registered), not a regression.

**Blast radius: isolated to the 10 call sites listed above.** Grepped every
other caller of `send_push_notification` across the repo (see inventory in
§3/§Remaining scope) — none of the other ~20 call sites were touched. No
shared component, hook, or utility signature changed; `send_push_notification`
itself is unmodified. No interaction with the ride state machine, wallet/money
deltas, or any of the 18 background loops beyond the two loops
(`scheduled_dispatch`, `stuck_ride_sweeper`) whose existing rider-notification
calls simply gained one keyword argument each.

## 5. User-experience effect

None visible. This is a defensive/correctness fix to which token column is
read internally — the rider sees the identical push (same title, body,
priority, data payload) they already received, just resolved through the
correct, explicit column instead of an implicit legacy fallback. Not visible
mid-session in any way a user could observe; no copy change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/lifecycle.py` | Added `target_app="rider"` to the "Ride Started" push (1 call site) | Rider-directed push was falling through to legacy `fcm_token` |
| `backend/routes/rides/matching.py` | Added `target_app="rider"` to the no-drivers-found auto-cancel push (1 call site) | Same |
| `backend/utils/scheduled_rides.py` | Added `target_app="rider"` to 5 rider-facing pushes (delay notice ×2, policy-blocked, dispatch-fired, 10-min reminder) | Same |
| `backend/utils/stuck_ride_sweeper.py` | Added `target_app="rider"` to the stuck-ride auto-cancel push (1 call site) | Same |
| `backend/services/payment_service.py` | Added `target_app="rider"` to both "Payment failed" pushes on the fresh-charge (no pre-auth hold) path (2 call sites) | Same |
| `backend/tests/test_coverage_rides.py` | Extended `test_ride_search_timeout_cancels_searching_ride` + added `test_rider_start_ride_push_target_app_is_rider` | Regression coverage for the two `routes/rides` fixes |
| `backend/tests/test_scheduled_rides_coverage.py` | Extended 3 existing tests + added `test_escalated_notification_targets_rider_app` | Regression coverage for 4 of the 5 `scheduled_rides.py` fixes |
| `backend/tests/test_scheduled_dispatch_cr.py` | Extended `test_policy_failure_blocks_dispatch_and_notifies_once` | Regression coverage for the corporate-policy-blocked push fix |
| `backend/tests/test_stuck_ride_sweeper_coverage.py` | Extended `test_single_stuck_ride_releases_hold_notifies_and_frees_driver` | Regression coverage for the stuck-ride sweeper fix |
| `backend/tests/test_settle_card_capture.py` | Added `_fresh_ride` helper + `TestFreshChargeFailurePushTargetApp` (2 new tests) | Regression coverage for both `payment_service.py` fixes |
| `backend/tests/test_p0_ship_blockers.py` | Widened `TestNoDriversAvailableTimeout`'s local `_capture_push` mock signature to accept `priority`/`target_app` kwargs + added a `target_app == "rider"` assertion | Pre-existing test used a fixed-signature stand-in for `send_push_notification`; broke (`TypeError: unexpected keyword argument 'target_app'`) once the `matching.py:1347` fix started passing the new kwarg — this is the exact kind of test-mock brittleness the sweep in step 7 exists to catch |

## 7. Before / after

**`backend/routes/rides/lifecycle.py`** (rider ride-start push):
```python
# Before
_deps.send_push_notification(
    rider_id,
    "Ride Started! ▶️",
    "Your ride has started. Have a safe trip!",
    data={"type": "ride_started", "ride_id": str(ride_id)},
)

# After
_deps.send_push_notification(
    rider_id,
    "Ride Started! ▶️",
    "Your ride has started. Have a safe trip!",
    data={"type": "ride_started", "ride_id": str(ride_id)},
    target_app="rider",
)
```

**`backend/services/payment_service.py`** (payment-failed push, fresh-charge decline branch):
```python
# Before
await send_push_notification(
    rider_id_for_push,
    "Payment failed",
    "Your payment method was declined. Please update your payment method in the app.",
    data={"type": "payment_failed", "ride_id": ride_id, "deeplink": "/wallet"},
)

# After
await send_push_notification(
    rider_id_for_push,
    "Payment failed",
    "Your payment method was declined. Please update your payment method in the app.",
    data={"type": "payment_failed", "ride_id": ride_id, "deeplink": "/wallet"},
    target_app="rider",
)
```

**`backend/utils/stuck_ride_sweeper.py`** (stuck-ride auto-cancel push):
```python
# Before
await send_push_notification(
    rider_id,
    "No drivers available",
    "We couldn't find a driver nearby. Please try again.",
    {"ride_id": str(ride_id), "type": "ride_cancelled"},
)

# After
await send_push_notification(
    rider_id,
    "No drivers available",
    "We couldn't find a driver nearby. Please try again.",
    {"ride_id": str(ride_id), "type": "ride_cancelled"},
    target_app="rider",
)
```

## 8. Rollback plan

`git revert` is sufficient and complete here — this is pure additive
application code (one new keyword argument per call site), no schema change,
no data migration, no config/flag, and nothing applied to live data (Stripe
charges, wallet deltas, ride state, insurance-period rows) that a code revert
could leave inconsistent. Reverting restores the exact prior behavior
(implicit fallback to the legacy `fcm_token` column), which — per §4 — was
already delivering the identical push to the identical token in the common
case, so a revert is a true no-op for any currently-registered device.

## 9. Verification performed

- [x] Automated tests run — unit tests only (mocked Supabase/FCM throughout,
  per repo convention; no integration/e2e run for this batch). Exact
  pass/fail counts from a real `pytest` run reported in the PR description /
  session summary (not reasoned about, not simulated).
- [ ] Manual repro / staging check — not performed (no staging environment
  available to this session).
- [x] Blast-radius grep performed — every `send_push_notification(` call site
  repo-wide was enumerated and classified (§3); the two files owned by
  parallel N5/N8 sessions were explicitly excluded and not touched.
- [x] Reviewed against relevant CLAUDE.md conventions — "Driver online/available
  flags" N/A here; no ride-state-machine transition changed; no money
  arithmetic touched (the two `payment_service.py` sites are inside the
  *failure* branches, after `payment_status` is already set to `"failed"` —
  no charge amount or Decimal path touched).
- [ ] Feature-flagged — not applicable; this is a non-user-visible internal
  correctness fix (§5), not new user-visible behavior, so no flag per
  CLAUDE.md's "Feature-flag anything user-visible and non-trivial" gate.

## 10. What was NOT verified

- No real FCM/Expo push was actually sent in this session — all pushes are
  mocked in the unit tests listed above; delivery to a live device against
  real `fcm_token_rider`/`fcm_token` values was not exercised.
- No production or staging Supabase data was inspected to confirm how many
  live rider rows currently have `fcm_token_rider` populated vs. relying on
  the legacy-column fallback — the "no regression" argument in §4 is a
  code-level guarantee (the fallback chain in `features.py`), not a verified
  count of affected rows.
- The remaining ~19 call sites listed in §Remaining scope below were
  inventoried and classified but not fixed, verified, or tested in this
  session.
- No visual/snapshot regression tooling exists for backend push payloads (not
  applicable here — no UI surface changed), so this is a non-issue for this
  particular change, noted per the standing gap in `ACTION_ITEMS.md` for
  completeness.
- `npm run build` / production frontend build: not applicable — this batch
  touched only `backend/`, no `admin-dashboard`/`rider-app`/`driver-app` code.

## Remaining scope (not fixed in this batch)

Full inventory from the ~30-call-site, ~17-file finding, for a follow-up
session to pick up directly without re-deriving it. `backend/routes/rides/
cancellation.py` and `backend/utils/receipt_email.py` were excluded entirely
per this session's instructions (owned by parallel N5/N8 sessions) and were
not inventoried here.

**Clearly rider-directed, missing `target_app="rider"` — ready to fix, same
pattern as this batch:**
- `backend/routes/disputes.py:98` — "Dispute received" push on
  `create_dispute`; `rider_id = ride.get("rider_id") or current_user["id"]`,
  and the route itself requires `ride.get("rider_id") == current_user["id"]`
  to reach this point, so unambiguous.
- `backend/routes/disputes.py:308` — "Dispute update" push on
  `resolve_dispute`; `rider_id = dispute.get("user_id")`, and `user_id` is
  set from `current_user["id"]` at dispute-creation time under the same
  rider-only guard as above.
- `backend/services/guest_notification_service.py:167` —
  `notify_guest_booking_created`'s in-app-surfacing branch (customer has the
  app installed); `guest_user["id"]` is the corporate guest rider being
  booked for. Unambiguous but not "ride-lifecycle core" in the same sense
  as this batch, so deprioritized behind it.

**Driver-directed pushes that are also missing `target_app="driver"` (a
related but distinct N10-adjacent gap — NOT rider-scope, flagged for
awareness only, not part of N10's remaining-rider-scope count):**
- `backend/routes/rides/matching.py:1071` — driver auto-offline
  ("missed_offers") push.
- `backend/services/cancellation_service.py:206` — driver cancellation-fee-earned push.
- `backend/utils/document_expiry.py:216` — driver document-expired-suspension push.

**Ambiguous / admin / multi-role — require their own careful read before any
fix, do not blindly annotate:**
- `backend/routes/notifications.py:108` (`/test-push`) — admin-supplied
  arbitrary `user_id`; role unknowable without a DB lookup the endpoint
  doesn't currently do.
- `backend/routes/admin/*.py` (not enumerated line-by-line here — out of the
  17-file list this session was scoped to) — admin broadcast/notification
  endpoints where the recipient can be rider, driver, or both depending on
  the admin's selection; each needs its own per-call-site read.

This batch fixed 10 of the ~30 originally-estimated call sites (across the
5 files listed in §6); **the three unambiguous rider-directed sites above
(disputes.py ×2, guest_notification_service.py ×1) are the concrete starting
point for batch 2.**
