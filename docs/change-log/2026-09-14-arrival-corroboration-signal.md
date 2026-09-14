# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (agent), for ittalenthire.ca@gmail.com |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | #1231 finding 12 (soft-rollout signal only — see scope note below) |

## 1. Issue / gap identified

`arrive_at_pickup` (`backend/routes/drivers/ride_flow.py`) accepts a driver's
arrival based solely on the live `drivers.lat/lng` marker, which is
mobile-submitted. A moderately sophisticated GPS spoof (smooth, physically
plausible fake path, normal reported speed/accuracy, no mock flag) can move
that marker to a fake near-pickup point and trigger a `driver_arrived`
transition — and the cancellation-fee / no-show windows that follow it —
without the driver actually being there.

## 2. Root cause

The geofence check trusts a single mutable value (`drivers.lat/lng`) with no
independent corroboration. Two anti-spoofing layers already exist upstream
of that value (`utils/location_integrity.py`'s `check_location_integrity`/
`evaluate_gps_plausibility` — mock flag, self-reported-speed sanity, and
position-derived teleport/sustained-speed checks) and both the v1
(`routes/drivers/location.py`'s legacy REST path) and v2
(`_apply_v2_live_marker_update`) location-write paths already gate the
marker write through them — confirmed by reading both paths in full as part
of this change (see section 4). Those checks reject an *obviously* fake
point (unset mock flag + physically-impossible speed/teleport). They cannot
reject a fake point that is internally consistent — a modified client can
fabricate a slow, steady walk toward pickup with sane speed/accuracy values
and no mock flag, and nothing upstream can distinguish that from a real one.

## 3. Fix / remediation

Approved scope: **soft-rollout signal only** — log + metric, never a block,
never a UX change. The hard-block and rider-side-confirmation variants
proposed in issue #1231 were explicitly declined for a later phase.

After `arrive_at_pickup`'s existing geofence check passes and the
`driver_accepted -> driver_arrived` transition has already committed, a
fire-and-forget background task (`spawn()`, same idiom the function already
uses for its push notification) checks whether the driver has at least one
server-side breadcrumb from the last 5 minutes, for this `ride_id`, within
the same 200 m arrival radius. Every row in `driver_location_history` was
already run through `evaluate_gps_plausibility`/`check_location_integrity`
at write time — a point that fails that check is logged and never inserted
(confirmed by reading `utils/breadcrumbs.py` in full) — so a hit means an
independently-verified fix corroborates the live marker, not just that one
value. A miss does not raise, does not touch `rides.status`, and cannot
delay the HTTP response (the check is spawned, not awaited); it only logs a
`logger.warning` (ids only, no raw lat/lng) and increments a new counter,
`spinr_dispatch_arrival_uncorroborated_total`.

**Alternative considered:** hard-block the transition when uncorroborated
(reject with 4xx). Rejected for now — the real-world false-positive rate
from ordinary GPS noise near pickup (parking garages, downtown canyons,
short driver_accepted -> driver_arrived legs with too few breadcrumbs to
have flushed yet) is unmeasured. Shipping a block first risks rejecting
genuine arrivals and is exactly the kind of live-tested-surface regression
CLAUDE.md's release gates ask to avoid; this change exists to gather that
false-positive rate before any future hard-block or rider-side-confirmation
variant is considered.

**Bonus investigation (explicitly requested by the task):** checked whether
the *older*, non-v2 REST location-write path
(`routes/drivers/location.py`'s legacy branch of `update_location_batch`,
~lines 715-861) gates its own live-marker write the same way the v2 path
does. **It already does** — `check_location_integrity(...)` is called and
checked (`if not trusted: return {"success": False, ...}`) *before* the
`_write_marker_if_due(...)` call that would move `drivers.lat/lng`. No
gating gap was found there; this is a negative result, not a fix, and no
code in that path was changed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to a new read.** This diff adds a `SELECT` against
  `driver_location_history`; it writes nothing. Grepped every non-test
  reader/writer of that table:
  - **Writers:** `routes/drivers/location.py` / `routes/websocket.py` (live
    ingestion) via `utils/breadcrumbs.py` (`persist_trip_location_batch`,
    `persist_idle_location_batch`, `persist_ride_breadcrumbs`) and
    `utils/breadcrumb_buffer.py` (client-side flush buffering). None of
    these are touched by this diff, and a plain `SELECT` cannot block or
    conflict with their `INSERT`s (Postgres MVCC).
  - **Other readers:** `routes/drivers/ride_complete.py`,
    `utils/route_finalizer.py`, `utils/trip_distance.py`,
    `utils/route_distance.py` (trip settlement / distance calc),
    `utils/driver_daily_rollup.py` (daily rollup), `utils/retention_purge.py`
    (90-day/3-year retention deletes), `routes/admin/drivers.py`,
    `routes/admin/maintenance.py` (admin inspection),
    `scripts/analyze_ride_route.py` (offline tool). None of these are
    modified, and an additional narrow, indexed read does not meaningfully
    add load — no new table, index, or migration was added, per the task's
    explicit constraint. **Correction (round 2 review):** this originally
    cited migration 239's `uq_dlh_ride_driver_session_sequence` unique index
    as the covering index; the actual better match is
    `idx_driver_location_history_ride_id_timestamp` (migration 369), which is
    `(ride_id, timestamp)` — exactly the leading equality column (`ride_id`)
    and order column (`timestamp`) this query uses. See the column-choice
    note directly below for why the query orders on `timestamp` and not
    `captured_at`, which this index does **not** cover — so this citation is
    now accurate for the query as actually written, not a claim of coverage
    for a column the query doesn't order by.
  - **Column choice, corrected after round-2 review:** the query was
    originally written to filter/order on `timestamp` alone. A fraud-auditor
    review flagged `timestamp` as a field the codebase documents elsewhere as
    being phased out in favour of `captured_at` (citing migration 371's
    `route_gap_latest_captures`, and a real incident — ride SPR-PE7TTB — from
    ordering on the wrong one of the two). Investigated before applying that
    fix literally: `captured_at` is **v2-only** by design (migration 235's
    own column comment: "Immutable device capture time for v2 trip
    breadcrumbs") — `utils/breadcrumbs.py`'s `persist_ride_breadcrumbs()`,
    the legacy REST/WS-single-ping path and still the dominant one, never
    populates it, only `timestamp`. Filtering on `captured_at` alone would
    have silently excluded every breadcrumb from that dominant path (`NULL`
    fails a `>=` comparison) and ordering `DESC` on it is doubly unsafe —
    Postgres sorts `NULL`s first under `DESC`, so legacy rows would crowd out
    real ones ahead of the `LIMIT`, the exact hazard migration 371's own
    comment documents from the SPR-PE7TTB incident. **Fix actually applied:**
    match on `captured_at` OR `timestamp` (whichever a row has — mirrors
    migration 371's own `captured`/`legacy` precedence), ordered on the
    always-populated `timestamp`. Correct for both writer generations today,
    and the `captured_at` OR-branch means this keeps working unmodified if
    `timestamp` is ever retired in favour of it. See the code comment on
    `_flag_uncorroborated_arrival_if_needed` for the full reasoning.
  - **State machine / money / insurance periods: untouched.** No write to
    `rides.status`, no WebSocket event added or removed, no wallet/Stripe
    call, no `driver_insurance_periods` write. The existing geofence 400 and
    the `driver_accepted -> driver_arrived` transition guard are byte-for-byte
    unchanged.
- **Known sources of noise in the new signal itself (not a functional risk,
  but worth stating so the metric isn't over-read once live):**
  - A very short `driver_accepted -> driver_arrived` leg (driver already
    at/near pickup on acceptance) may not yet have a flushed breadcrumb in
    the 5-minute window, producing a false "uncorroborated" flag with no
    spoofing involved. This is exactly the false-positive-rate question this
    soft rollout exists to measure — see section 3.
  - **`utils/breadcrumb_buffer.py`'s WS breadcrumb buffer** only flushes on
    10 points / 10s-since-last-point / disconnect / ride completion — no
    independent timer bound. If a driver's pings pause briefly right before
    the "Arrived" tap (backgrounded, parking-garage signal loss), the
    freshest points can sit unflushed in that buffer. Low severity given the
    5-minute window (an earlier, already-flushed point should usually still
    corroborate), but it's a second, distinct source of the same
    spurious-uncorroborated-flag noise as the short-leg case above.
  - **Independence caveat — read this before trusting the metric as general
    proof of a separate signal:** for the *dominant* WS single-ping
    ingestion path, the live `drivers.lat/lng` marker and the breadcrumb row
    this check queries are derived from the **same incoming client message**,
    gated by the **same** plausibility check (`check_location_integrity`
    inside the WS handler) — so for that path, a "hit" here re-checks the
    same fact via a second row, not independent corroboration. It genuinely
    is independent for the REST v2 acknowledged-outbox trip-batch path
    (`persist_trip_location_batch`'s finding-11 chained per-point checks,
    seeded from the driver's last-known DB position), and this check would
    have caught a live-marker-only write bug if one existed — the bonus
    investigation in section 3 confirmed none does today. State this
    precisely when reading the eventual false-positive-rate data: a low
    uncorroborated rate on WS-dominant traffic partly reflects "the marker
    and the breadcrumb agree because they're the same write," not purely
    "an independent check passed."
- **Failure mode of the new read itself:** wrapped in try/except; a DB error
  is logged via `logger.error(..., exc_info=True)` (per CLAUDE.md's DB-error
  rule) and swallowed at that point only — it cannot propagate into the
  request (already returned) or affect the ride.

## 5. User-experience effect

**None.** This is server-side telemetry only — no response shape change, no
new error code, no copy/notification change, nothing observable by the
rider or driver, mid-session or otherwise. Per CLAUDE.md's flag rule
("Feature-flag anything user-visible and non-trivial"), no feature flag was
added: there is no user-visible behavior to flag, gate, or roll back from
the rider/driver's perspective — the response, status code, and timing of
`arrive_at_pickup` are identical whether or not the check finds
corroboration.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/ride_flow.py` | Added `_flag_uncorroborated_arrival_if_needed()` (module-level helper) + a `spawn()` call site inside `arrive_at_pickup`, right after the existing `driver_arrived` state-transition metric. Added `timedelta` to the `_deps` import list (already available there, just not previously imported into this module). Round 2: query filter changed from `{"timestamp": {"$gte": window_start}}` to `{"$or": [{"captured_at": {"$gte": window_start}}, {"timestamp": {"$gte": window_start}}]}` (see section 4's column-choice note); ordering stays on `timestamp`. | #1231 finding 12 soft-rollout signal. |
| `backend/tests/test_driver_ride_flow_coverage.py` | Added `TestArriveAtPickupCorroborationSignal` (3 tests: corroborated -> no signal, uncorroborated -> metric fires but arrival still succeeds, breadcrumb-read failure is swallowed not raised). Also removed one pre-existing, unrelated dead local variable (`arrived_dt` in `TestMarkRiderNoshowSuccess._base_patches`, assigned but never read) — this repo's pre-commit hook runs `ruff check` on the whole staged file and blocked the commit on it; confirmed via `git show` that it predates this change and is unrelated to `mark_rider_noshow`/`arrive_at_pickup` logic. Round 2: added `_assert_scoped_corroboration_query()` and wired it into the corroborated/uncorroborated tests — asserts the actual `driver_id`/`ride_id` scoping, the `$or(captured_at, timestamp)` shape, and `order`/`desc`/`limit`, not just the return value. | Required test coverage for the new non-blocking branch; the dead-variable removal was required to get past the pre-commit lint gate, not a deliberate cleanup pass. |
| `docs/change-log/2026-09-14-arrival-corroboration-signal.md` | This entry. | CLAUDE.md mandatory Change Impact Log for a live-tested-surface change. |

## 7. Before / after

Purely additive — no existing branch's behavior changed. Shown for
traceability, not because prior behavior changed:

```python
# Before (backend/routes/drivers/ride_flow.py, end of arrive_at_pickup's
# success path)
_metric_inc("spinr_rides_state_transition_total", {"to_status": "driver_arrived"})

if ride.get("rider_id"):
    ...
```

```python
# After
_metric_inc("spinr_rides_state_transition_total", {"to_status": "driver_arrived"})

if targets:
    # #1231 finding 12 (soft-rollout signal only): fire-and-forget, must
    # not add latency to a response the rider is actively waiting on.
    spawn(_flag_uncorroborated_arrival_if_needed(driver["id"], ride_id, targets, ARRIVAL_RADIUS_KM))

if ride.get("rider_id"):
    ...
```

## 8. Rollback plan

No feature flag exists because there is nothing user-visible to gate (see
section 5), so rollback is a plain code revert of these two files — safe
because the change is purely additive (new function + one new fire-and-forget
call site) and touches no live data (no migration, no `rides`/wallet/Stripe
write). If the new metric/log volume itself becomes a nuisance before a
revert can ship, the spawned check is a single `if targets:` block that can
be commented out or the metric name can be filtered at the dashboard/alert
level with zero code change.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_driver_ride_flow_coverage.py` (109 passed, includes the 3 new tests), plus a broader regression sweep of every test file that exercises `arrive_at_pickup`/`drivers/ride_flow.py`/location-batch ingestion (`test_accept_ride_document_expiry.py`, `test_accept_ride_service_area_gate.py`, `test_active_ride_rider_pii.py`, `test_admin_rides_cancel_state.py`, `test_c2_driver_cancel_atomic.py`, `test_coverage_rides.py`, `test_dispatch_metrics.py`, `test_drivers.py`, `test_drivers_extended.py`, `test_e2e_cancellation.py`, `test_e2e_ride_lifecycle.py`, `test_idle_location_batch.py`, `test_location_batch.py`, `test_location_batch_revoked_session.py`, `test_location_write_gate.py`, `test_loguru_call_conventions.py`, `test_offer_expiry_accept_race.py`, `test_period1_accumulation_endpoint.py`, `test_pickup_otp_bruteforce_lockout.py`, `test_ride_accept_flow.py`, `test_ride_complete_coverage.py`, `test_ride_completion_location.py`, `test_ride_state_machine.py`, `test_ride_state_transition_metrics.py`, `test_rides.py`) — 717 passed, 0 failed, 0 regressions.
- [x] `ruff check` and `ruff format --check` clean on both modified files (one pre-existing, unrelated `F841` in `test_driver_ride_flow_coverage.py`'s `TestMarkRiderNoshowSuccess` class, confirmed present at `HEAD` before this change — not touched, not introduced here).
- [x] `test_loguru_call_conventions.py` passes — `ride_flow.py` uses stdlib `logging` (confirmed by reading `_deps.py`), so the new `logger.warning`/`logger.error` calls use plain positional `%s` args, no `extra=`/`exc_info=` loguru pitfalls apply.
- [x] Manual repro via `mock_supabase_client`-style fakes (the 3 new tests): corroborated breadcrumb -> no signal; zero breadcrumbs -> arrival still returns `{"success": True}` **and** the metric fires; a breadcrumb-read exception is swallowed and does not propagate.
- [x] Blast-radius grep performed — see section 4.
- [x] Reviewed against relevant CLAUDE.md conventions: observability (log/metric/Sentry table — this is "degraded-but-recovered", warning + metric, never Sentry), PIPEDA (no raw lat/lng logged, ids only), dual-import pattern (not needed — reuses the module's existing `_deps`-sourced imports), ride state machine (no new transition; existing `_require_ride_in_state`-equivalent guard on `arrive_at_pickup` untouched).
- [x] Adversarial review: this session could not invoke the `spinr-fraud-auditor` / `spinr-dispatch-reviewer` subagents directly (no Agent/Task tool exposed here) and manually applied both checklists instead — see that self-review's reasoning preserved below for the record. **The two agents were subsequently run independently (by the coordinating session) against this diff — see section 11 for their actual findings and the fixes applied in response.** The self-review's conclusions (no blockers under either checklist) held up against the real runs modulo the two real, fixable gaps in section 11 — this is noted so the earlier "no blockers" self-assessment isn't read as the final word.
  - *(preserved for the record)* Fraud-auditor surface 4 (GPS plausibility) doesn't apply as a writer-side blocker since this diff only reads an already-integrity-gated table; its own documented residual gap ("doesn't stop a single static fake point with no teleport") is exactly the gap this signal exists to measure, not something this diff was expected to close. Dispatch-reviewer rules 1-3 (state machine / race guard / WS events) and 7 (background-task replay-safety) don't apply — no state write, no WS change, and the new task is a per-request fire-and-forget `spawn()` (same idiom as the function's existing push-notification call), not a `lifespan.py`-registered loop, and is read-only so a duplicate run has no side effect beyond a possible duplicate log line/metric increment.
- [ ] Feature-flagged: not applicable — no user-visible behavior exists to flag (see section 5).

## 10. What was NOT verified

- Not tested against a real Supabase instance or real GPS hardware/fake-GPS
  tooling — only `mock_supabase_client`-style fakes. The real-world
  false-positive rate this signal exists to measure (GPS noise, short
  navigation legs with unflushed breadcrumbs) is by definition not knowable
  from unit tests; it can only come from watching
  `spinr_dispatch_arrival_uncorroborated_total` against real traffic.
- No load/latency test of the spawned query under production-scale
  concurrent arrivals — reasoned about (single indexed-equality-filtered
  `SELECT`, bounded by `LIMIT 200`, fire-and-forget) rather than measured.
- No admin-dashboard/rider-app/driver-app change exists in this diff, so the
  visual-regression-tooling disclosure and `npm run build` question in
  CLAUDE.md's template don't apply — backend-only, `pytest`-verified.
- Update: the gap noted above is closed — see section 11. Both agents were
  run independently against this diff before merge.

## 11. Round 2: independent adversarial review results and fixes

`spinr-dispatch-reviewer` and `spinr-fraud-auditor` were run independently
(by the coordinating session, which has Agent-tool access this one lacks)
against the diff described above. Results:

- **`spinr-dispatch-reviewer`: clean, SAFE TO MERGE**, only trivial nits
  (the index-citation nit is folded into the fix below since it's the same
  underlying column-choice question the fraud-auditor also raised).
- **`spinr-fraud-auditor`: two real, fixable gaps**, not blockers on the
  state machine or response path, but real enough to fix before the metric
  is trusted as signal:

  1. **Test gap.** The 3 new tests' `fake_get_rows` branched only on
     `table`, ignoring `filters`/`order`/`desc`/`limit` — none of the 3
     asserted what was actually passed to `get_rows`, so the suite gave zero
     protection against a future silent break (wrong column, dropped scope,
     inverted comparison) while still showing "3 passed". **Fixed:** the
     `driver_location_history` call in the corroborated and uncorroborated
     tests is now captured and asserted via a new
     `_assert_scoped_corroboration_query()` helper — confirms `driver_id`/
     `ride_id` scoping, the `$or(captured_at, timestamp)` shape (see finding
     2), a `~5`-minute `$gte` bound, and `order="timestamp", desc=True,
     limit=200`.
  2. **Column choice.** The query filtered/ordered on
     `driver_location_history.timestamp`, a column the codebase documents
     elsewhere as being phased out in favour of `captured_at` (citing
     migration 371 and a real past incident, ride SPR-PE7TTB, from ordering
     on the wrong one of the two). **Not fixed exactly as literally
     instructed** ("filter/order on `captured_at` instead ... same value
     today") — that instruction turned out to be factually incorrect for
     this table once checked against the actual writer code: `captured_at`
     is **v2-only** (migration 235's own column comment), and
     `persist_ride_breadcrumbs()` — the legacy/WS-single-ping path, still
     the *dominant* one per this same review's own disclosure-3 finding
     below — never populates it, only `timestamp`. Filtering on `captured_at`
     alone would have silently excluded every breadcrumb from the dominant
     path (`NULL` fails `>=`) and made the signal read "uncorroborated" for
     most real traffic — a worse regression than the one being fixed, and
     ordering `DESC` on it is separately unsafe (Postgres sorts `NULL`s
     first under `DESC`, which is the exact SPR-PE7TTB hazard migration
     371's own comment documents). **Fix actually applied:** match on
     `captured_at` OR `timestamp` (mirrors migration 371's own
     `captured`/`legacy` fallback precedence exactly), keep ordering on the
     always-populated `timestamp`. This satisfies the underlying concern
     (not hard-depending on a column that could someday change meaning
     without warning — the `captured_at` OR-branch keeps working if
     `timestamp` is ever retired) without breaking today's dominant path.
     Verified against `backend/tests/test_route_gap_monitor.py` (the
     existing suite covering the same `captured_at`/`timestamp` duality via
     migration 371's RPC) — still 100% passing after this change.

- **Two disclosure-only asks** (no code change needed), added to section 4
  above: (3) for the dominant WS single-ping path, the live marker and the
  breadcrumb queried here come from the *same* incoming message gated by the
  *same* check, so a hit there re-checks one fact via a second row rather
  than proving independent corroboration — genuinely independent only for
  the REST v2 trip-batch path; (4) `utils/breadcrumb_buffer.py`'s flush
  triggers (10 points / 10s / disconnect / completion, no independent timer)
  are a second, low-severity source of the same short-leg uncorroborated
  noise already disclosed.
- **Cosmetic index-citation nit**: corrected in section 4 — the covering
  index is migration 369's `idx_driver_location_history_ride_id_timestamp`
  (`ride_id, timestamp`), not migration 239's unique index. That index does
  **not** cover `captured_at` ordering, which is precisely why this query
  keeps ordering on `timestamp` rather than switching — no index-served-
  performance claim is made for a column this query doesn't order by.

**Re-verification after all fixes:** `ruff check` / `ruff format --check`
clean on both files; `test_driver_ride_flow_coverage.py` 109/109; the same
broader regression set as section 9 (735 tests across every file exercising
`arrive_at_pickup`/location-batch ingestion, now also including
`test_route_gap_monitor.py` for the `captured_at`/`timestamp` overlap) —
0 failed, 0 regressions.
