# Change Impact & Risk Log — configurable on-demand search window

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (session_01QsLkWrRTmQ71WT3t75TCAu) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | not opened yet (worktree commit, not pushed) |
| Related issue or gap ID | `.claude/plans/2026-09-25-dispatch-reoffer-and-search-window.md` Phase 2 (2.2 + 2.3) |

## 1. Issue / gap identified

The "no driver found" search window for on-demand rides is hard-coded to 5 minutes, so riders in a thin-supply launch market wait about 4 minutes with nothing happening. The founder wants 180 s for launch, set from admin without a deploy.

## 2. Root cause

Three places each hard-code their own 5-minute value and must agree:
`ride_search_timeout(timeout_seconds=300)` (in-process timer, `routes/rides/matching.py`),
`_SEARCHING_TIMEOUT_MINUTES = 5` (durable backstop, `utils/stuck_ride_sweeper.py`), and
`_MAX_DISPATCH_ATTEMPTS = 30` (30 × 10 s no-driver retries, `routes/rides/matching.py`).
The setting column `settings.ride_search_timeout_seconds` (migration 468, default 300) existed, but nothing read it.

## 3. Fix / remediation

All three now read `settings.ride_search_timeout_seconds` through the cached `get_app_settings()` (60 s in-process TTL):

- **Timer**: `ride_search_timeout(r_id, timeout_seconds=None)` reads the setting inside the spawned task, so booking never waits on it. `booking.py` passes `None` for on-demand rides only.
- **Sweeper**: the on-demand cutoff is `now − setting`. The scheduled cutoff stays a fixed 5 minutes (see "Scheduled rides" below).
- **Retry cap**: `_dispatch_retry` stops a non-scheduled ride after `ceil(window ÷ 10 s)` attempts. That is 30 at 300 s, the same as today's constant, and 18 at 180 s.
- **Clamp is 90–300 s.** A missing setting returns 300 without logging. A read error or a non-integer value returns 300 and logs at error level, with the exception (loguru `opt(exception=True)` in matching, stdlib `exc_info=True` in the sweeper). The two readers share the same default, clamp and fallback, and a parametrised test checks that they agree.
- `.claude/context/domain-dispatch.md` "Matching algorithm" / "Offer timeout" were rewritten to match the code:
  - batch offers, fixed radius (no 2/5/10 km expansion), ETA ÷ acceptance-rate ranking;
  - 15 s offer timeout, 300 s offer-skip key, 10 s retry;
  - the configurable window;
  - the flags from migrations 466/467/469, which exist but default off.

**Why the clamp tops out at 300 s, not 600 s.** `ride_offers` has `UNIQUE(ride_id, driver_id)` (migration 100), and the `spinr:offer_skip` key lasts 300 s. In a search longer than about 300 s, a driver who was already offered the ride can be ranked again. On the default PostgREST claim path, the bulk `ride_offers` insert then fails, releases every claimed driver and raises, and the ride stalls. Migration 468's CHECK and `SettingsUpdateRequest` enforce `le=300` too (`110269c`). The code clamps to 300 on its own as well.

