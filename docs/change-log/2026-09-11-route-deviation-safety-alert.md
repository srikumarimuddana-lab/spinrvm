# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | (this commit) |
| Related issue or gap ID | `.claude/context/domain-safety.md`'s "Night ride protections" — ">500m/60s live deviation ping" was explicitly listed under "Intended, not built" |

## 1. Issue / gap identified

No code in this repo detects, live, whether a driver on an `in_progress`
ride has deviated substantially from the booked route and alerts the
trust-and-safety team. `domain-safety.md` names this gap explicitly.
`utils/route_validation.py`'s `deviation_pct` is a different check
entirely — it runs once, after the trip completes, and asks "is this GPS
point near any road" (fraud/spoofing detection), not "is the driver still
heading toward the booked destination."

## 2. Root cause

Never built — not a regression, a greenfield gap. Confirmed by reading
`utils/route_validation.py`'s only call site
(`routes/drivers/_shared.py`, post-trip) and by there being no other
module that reads `rides.planned_route_polyline` against a live driver
position.

## 3. Fix / remediation

New module `backend/utils/route_deviation_alerter.py`, registered as a
background loop in `core/lifespan.py` (`route_deviation_alerter (30s)`,
added to `_WATCHDOG_LOOP_NAMES`) and gated by a new `app_settings` flag,
`route_deviation_alert_enabled` (`schemas.py`, default `False`).

