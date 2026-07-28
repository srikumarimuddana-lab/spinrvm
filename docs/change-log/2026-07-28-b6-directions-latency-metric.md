# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | (filled in on PR) |
| Related issue or gap ID | B6 (`ACTION_ITEMS.md`) |

## 1. Issue / gap identified

`_PRICING_ROUTE_WAIT_S`/`DIRECTIONS_TIMEOUT_S` in the fare-estimate path (`routes/rides/estimates.py` / `_shared.py`) were set to 1.5s/2.0s by judgement, not data. Nobody has measured the real Google Directions latency distribution, so it's unknown how much of the road-route pricing benefit a tighter timeout would give up, or whether the current one is even generous enough.

## 2. Root cause

Not a bug — a missing observability prerequisite. The timeout constants were picked when the road-route pricing fix shipped (the "Regina street 12.12km→16.46km" incident fix), with no latency histogram in place to justify the specific numbers chosen.

## 3. Fix / remediation

`_route_fetch()` inside `compute_ride_estimates` now times every real Directions call and records it to a new `spinr_fare_directions_duration_ms` histogram (via the existing `utils/metrics.py` `observe()`/`_metric_observe` plumbing — no new metrics infrastructure). Recorded in a `finally` block so a slow or failed call still shows up in the distribution — the exact tail this metric exists to reveal.

**Explicitly not done in this change**: re-tuning `DIRECTIONS_TIMEOUT_S`/`_PRICING_ROUTE_WAIT_S` from the observed p99. That requires real production traffic this dev session cannot generate — the constants are unchanged. This PR closes only the measurement half of B6.

## 4. Risk & impact on existing functionality

- **What else reads/writes the same state?** `spinr_fare_directions_duration_ms` is a brand-new metric name — no other caller reads or writes it. The instrumented `_route_fetch()` closure itself is called from exactly one place (`compute_ride_estimates`'s route_task spawn) and its return value/exception behavior is completely unchanged — only a timer wraps the existing call.
- **Could this regress a flow that currently works?** No — the change is purely additive observability. The Directions call, its timeout, its fail-open-to-haversine fallback, and the pricing wait (`_PRICING_ROUTE_WAIT_S`) are all byte-for-byte unchanged.
- **Blast radius**: isolated — one function (`_route_fetch`, a closure inside `compute_ride_estimates`), one file (`routes/rides/estimates.py`).
- **Background loops / ride state machine / money?** None. No ride state, wallet, or background-loop interaction — this is a fare-estimate (pre-booking) read path only.

## 5. User-experience effect

None. No rider/driver/corporate-admin/internal-admin facing behavior changes — the metric is invisible to end users and adds negligible per-request overhead (two `time.monotonic()` calls, one in-process histogram update).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/estimates.py` | `_route_fetch()` now times the Directions call and records `spinr_fare_directions_duration_ms` in a `finally` block | Measure real Directions latency (B6) so the hardcoded timeout can eventually be justified by data |
| `backend/tests/test_ride_estimate_branches.py` | 2 new tests: metric recorded on success, metric recorded even when the Directions call raises | Pin the "record even on failure" behavior — the whole point of using a `finally` block |

## 7. Before / after

```python
# Before
async def _route_fetch() -> Optional[dict]:
    if not _maps_key:
        return None
    try:
        return await _fetch_directions_route(...)
    except Exception as _route_err:
        logger.warning("[estimate] route fetch failed (non-fatal): %s", _route_err)
        return None
```

```python
# After
async def _route_fetch() -> Optional[dict]:
    if not _maps_key:
        return None
    _route_t0 = _time.monotonic()
    try:
        return await _fetch_directions_route(...)
    except Exception as _route_err:
        logger.warning("[estimate] route fetch failed (non-fatal): %s", _route_err)
        return None
    finally:
        _deps._metric_observe(
            "spinr_fare_directions_duration_ms",
            (_time.monotonic() - _route_t0) * 1000.0,
        )
```

## 8. Rollback plan

`git-revert-safe` — the change is a single additive timer + histogram observation with no schema, config, or behavior change. Reverting removes the metric with zero functional impact; no data-level remediation needed since nothing durable was written.

## 9. Verification performed

- [x] Automated tests run (unit) — `backend/tests/test_ride_estimate_branches.py` (2 new tests) plus the full local backend suite (4865 passed, 8 skipped, 1 xfailed; the 4 failures present are pre-existing on `main`, unrelated to this diff — confirmed via `git diff --stat origin/main -- backend/`, which shows only `routes/rides/estimates.py`, this test file, and `ACTION_ITEMS.md` touched).
- [ ] Manual repro steps followed in staging — not performed, no staging/live Google Directions API access in this session.
- [x] Blast-radius grep performed — searched `routes/rides/` and `ai/` for every other `_fetch_directions_route` caller; found `routes/rides/booking.py`, intentionally NOT instrumented here since B6's SLA concern (CLAUDE.md's fare-estimate P95) is specific to the estimate path, not the post-booking one.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — matches the observability convention in `utils/metrics.py`'s own `time_ms()` docstring ("Records even when the block raises — a slow failure is still latency the SLA dashboards must see").
- [ ] Feature-flagged — not applicable, pure observability addition with no user-visible or risky behavior to flag.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — single additive observation call, plain revert
- [x] Blast radius is stated, not assumed — isolated to one closure in one file, confirmed via grep of all other callers
- [x] No silent behavior change to an already-shipped flow — the Directions call, timeout, and fallback behavior are byte-for-byte unchanged; only a timer wraps them