**Scheduled rides are unchanged.**
- `utils/scheduled_rides.py` still calls `ride_search_timeout(ride_id)` with the 300 s default. That path never reads the setting.
- `booking.py` passes the default, not `None`, when the ride it just dispatched has a `scheduled_time` (keyed on `scheduled_time`, like the sweeper — changed after Codex review on #5776; an `is_scheduled` ride with no time is dispatched immediately and is on-demand for both).
- Even if `None` reaches the timer for a scheduled ride, its grace after pickup (`scheduled_search_deadline(ride, 300)`) is still 300 s.
- The sweeper's scheduled branch still requires both `scheduled_time` and `ride_requested_at` to be more than 5 minutes old, as before.
- `_dispatch_retry`'s scheduled branch (deadline-based) runs before the settings read and does not use it.

**Alternative considered:** reading the setting in `booking.py` and passing an int. Rejected because it adds a settings read on the booking request path. Reading inside the spawned task costs nothing on the request, and it keeps the scheduled/default path free of any settings read.

## 4. Risk & impact on existing functionality

Blast radius: backend only, dispatch domain. There is no schema change in this commit and no state-machine change: it is still `searching → cancelled`, with the same CAS claim, payload, hold release, WS and push. There is no money-path change. The hold release on auto-cancel is unchanged; it just happens sooner.

Every caller of `ride_search_timeout`:
- `routes/rides/booking.py` `create_ride`. Changed: on-demand rides pass `timeout_seconds=None`; scheduled rides pass the default.
- `utils/scheduled_rides.py` (scheduled dispatch loop). Not changed; still 300 s.
- Re-exported from `routes/rides/__init__.py`. Tests call it with an explicit `timeout_seconds=` (`test_p0_ship_blockers.py`, `test_coverage_rides.py`) or with the default (`test_scheduled_timing_guards.py`). Both keep today's behaviour.

`_dispatch_retry` / `_MAX_DISPATCH_ATTEMPTS`:
- The constant is kept (30) and still exported. `test_dispatch_perf.py`, `test_dispatch_db_errors.py` and `test_offer_timeout.py` call with `attempt=_MAX_DISPATCH_ATTEMPTS + 1`. That attempt still stops for any window ≤ 300 s.
- The retry now reads the (cached) settings once per retry, and only for non-scheduled rides.

Consumers of the stuck-ride sweeper:
- `core/lifespan.py` spawns `stuck_ride_sweeper_loop`; `core/background_loop_registry.py` lists it.
- Docstring-only references, no code dependency: `utils/card_hold_release.py`, `utils/stale_intent_reconciler.py`, `utils/orphaned_hold_reconciler.py`, `utils/driver_claim_reaper.py`, `utils/stale_in_progress_ride_alerter.py`.
- The claim query changed shape. It used to be `.lt(ride_requested_at).or_(scheduled_time.is.null, scheduled_time.lt)`. It is now `.or_(and(scheduled_time.is.null, ride_requested_at.lt.<window>), and(scheduled_time.lt.<5m>, ride_requested_at.lt.<5m>))`. At 300 s both cutoffs are the same instant, so it selects the same rows as the old filter. Nested `and()` inside `or=` is already used in `routes/rides/queries.py`.

Rides the sweeper covers that have **no** in-process timer:
- Corporate guest bookings (`services/company_booking_service.py` → `_prep_and_dispatch`) never spawn `ride_search_timeout`. Only the sweeper cancels them. With the setting at 180 s they cancel at about 180 to 240 s (one sweep interval), the same as consumer rides.

Behaviour to know about:
- **Mid-session change.** The timer reads the window once, when the ride's task starts. The sweeper reads it on every 60 s sweep, after a cache delay of up to 60 s. Lowering 300 → 180 while rides are searching means the sweeper can cancel those rides at 180 s, before their own 300 s timer fires. Both paths produce the same cancel, and the CAS makes a double cancel impossible.
- **Window is measured from the first request.** `ride_requested_at` is never reset. A ride that goes back to `searching` after an offer expires or a pre-accept decline is still measured from its first request, as it is today at 300 s.
- **Timer finds the ride mid-offer.** If the timer fires while the ride is `driver_assigned` (offer pending), it does nothing, as today. The sweeper cancels the ride once it is back in `searching`.
- **Timer and sweeper agree on "scheduled".** Both key on `scheduled_time`, so a ride with a time (whatever `is_scheduled` says) keeps the fixed 5-minute window in both, and an `is_scheduled` ride with no time uses the setting in both.
- **Unique-constraint ceiling.** Covered in section 3: the code clamps to ≤ 300 s so a search never outlives the 300 s offer-skip key.

## 5. User-experience effect

- **Rider**, only after an admin lowers the setting. At 180 s a searching rider gets the existing auto-cancel about 2 minutes sooner. The WS `ride_cancelled` message and the "No drivers available" push are unchanged. It is visible mid-session to a rider who is already searching when the value changes (see section 4).
- **Today the rider app jumps to home on this cancel**, with no explanation (`rider-app/app/_layout.tsx`). Plan Phase 3 adds the "No drivers right now" sheet with **Try again** and **Schedule**. Lowering the setting should ship with or after Phase 3, so the shorter wait ends on a real screen. Keep the setting at 300 until Phase 3 is live.
- **Driver**: none. **Admin**: the retry log lines stop sooner; nothing else changes.
- At the default 300 s: no change for anyone.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/468_settings_ride_search_timeout.sql` | New `settings.ride_search_timeout_seconds INT NOT NULL DEFAULT 300`, `CHECK BETWEEN 90 AND 300` (commits `e231a0a`, `110269c`); admin field in `backend/routes/admin/settings.py` with `ge=90, le=300` | The setting itself; 300 cap for the UNIQUE(ride_id, driver_id) reason above |
| `backend/routes/rides/matching.py` | New `_ride_search_timeout_seconds()` (read + clamp 90–300 + fallback) and `_max_dispatch_attempts()`. `_dispatch_retry` derives its cap from the window. `ride_search_timeout` accepts `timeout_seconds=None` and keeps a 300 s scheduled grace. | One source for the timer and the retry cap |
| `backend/routes/rides/booking.py` | Spawn site passes `timeout_seconds=None` for non-scheduled rides | On-demand rides use the setting; scheduled rides don't |
| `backend/utils/stuck_ride_sweeper.py` | New `_search_timeout_seconds()`. The claim uses separate on-demand and scheduled cutoffs. | The durable backstop cancels at the same time as the timer; scheduled rides unchanged |
| `backend/tests/test_ride_search_timeout_setting.py` | New tests | See section 9 |
| `.claude/context/domain-dispatch.md` | Matching / offer-timeout sections rewritten to match the code | The old text was wrong (single offer, radius expansion, "never re-offer") |
| `docs/change-log/2026-09-25-configurable-ride-search-timeout.md` | This file | Required by the change log policy |

## 7. Before / after

```python
# Before — matching.py
elif attempt > _MAX_DISPATCH_ATTEMPTS:          # always 30
    return
...
async def ride_search_timeout(r_id, timeout_seconds: int = 300):
    await asyncio.sleep(timeout_seconds)
    ...
    deadline = scheduled_search_deadline(current_ride or {}, timeout_seconds)

# Before — booking.py
_deps.spawn(matching.ride_search_timeout(ride.id))

# Before — stuck_ride_sweeper.py
cutoff_iso = (now - timedelta(minutes=5)).isoformat()
... .lt("ride_requested_at", cutoff_iso)
    .or_(f"scheduled_time.is.null,scheduled_time.lt.{cutoff_iso}")
```

```python
# After — matching.py
elif attempt > _max_dispatch_attempts(await _ride_search_timeout_seconds()):  # ceil(window/10)
    return
...
async def ride_search_timeout(r_id, timeout_seconds: Optional[int] = 300):
    scheduled_grace = 300 if timeout_seconds is None else timeout_seconds
    if timeout_seconds is None:
        timeout_seconds = await _ride_search_timeout_seconds()   # setting, 90..300, fallback 300
    await asyncio.sleep(timeout_seconds)
    ...
    deadline = scheduled_search_deadline(current_ride or {}, scheduled_grace)

# After — booking.py
if updated_ride.get("scheduled_time"):
    _deps.spawn(matching.ride_search_timeout(ride.id))
else:
    _deps.spawn(matching.ride_search_timeout(ride.id, timeout_seconds=None))

# After — stuck_ride_sweeper.py
on_demand_cutoff_iso = (now - timedelta(seconds=await _search_timeout_seconds())).isoformat()
scheduled_cutoff_iso = (now - timedelta(minutes=5)).isoformat()
... .or_(f"and(scheduled_time.is.null,ride_requested_at.lt.{on_demand_cutoff_iso}),"
         f"and(scheduled_time.lt.{scheduled_cutoff_iso},ride_requested_at.lt.{scheduled_cutoff_iso})")
```

Concrete scenario (on-demand, setting = 180, no drivers):

| | Before | After |
|---|---|---|
| Last dispatch retry | ~300 s | ~180 s |
| Cancel | 300 s | 180 s |
| Restart-lost timer | 300–360 s | 180–240 s |

Scheduled ride, pickup P, setting = 180: cancelled at P + 300 s before and after.

## 8. Rollback plan

No deploy needed: `UPDATE settings SET ride_search_timeout_seconds = 300;` (or set it in admin Settings). Every replica returns to exactly today's behaviour within one settings-cache TTL (60 s). At 300 s the timer, retry cap and sweeper cutoffs are all identical to the pre-change code. No data is written differently: a cancelled ride is the same row shape as before, only earlier.

## 9. Verification performed

- [ ] Automated tests run. **Not executed locally**: pytest cannot be installed in this sandbox (PyPI is blocked). The tests were written for CI in `backend/tests/test_ride_search_timeout_setting.py`:
  - matching and sweeper readers agree for 180 / 300 / 90 / "180" / 180.0 / clamp 30→90, 301→300, 600→300 / null / "abc" / True / list;
  - missing setting → 300 with no error log;
  - read error → 300 with an error log that carries the exception (loguru `opt(exception=True)`; stdlib `exc_info`);
  - garbage value → error log;
  - timer with setting 180 sleeps 180 and issues the CAS cancel claim;
  - read error or garbage → timer sleeps 300 and still cancels;
  - default call (the scheduled_rides.py path) sleeps 300 and never reads the setting;
  - a scheduled ride with `None` keeps the pickup + 300 s grace (second sleep ≈ 240 s, not ≈ 120 s);
  - retry cap: 18 at 180 s, 30 at 300 s / missing / read error;
  - a scheduled ride at attempt 40 is still dispatched and never reads the setting;
  - sweeper claim clause: on-demand cutoff is now − 180 s and scheduled cutoff is now − 5 min; with a missing, errored or garbage setting both cutoffs equal now − 300 s;
  - booking spawns `timeout_seconds=None` for on-demand and no kwarg for a scheduled ride dispatched at once.
- [x] `ruff check` + `ruff format --check` clean on all changed Python files; `python -m py_compile` OK.
- [x] Blast-radius grep: `ride_search_timeout`, `_MAX_DISPATCH_ATTEMPTS`, `_SEARCHING_TIMEOUT_MINUTES`, `scheduled_search_deadline`, `stuck_ride_sweeper`, `ride_requested_at` writers, `status: searching` writers.
- [x] Reviewed against CLAUDE.md conventions:
  - state machine: no new transition;
  - observability: the loguru vs stdlib error-log forms match each module's logger;
  - dual-import pattern kept for the new `settings_loader` import in the sweeper;
  - no silent swallow: a settings failure logs at error level and falls back to the documented default.
- [x] Flag: the setting itself is the flag. The default of 300 means no change until an admin sets a lower value.

## What was NOT verified

- No test was run, including the existing suites that touch these functions: `test_p0_ship_blockers.py`, `test_stuck_ride_sweeper*.py`, `test_dispatch_*`, `test_offer_timeout.py`, `test_scheduled_*`, `test_create_ride_post_insert_branches.py`. CI must be green before merge.
- The new sweeper `or=(and(...),and(...))` filter was not run against real PostgREST or Supabase. Its shape follows an existing nested-`and` use in `routes/rides/queries.py`, and the timestamp format is the same `+00:00` ISO string the old filter already used.
- No staging run, no `mock_supabase_client` end-to-end dispatch dry run, and no `spinr-dispatch-reviewer` pass yet. The plan requires the dispatch-reviewer pass before merge.
- The rider-app behaviour on the earlier cancel was not exercised. The rider app has no visual-regression tooling; the jump-to-home behaviour comes from the plan, not from a device.
- Migration 468's CHECK and `SettingsUpdateRequest` are both `90..300` on this branch (`110269c`), so a value above 300 cannot be stored; the code clamp is a second guard.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (set 300)
- [x] Blast radius is stated, not assumed
- [x] UX field filled in (rider sees the existing cancel sooner once the setting is lowered; ship with or after Phase 3)
