# Change Impact & Risk Log — dispatch wider second search pass (flagged, default off)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (for mkkreddy52@gmail.com) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | Commit `d6e1efa` (code + tests); settings from `e231a0a` (migration 469) |
| Related issue or gap ID | `.claude/plans/2026-09-25-dispatch-reoffer-and-search-window.md`, Phase 4 |

## 1. Issue / gap identified

The dispatch radius is fixed for the whole search. In a thin-supply market, a
ride can cancel with "no drivers" while a driver sits a few km past the radius.

## 2. Root cause

`_match_driver_to_ride_attempt` uses one radius from
`DispatchService.resolve_matching_config` (the area's `search_radius_km`, else
the global setting) on every attempt. Nothing widens it. The
"2 → 5 → 10 km" expansion in `.claude/context/domain-dispatch.md` does not exist
in code.

## 3. Fix / remediation

The attempt reads the settings from migration 469. The flag
`dispatch_expanded_radius_enabled` defaults to false. When it is on and the ride
has been searching for at least `dispatch_expanded_radius_after_seconds`
(default 45), the attempt uses:

```
effective = max(normal, min(normal × dispatch_expanded_radius_multiplier, dispatch_expanded_radius_max_km))
```

This happens once, right after `resolve_matching_config`, by rebinding the local
`search_radius`. So all six consumers get the same value: the primary geo box,
the primary `fetch_dispatch_candidates`, the primary `filter_and_rank_drivers`,
and the same three in the vehicle cascade. The box and the haversine gate cannot
disagree.

- **"Searching since" is `rides.ride_requested_at`.** Booking stamps it. When a
  scheduled ride flips to `searching`, it is set to the flip time
  (`utils/scheduled_rides.py`, `features.py`). The stuck-ride sweeper uses the
  same clock to cancel. `created_at` was not used, because for a scheduled ride
  it is the booking time. `attempt` was not used, because offer-timeout and
  decline re-dispatches restart it at 0.
- The info log `[DISPATCH] expanded radius ride_id=… normal_km=… effective_km=…`
  records each expansion, and the counter `spinr_dispatch_radius_expanded_total`
  goes up by one. The log holds no GPS and no PII.
- A missing or unparseable `ride_requested_at` logs a warning and keeps the
  normal radius. Invalid settings values log an error and keep the normal radius.
- **No fare change.** There is no long-pickup fee. That needs founder sign-off and
  is out of scope.

**Alternative considered:** a second, explicit "expanded pass" that runs only
after the normal pool comes back empty, as the plan text suggests. Rejected:
it doubles the candidate queries for each attempt, and it needs changes in the
cascade and no-drivers branches, where other agents are editing. A time-based
switch reuses the existing 10 s retry loop as the second pass and changes one
variable.

## 4. Risk & impact on existing functionality

- **Blast radius: one function.** The only production caller of
  `resolve_matching_config` is `routes/rides/matching.py:421`. Its return shape
  is unchanged. The other references are tests that patch it:
  `test_rides_matching_coverage.py`, `test_coverage_rides.py`,
  `test_dispatch_db_errors.py`, `test_e2e_wav_dispatch.py`,
  `test_offer_timeout.py`, `test_dispatch_claim_parity.py`,
  `test_dispatch_metrics.py`, `test_dispatch_notify_loop_branches.py` and
  `test_dispatch_match_attempt_branches.py`. They all pass `{}` or settings
  without the flag, so they take the flag-off path.
- **`search_radius` inside the attempt.** Every use is between the rebind and
  the end of the cascade: the match-start log, `dispatch_geo_bounds` ×2,
  `fetch_dispatch_candidates` ×2, `filter_and_rank_drivers` ×2, and the pool
  logs. Nothing after the cascade reads it.
- **Not changed:** `/rides/estimate`, which uses its own hard-coded 10 km for
  the driver count and pre-booking ETA; `DispatchService.find_candidate_drivers`;
  offer-skip keys; the state machine; money.
- **P95 dispatch latency.** A bigger box can return more candidates. The read
  is still capped at `max_candidate_pool` (50–500, default 500). Ranking and
  Distance Matrix ETA stay limited to the top `max(max_offers × 5, 15)`. The
  expected cost is a larger DB scan within the online-and-available partial
  index, not extra round-trips.
  - With provider `legacy`, an unordered LIMIT can drop the nearest driver
    when a box holds more than `max_candidate_pool` drivers. That existing
    warning becomes more likely with a larger box.
  - With provider `h3`, a larger radius can move the read to a coarser
    resolution. It can also raise `H3DiskTooLargeError`, and the provider then
    falls back to `legacy`.
  - Neither case is new: admins can already set `search_radius_km` up to 100,
    and `max_km` is capped at 100 too.
- **Insurance periods.** Classification does not change. Period 2 still opens
  at claim, exactly as today. Offers can now reach drivers further away, so
  Period 2 legs (en route to pickup) will be longer in time and distance.
  Nothing about which period applies changes.
- **Driver economics.** With 0% commission, pickup is unpaid. Far drivers may
  decline, and Phase 1 would then re-ask them once. That is a product
  trade-off, recorded in the plan's open question on a long-pickup fee.
- **Background loops.** The 10 s dispatch retry chain and the stuck-ride
  sweeper are unchanged. The expansion is only a per-attempt read.