Every 30 seconds, for every `in_progress` ride with a usable
`planned_route_polyline` (≥2 points): compute the driver's current
position's (`drivers.lat/lng`) minimum haversine distance to any point on
that polyline (reusing `geo_utils.calculate_distance` — no new external
API call, unlike `route_validation.py`'s OSRM/Google Roads dependency).
If that distance is ≥500m and stays ≥500m for ≥60 seconds (tracked via two
Redis `SET NX` keys per ride, the same two-phase claim idiom
`safety_checkin_loop.py` uses for its own send/escalate state), open a
`safety_incidents` row (`category="route_deviation"`) and fan it out via
the existing `notify_safety_team()` helper (WS + email + critical log),
plus an audit-log entry. Returning to within 500m of the route clears
both Redis keys, so a second, later deviation episode on the same (long)
ride is tracked and can escalate again rather than being permanently
suppressed by the first episode.

This is deliberately **alert-only** — it never writes to `rides`,
`drivers`, or any insurance-period table. Mirrors
`stale_in_progress_ride_alerter.py`'s own reasoning: an automated ride-state
reaction to a live deviation is out of scope because the deviation could be
a legitimate detour (road closure, rider-requested stop), so a human on the
safety team decides what happens next; this loop's only job is getting them
there.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated / additive.** New module, new loop registration,
  new flag, one new `safety_incidents` category value. No existing route,
  background loop, or table write path is modified.
- **Who else reads/writes the tables this touches:**
  - `rides.planned_route_polyline` — written once at booking
    (`routes/rides/booking.py`), read by the post-booking snapshot-image
    generator (`routes/drivers/_shared.py`'s `_generate_and_store_ride_snapshot`
    caller in `booking.py`) and now also read (never written) by this new
    loop. No write-write or write-read race: this loop only ever reads the
    column.
  - `drivers.lat/lng` — written by every driver location-update path
    (`routes/drivers/location.py`, gated by `utils/location_write_gate.py`)
    and read by dispatch, admin monitoring, and now also this new loop
    (read-only). No interaction with `location_write_gate.py`'s Period-1
    insurance accumulator logic (CLAUDE.md's warning about that file) —
    this loop never calls into the write gate or writes `drivers` at all.
  - `safety_incidents` — written by `routes/safety.py` (`POST
    /safety/report`), `routes/rides/safety.py` (`trigger_emergency`),
    `safety_checkin_loop.py`'s escalation, and now this loop. Read by the
    admin safety dashboard (`admin-dashboard/src/app/dashboard/safety/page.tsx`),
    which renders `category` as free text (confirmed by reading the page:
    it already displays a non-enumerated existing value,
    `safety_checkin_no_response`, with no special-casing) — the new
    `"route_deviation"` category needs no dashboard change to display
    correctly.
  - `app_settings` — one new key, `route_deviation_alert_enabled`, additive
    to the existing `AppSettings` schema; no existing key touched.
- **Background loop count changed**: `core/lifespan.py` now spawns 42
  loops (was 41) — `CLAUDE.md`'s own stated count and
  `tests/test_lifespan_watchdog_coverage.py`'s hardcoded assertions were
  both updated in this change (the test is specifically designed to fail
  loudly on a miscount, confirmed it did before the fix).
- **Replay-safety**: two `redis_set_nx` claims (first-observed timestamp,
  escalation) ensure only one replica starts tracking a deviation episode
  and only one replica creates the incident, matching this repo's
  established idiom. A Redis error during either claim is unguarded (same
  as `safety_checkin_loop.py`'s own claim call) — it aborts just that tick
  for that ride via the outer loop's try/except, retried automatically 30s
  later; it does not corrupt state or duplicate-fire.

## 5. User-experience effect

**None, currently.** The flag defaults `False`, so this loop's `_tick()`
returns immediately without querying anything (verified by test:
`test_flag_disabled_skips_the_tick_entirely` asserts zero DB queries).
Once an admin turns the flag on: no rider/driver-facing UI change at
all — the only visible effect is a new row appearing in the internal
admin safety dashboard's incident queue when a sustained deviation is
detected. Not visible to a rider or driver mid-ride in any way.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/route_deviation_alerter.py` | New. The detection/escalation loop. | Closes the domain-safety.md gap. |
| `backend/tests/test_route_deviation_alerter.py` | New. 19 tests. | Coverage for detection, sustain-window timing, episode reset, flag fail-closed behavior, no-ride/driver-mutation invariant, loop wrapper. |
| `backend/core/lifespan.py` | Added `_spawn("route_deviation_alerter (30s)", ...)` + `_WATCHDOG_LOOP_NAMES` entry. | Registers and watchdog-monitors the new loop. |
| `backend/schemas.py` | Added `route_deviation_alert_enabled: bool = False` to `AppSettings`. | Dark-launch kill switch. |
| `backend/tests/test_lifespan_watchdog_coverage.py` | Updated hardcoded loop-count assertions (41→42 spawned+watched). | This repo's own regression guard for exactly this kind of change — it failed as designed until fixed. |
| `CLAUDE.md` | Updated the "spawns 41 background asyncio loops" line and the Redis-leader-lock count/description. | Keep the stated counts accurate — this repo has an established pattern of catching and correcting exactly this kind of drift. |
| `.claude/context/domain-safety.md` | Updated "Route-deviation safety alert" from "does not exist" to "built, dark-launched." | Keep the domain doc in sync with reality — explicitly notes it's not yet verified in staging or turned on. |

## 7. Before / after

Pure additive change — no existing behavior-changing diff to show. The
one "before/after" worth noting is prose, in `domain-safety.md`:

**Before:** "Route-deviation safety alert — does not exist... it does not
run live and pings nobody on the safety team."

**After:** "built 2026-09-11, dark-launched (not yet verified in staging
or turned on)... gated behind `app_settings.route_deviation_alert_enabled`
(default False)."

## 8. Rollback plan

- **Immediate, no-deploy rollback**: flip `route_deviation_alert_enabled`
  back to `False` (or leave it — it already defaults off) via the admin
  `app_settings` mechanism. The loop's `_tick()` returns immediately with
  zero side effects when disabled.
- **Full code rollback**: `git revert` — nothing here has been applied to
  live data yet (flag defaults off), so a plain revert is fully sufficient
  if the feature is abandoned outright. No migration, no Stripe charge, no
  wallet delta, no ride-state row to unwind.

## 9. Verification performed

- [x] **Automated tests run**: `python3 -m pytest tests/test_route_deviation_alerter.py tests/test_lifespan_watchdog_coverage.py tests/test_safety_checkin_loop.py tests/test_stale_in_progress_ride_alerter.py --no-cov -q` → **64 passed, 0 failed**. (The project's default `--cov-fail-under=60` gate was skipped for this targeted run via `--no-cov` since running 4 test files can never reach 60% coverage of the ~53k-line backend — the full suite's own coverage gate is unaffected by this change, which only adds a small, fully-tested new module.)
- [x] `ruff check` / `ruff format --check` clean on all changed Python files.
- [x] Blast-radius grep performed — see §4 above (`planned_route_polyline`
  readers/writers, `drivers.lat/lng` readers/writers,
  `safety_incidents` readers/writers, admin dashboard category rendering).
- [x] Reviewed against `CLAUDE.md`'s background-loop conventions (replay-safety,
  the Period-1 accumulator warning on `location_write_gate.py` — confirmed
  not applicable since this loop never writes `drivers`) and the
  `spinr-background-loop` skill's recipe.
- [x] Feature-flagged — `route_deviation_alert_enabled`, default off, per
  CLAUDE.md's rollout rule for new, non-trivial, team-facing behavior.
- [ ] Manual repro / staging check — not run; this sandbox has no staging
  Supabase environment. The flag being off by default is the safeguard
  until a human verifies it in staging and flips it on.

## 10. What was NOT verified

- **Not run against a real Postgres/Supabase instance** — verified only
  against the mocked `mock_supabase_client`-style `FakeDB` test double
  (matching this repo's own convention for background-loop unit tests,
  e.g. `test_stale_in_progress_ride_alerter.py`'s identical approach). The
  actual `rides`/`drivers` column names and types were confirmed by
  reading `schemas.py` and other call sites, not by querying a live
  schema.
- **The point-to-polyline distance calculation is vertex-distance, not a
  true point-to-segment projection** — a deliberate simplicity trade-off
  (stated in the module's own docstring) given a Google/OSRM overview
  polyline is normally dense enough for a 500m threshold; this has not
  been validated against a real, sparse or unusually simplified polyline
  that might produce a materially different distance than a true
  segment-projection would.
- **Not verified end-to-end with `notify_safety_team`'s real email/WS fan-out** —
  the escalation path calls the existing, already-tested helper, but this
  change's own tests mock it rather than exercising a real email send or
  WS broadcast.
- **No staging/production verification that the flag is actually off in a
  live environment** — it defaults off in code; nothing here confirms
  what any deployed environment's `app_settings` row currently contains.

## Sign-off

- [x] Rollback plan is concrete and testable (flag flip, or plain revert — see §8).
- [x] Blast radius is stated: isolated/additive, every touched table's
  other readers/writers enumerated in §4.
- [x] No silent behavior change to any already-shipped flow — new
  behavior ships fully dark (flag off by default) until a human turns it on.
