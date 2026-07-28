# Trip / Ride State Machine (as extracted from code)

Read-only recon, 2026-07-25. Extracted from `backend/models/ride_status.py`,
`backend/routes/rides/*.py`, `backend/routes/drivers/*.py`,
`backend/services/dispatch_service.py`, `backend/utils/scheduled_rides.py`,
`backend/utils/offer_expiry_reaper.py`, `backend/utils/stuck_ride_sweeper.py`,
and `backend/features.py`. This is the actual set of states/transitions found
by static reading + grep, not just the documented diagram in CLAUDE.md -
differences from CLAUDE.md's diagram are called out explicitly below.

## States found in code

`backend/models/ride_status.py` (`RideStatus(str, Enum)`) defines the
canonical set, matching CLAUDE.md:

- `scheduled`
- `searching`
- `driver_assigned`
- `driver_accepted`
- `driver_arrived`
- `in_progress`
- `completed`
- `cancelled`

`active_statuses()` / `terminal_statuses()` classmethods exist on the enum.

## Mermaid state diagram

```mermaid
stateDiagram-v2
    [*] --> scheduled : booking.py (scheduled ride created)
    [*] --> searching : booking.py (immediate ride created)

    scheduled --> searching : utils/scheduled_rides.py background loop (dispatch time reached)
    scheduled --> cancelled : routes/rides/cancellation.py (rider/admin cancel before dispatch)

    searching --> driver_assigned : services/dispatch_service.py:448 assign_driver_to_ride (STRING LITERAL "driver_assigned", not RideStatus enum - see findings.md #2)
    searching --> cancelled : routes/rides/cancellation.py (rider/admin cancel, pre-match)
    searching --> cancelled : utils/stuck_ride_sweeper.py (auto-cancel, no drivers found after ~5 min; raw Supabase .eq("status","searching") claim - see findings.md #1.5)

    driver_assigned --> searching : routes/rides/matching.py process_expired_offer (offer timeout ~15s, releases driver) [ALSO re-invoked by utils/offer_expiry_reaper.py as a durable backstop - see "duplicate-checked transitions" below]
    driver_assigned --> driver_accepted : routes/drivers/ride_flow.py (driver accepts offer)
    driver_assigned --> cancelled : routes/rides/cancellation.py / routes/drivers/ride_cancel.py (pre-trip cancel)

    driver_accepted --> driver_arrived : routes/drivers/ride_flow.py (driver marks arrived)
    driver_accepted --> cancelled : routes/rides/cancellation.py / routes/drivers/ride_cancel.py

    driver_arrived --> in_progress : routes/rides/lifecycle.py rider_start_ride (line ~62) AND/OR routes/drivers/ride_flow.py driver-side start (dual trigger path - see notes)
    driver_arrived --> cancelled : routes/rides/cancellation.py / routes/drivers/ride_cancel.py (no-show handling)

    in_progress --> completed : routes/rides/lifecycle.py rider_complete_ride (line ~126) AND routes/drivers/ride_complete.py (driver-side complete, line ~621) [two independent write paths to the same transition - see notes]

    completed --> [*]
    cancelled --> [*]

    note right of cancelled
      cancellation.py also writes ride status
      back to "scheduled" in the no-show /
      cancel-and-requeue path for scheduled
      rides (lines ~483/491) - a re-entrant
      edge not present in CLAUDE.md's diagram.
    end note
```

## Divergences from the CLAUDE.md-documented diagram

1. **`scheduled` -> `cancelled` -> `scheduled` (requeue) edge exists in code but not in the CLAUDE.md diagram.** `backend/routes/rides/cancellation.py:483,491` writes both `CANCELLED` and back to `SCHEDULED` in the same code path (scheduled-ride cancel-and-requeue). CLAUDE.md's diagram shows `cancelled` as a pure terminal state reachable only pre-`in_progress`; this requeue edge means `cancelled` is not always terminal in practice for scheduled rides. Needs a design-doc clarification or a fix if this is unintended (e.g. writing `SCHEDULED` should probably never pass through `CANCELLED` as an intermediate write - flagged as a state-machine documentation gap, not confirmed as a functional bug).