- **Review (spinr-dispatch-reviewer, 2026-09-25): safe to merge, no blockers.**
  Two warnings, handled as follows:
  - Scheduled rides can search up to ~35 min (dispatch lead + 5 min grace),
    longer than the 300 s Redis offer-skip key, so a wider box made it more
    likely that a driver already offered the ride re-enters the pool. Covered
    by the durable already-offered filter in the same PR
    (`docs/change-log/2026-09-25-dispatch-already-offered-db-filter.md`).
  - Config trap: `dispatch_expanded_radius_after_seconds` must be below
    `ride_search_timeout_seconds`, or the wider pass never fires for on-demand
    rides (nothing enforces this across the two settings). Launch values:
    search 180 s, expand after 45 s.

## 5. User-experience effect

- **Flag off (default): nobody sees a change.**
- **Flag on:**
  - A rider who is still searching after `after_seconds` may be matched with a
    further driver, so the pickup wait is longer. This shows up mid-session:
    a search that is already running widens on its next 10 s retry after the
    flag flips.
  - Drivers up to `max_km` away can get offers. The driver-app offer card
    already shows a client-side straight-line "X km away" to the pickup.
    It shows no pickup time.
- No copy or notification changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/469_settings_dispatch_expanded_radius.sql` | New `settings` columns `dispatch_expanded_radius_enabled` (default false), `_after_seconds` (45), `_multiplier` (1.5), `_max_km` (20), with CHECK bounds. Added in `e231a0a`; admin fields in `backend/routes/admin/settings.py`. | Flag and tuning for the wider pass |
| `backend/routes/rides/matching.py` | In `_match_driver_to_ride_attempt`, compute the effective radius right after `resolve_matching_config` (behind the flag), then log and count the expansion. | Phase 4 wider second pass |
| `backend/tests/test_dispatch_expanded_radius.py` | New. Covers flag off (columns absent, and false with aggressive values), before and after the delay, `after_seconds=0`, cap at `max_km`, never below normal, missing timestamp, and scheduled-ride timing. Each test checks that all six consumers, cascade included, got the same radius. | Regression coverage |
| `docs/change-log/2026-09-25-dispatch-expanded-radius.md` | This file | CLAUDE.md gate |

## 7. Before / after

```python
# Before
(algorithm, min_rating, search_radius, max_offers, use_eta, max_candidate_pool) = \
    await _shared.dispatch.resolve_matching_config(ride, app_settings=..., area=...)
# ... search_radius used unchanged for box / read / rank / cascade
```

```python
# After
(... search_radius ...) = await _shared.dispatch.resolve_matching_config(...)
if app_settings.get("dispatch_expanded_radius_enabled") is True:
    # searching for >= after_seconds (from ride_requested_at)?
    _expanded = max(search_radius, min(search_radius * mult, max_km))
    if _expanded > search_radius:
        search_radius = _expanded   # every later consumer uses it
        logger.info("[DISPATCH] expanded radius ride_id={} normal_km={} effective_km={}", ...)
        _metric_inc("spinr_dispatch_radius_expanded_total")
```

Dry run with the defaults (area radius 10 km, multiplier 1.5, cap 20 km, 45 s):

| Case | Radius |
|---|---|
| Attempt at 0 s or 10 s | 10 km, as today |
| First retry at or after 45 s | 15 km, for the primary pool and the cascade |
| Multiplier set to 3.0 | 20 km (the cap) |
| Area radius 25 km | Stays 25 km (never below normal, no metric) |

## 8. Rollback plan

- `UPDATE settings SET dispatch_expanded_radius_enabled = false;` or turn it off
  in the admin settings API.
- The next dispatch attempt, at most about 10 s later, goes back to the normal
  radius. No redeploy.
- No data is written, so there is nothing to remediate. Offers already sent to
  far drivers finish or expire normally.

## 9. Verification performed

- [ ] Automated tests run: **no.** pytest and the backend dependencies
  (loguru, fastapi and others) cannot be installed in this sandbox, because
  PyPI is blocked. The new test file has not been run. CI runs it.
- [x] `ruff check` and `ruff format --check` pass on both changed Python files.
  `python -m py_compile` passes on both. The repo pre-commit hook passed
  (PII-in-logs, money arithmetic and ruff checks).
- [x] Blast-radius grep done for `resolve_matching_config`, `search_radius`
  inside `matching.py`, writers of `ride_requested_at`, and writers that set
  `status: searching` (scheduled flips re-stamp it; offer-timeout and decline
  reverts keep it).
- [x] Checked against CLAUDE.md: loguru `{}` placeholders and
  `opt(exception=True)`; no GPS or PII in logs; metric named
  `spinr_<domain>_<metric>_total`; flag defaults off; no state-machine or money
  change.
- [x] Flagged, default off.
- [ ] `mock_supabase_client` dry run executed: no. The new tests use
  `patch(...db_supabase)`, like `test_dispatch_match_attempt_branches.py`, and
  have not been run.

### What was NOT verified

- The new tests were never executed. The mock harness is modelled on
  `test_dispatch_match_attempt_branches.py`, but nothing confirms it passes.
- No staging run. Real candidate counts and P95 latency at 15–20 km boxes for
  the `legacy`, `postgis` and `h3` providers were not measured.
- No `spinr-dispatch-reviewer` or `spinr-insurance-period-auditor` pass was
  run, because this sub-agent has no Agent tool. **Run both before merge.**
- The effect on acceptance rate from longer unpaid pickups is unknown.
- rider-app and driver-app were not changed and have no visual-regression
  tooling. Their offer-card and ETA behaviour was checked by reading the code,
  not by running the apps.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flag off)
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change: default off; UX effect stated for when it is on
