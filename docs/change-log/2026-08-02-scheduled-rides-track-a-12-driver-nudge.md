# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch, drivers |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Scheduled Rides gap review — Finding #06 |

## 1. Issue / gap identified

Drivers get zero advance notice of upcoming scheduled demand — the only
driver-facing "scheduled" surface was a history filter on past trips.
Spinr already knows, up to an hour ahead, where a scheduled pickup will
happen; that knowledge was unused.

## 2. Root cause

The scheduled dispatcher only ever acted at two points: a 10-minute rider
reminder and the dispatch moment itself. Nothing between booking and
dispatch touched the driver side at all.

## 3. Fix / remediation

New `_maybe_nudge_nearby_drivers()` in `backend/utils/scheduled_rides.py`,
called from `check_scheduled_rides()` for any scheduled ride within
`_DRIVER_NUDGE_LEAD_MINUTES` (60) of pickup:
- Deduped once per ride via Redis NX (6h TTL, comfortably past the 1h window).
- Queries the `drivers` table for `is_online AND is_available` within a
  `_DRIVER_NUDGE_RADIUS_KM` (10km) bounding box around the pickup, reusing
  `dispatch_geo_bounds()` — the same helper the real dispatch candidate
  query uses — rather than the `find_nearby_drivers` PostGIS RPC, which a
  code comment in `matching.py` documents as effectively dead (it reads a
  `location` column that driver location updates never populate, so it
  always returns zero rows).
- Sends one push per matched driver (capped at
  `_DRIVER_NUDGE_MAX_RECIPIENTS` = 20), generic copy with no exact address
  (PIPEDA — never disclose exact pickup location to a driver who hasn't
  been offered the ride) and no promise of getting the ride (dispatch at
  go-time is unchanged, still the normal matching pool).
- **Flag-gated, defaulted OFF**: `AppSettings.scheduled_ride_driver_nudge_enabled = False`.
  Unlike the Finding #07 kill switch (which pauses *existing* always-on
  behavior and fails open on a settings-lookup error), this gates a
  genuinely *new* driver-facing notification type and fails **closed**
  (skips) on a settings-lookup error — an unreviewed new feature should
  not go live because a lookup hiccuped.
- Every failure (settings lookup, candidate query, individual push) is
  caught and logged; one driver's push failure never blocks the others.

## 4. Risk & impact on existing functionality

- **Blast radius: additive, isolated, and off by default.** With the flag
  at its default (`False`), this entire code path is a no-op — every new
  branch returns before any query or push. Grepped for other readers of
  `scheduled_ride_driver_nudge_enabled` — none; this is the only call site.
- Reuses `dispatch_geo_bounds()` from `services/dispatch_service.py`
  read-only (a pure function, no shared mutable state) — no risk to the
  real dispatch candidate query it also serves.
- The `drivers` table query here is a **separate, lighter** query than the
  real dispatch candidate search (no `vehicle_type_id`, `is_verified`,
  `status='active'`, WAV, or presence filtering) — deliberately so: a
  heads-up nudge isn't vehicle-type-specific, and a driver who isn't
  currently dispatchable for some other reason can still usefully see
  "demand is coming" and choose to fix whatever's blocking them. This is
  NOT a substitute for the real dispatch filter and must never be used to
  actually assign a ride.
- No interaction with money, the ride state machine, or corporate billing.

## 5. User-experience effect

**Driver-facing, and currently invisible** — ships dark. Once enabled by an
admin: an online, available driver within 10km of an upcoming scheduled
pickup (~60 minutes out) receives one push ("Scheduled ride coming up
nearby... Stay online for first chance at it") per qualifying ride. No
rider or corporate-admin-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/schemas.py` | New `scheduled_ride_driver_nudge_enabled: bool = False` | Flag gate for the new notification type |
| `backend/utils/scheduled_rides.py` | New `_maybe_nudge_nearby_drivers()`; wired into `check_scheduled_rides()`'s per-ride loop; added `pickup_lat`/`pickup_lng` to the candidate query's `columns` | Implement the nudge, feeding it the location it needs |
| `backend/tests/test_scheduled_dispatch_cr.py` | New `TestDriverNudge`: disabled-by-default, happy path, dedupe, missing-location no-op, one-recipient-failure-doesn't-block-others | Cover the flag gate and the fan-out-with-partial-failure behavior specifically |

## 7. Before / after

```python
# Before
# (no driver-facing signal existed between booking and dispatch)
```

```python
# After
if now <= scheduled_time and scheduled_time <= nudge_window_end:
    await _maybe_nudge_nearby_drivers(ride)  # no-op unless scheduled_ride_driver_nudge_enabled
```

## 8. Rollback plan

Flip `scheduled_ride_driver_nudge_enabled` back to `false` — effective
within the 60s settings-cache TTL, no redeploy, no data to unwind (this
feature never writes to the `rides` or `drivers` tables, only reads and
sends pushes). If a full code rollback is ever needed instead, plain
`git revert` — no migration involved.

## 9. Verification performed

- [x] Automated tests: `backend/tests/test_scheduled_dispatch_cr.py`, full
      file, 25 passed (20 prior + 5 new) via the session's venv.
- [x] `ruff check` on both modified backend files — clean.
- [ ] Manual repro in staging — not performed, no staging access. This is
      the one item in this session's batch I'd flag most strongly for a
      staging smoke test before flipping the flag on — notification
      fatigue and push-volume-at-scale are real UX risks a unit test
      cannot catch.
- [x] Blast-radius grep performed (see §4).
- [x] Reviewed against CLAUDE.md's PIPEDA logging rules — no raw GPS
      coordinates are logged anywhere in the new code (only used as query
      parameters); no exact pickup address is included in the push copy.
- [x] Feature-flagged — yes, defaulted off, per CLAUDE.md's "ship dark,
      verify, then flip on" convention for new user-visible, non-trivial
      behavior.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — off by default, additive,
      no shared-state risk
- [x] No silent behavior change to any already-shipped flow — this is a
      wholly new, currently-dark feature; nothing existing changes until
      an admin explicitly opts in

## What was NOT verified

Not tested against real push-delivery volume or a real driver population
density — the `_DRIVER_NUDGE_RADIUS_KM` (10) and
`_DRIVER_NUDGE_MAX_RECIPIENTS` (20) values are reasoned defaults, not
tuned against production data. Before enabling the flag, I'd recommend a
staging check with realistic driver density to confirm the radius/cap
combination doesn't either under-reach (too few candidates in a sparse
area) or spam a dense downtown core. No admin-dashboard UI toggle was
built for this flag either, following the same precedent noted in the
Finding #07 kill-switch change log.