2. **`driver_assigned` is written as a raw string literal, not the enum**, at `backend/services/dispatch_service.py:448`. Every other transition sampled in this pass uses `RideStatus.<MEMBER>` or `RideStatus.<MEMBER>.value`. See findings.md #2.

3. **`backend/features.py` writes `IN_PROGRESS`/`SCHEDULED`/`CANCELLED`/`SEARCHING` at lines 461, 1174, 1205, 1877, 1893**, independent of the `routes/rides/` package's transitions above. Not confirmed whether these are dead/legacy call sites or still live; if live, they constitute additional, uncatalogued edges into the same states. See findings.md #5.

## Transitions implemented/checked in more than one place

These are the state-guard duplications also flagged in findings.md #1, restated here specifically as they relate to the state machine:

| Transition | Independent implementations found |
|---|---|
| `searching -> driver_assigned` (and the reverse, timeout) | `services/dispatch_service.py` (assign) + `routes/rides/matching.py:process_expired_offer` (revert on timeout) + `utils/offer_expiry_reaper.py` (durable backstop re-invoking the same `process_expired_offer` - a deliberate second caller of the same function, per its own docstring, rather than a second independent implementation - lower risk than the others in this table) |
| `driver_arrived -> in_progress` | `routes/rides/lifecycle.py:rider_start_ride` (rider-triggered, inline status check + atomic update, does NOT call `_require_ride_in_state_rider`) - need to confirm whether a driver-side equivalent trigger exists in `routes/drivers/ride_flow.py`; if both rider and driver can independently trigger the same transition, that is a second implementation of the same edge and a potential double-fire race not fully traced in this pass |
| `in_progress -> completed` | `routes/rides/lifecycle.py:rider_complete_ride` (rider-side, inline guard) + `routes/drivers/ride_complete.py:621` (driver-side, a separate inline filter-based atomic guard). Two independently written code paths converge on the same transition - if both rider and driver apps call their respective endpoints near-simultaneously (e.g. rider taps "done" while driver's app also fires an auto-complete), the atomic `.eq("status","in_progress")` filter on each individually prevents a double-write, but the *guard logic itself* (validation, side effects like receipt generation, notification fan-out) is not shared code, so any bug fixed in one path is not automatically fixed in the other. |
| `searching -> cancelled` (auto, no-driver timeout) | `utils/stuck_ride_sweeper.py` (raw Supabase client, `.eq("status","searching").lt(...)`) - the only one of the four/five guard implementations that bypasses `db_supabase.py` entirely; also the only automatic-cancellation path for a ride stuck in `searching`, separate from the rider/admin-initiated cancellation in `routes/rides/cancellation.py`. |
| Rider-side vs driver-side "require ride in state X" guard (used across several transitions above) | `routes/rides/_shared.py:290 _require_ride_in_state_rider` vs `routes/drivers/_shared.py:558 _require_ride_in_state` - same intent, different exception type, both hand-maintained in parallel. |

## Notes / follow-ups not completed in this pass

- `backend/routes/admin/rides.py` (3430 lines) was only grep-sampled for status literals, not read for its own state-transition logic; admin override transitions are not represented in the diagram above and should be added in a follow-up pass.
- Whether `routes/drivers/ride_flow.py` has an independent driver-triggered `driver_arrived -> in_progress` path (paralleling the rider-triggered one in `lifecycle.py`) was not fully confirmed - flagged in the table above as needing verification, since CLAUDE.md's Insurance Periods table treats `in_progress` entry as the Period 2 -> Period 3 boundary and a duplicate/racing trigger here has regulatory-audit relevance (`driver_insurance_periods` append-only log).
- `backend/ai/tools_driver.py` and `backend/routes/quests.py` were grep-hit for ride-status string literals but not read this pass; unclear whether they read-only or also write status.
