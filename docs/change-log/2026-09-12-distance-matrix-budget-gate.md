# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code (audit follow-through, roadmap item R4) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch, rides |
| PR / commit link | srikumarimuddana-lab/spinrvm#5290 |
| Related issue or gap ID | `docs/audit/ride-experience/ROADMAP.md` R4 (P2); `EXTENDS-B3`; `cost-inventory-table.md` |

## 1. Issue / gap identified

`backend/utils/maps_eta.py`'s Google Distance Matrix fallback (used when self-hosted OSRM is
unavailable, for driver ETA feeding both dispatch ranking and the rider-facing arrival estimate)
had no budget accounting at all. Worse than R2's gap: `"distance_matrix"` was not even a member
of `maps_budget.py`'s `Sku` type, so `estimate_today_usd()` structurally could not total spend on
this call site regardless of volume — the daily circuit breaker was blind to it, not merely
unwired.

## 2. Root cause

B3 (2026-07-28) already fixed this call site's *frequency* (15s cache + >100m movement gate), but
never registered it as a tracked SKU for *cost visibility* — a different, adjacent gap B3's scope
didn't reach. Found by the 2026-09-12 ride-experience audit
(`docs/audit/ride-experience/module-d-backend-cost.md`), not by an incident.

## 3. Fix / remediation

Added `"distance_matrix"` to `maps_budget.py`'s `Sku` Literal and `_PRICE_USD` dict (rate
estimate — see the "what was NOT verified" note below), then wired `check_budget()` +
`record_call("distance_matrix")` into both `get_ride_eta_seconds` and `batch_get_etas` in
`maps_eta.py`, gating the Google call and falling through to the existing haversine estimate on
exhaustion — same shape as R2's fix, applied to this call site.

**Adversarial review finding, fixed before commit:** an independent `spinr-performance-sla-reviewer`
pass flagged that `batch_get_etas` is called directly (not backgrounded) from
`routes/rides/matching.py`'s dispatch-matching path, inside a purpose-built 1.2s timeout carve-out
for the Distance Matrix call. Wiring `check_budget()` into that path made `estimate_today_usd()`'s
pre-existing 7-sequential-`redis_get` implementation newly relevant to a tightly time-boxed hot
path for the first time. Fixed by batching those reads into a single `redis_mget` call (the same
pattern this codebase already uses on the dispatch hot path for offer-skip lookups), preserving
the function's documented "errors fail open" contract explicitly (`redis_mget` raises on a Redis
error, unlike `redis_get`'s own per-key soft-fail — caught and treated as "assume 0" like before).

## 4. Risk & impact on existing functionality

- **What else reads/writes the same state:** `maps_budget.py`'s Redis-backed daily counters are
  shared with `maps_proxy.py`, `route_distance.py`, `_shared.py` (R2), and `ai/tools_booking.py`.
  This fix adds a new SKU bucket (`distance_matrix`) — additive, no existing bucket's key or
  semantics changed. `estimate_today_usd()`'s Redis access pattern changed (N calls → 1), but its
  return contract (a `float` total, fail-open on error) is unchanged — verified by new unit tests.
- **Could this regress a working flow?** Only in the budget-exhausted edge case, where
  `get_ride_eta_seconds`/`batch_get_etas` now correctly fall back to haversine instead of
  continuing to call (and bill) Google — the intended fix, using an already-shipped fallback path.
- **Blast radius:** single-surface (backend). `get_ride_eta_seconds` is called from the WS receive
  path via a backgrounded `refresh_ride_eta` (confirmed off the <150ms hot path by the reviewer —
  `routes/websocket.py`'s receive loop only calls the cache-only `get_cached_ride_eta`).
  `batch_get_etas` is called directly from `routes/rides/matching.py`'s dispatch ranking, inside
  its existing `asyncio.wait_for(1.2)` + haversine-fallback-on-exception guard — that guard already
  protects the hard 2s dispatch SLA; this diff's own redis_mget fix additionally protects the
  *quality* of ETA-based ranking from degrading under Redis latency, which the reviewer flagged as
  a new (non-blocking) risk before the fix.
- **Known, pre-existing, not fixed here:** the multi-replica in-process Redis fallback (when
  `REDIS_URL` is unset) tracks the daily budget per-replica, not globally — an existing,
  documented fail-open trade in `maps_budget.py`, unrelated to this diff.

## 5. User-experience effect

- **Who sees a difference:** nobody under normal operation. In the rare budget-exhausted case,
  ETA precision degrades slightly (haversine vs. traffic-aware) for both dispatch ranking and the
  rider's arrival estimate — the existing, already-shipped fallback, not new behavior.
- **Visible mid-session?** No.
- **Copy/notification change:** none.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/maps_budget.py` | Added `"distance_matrix"` SKU; batched `estimate_today_usd()`'s per-SKU `redis_get` loop into one `redis_mget` call. | R4 + adversarial-review fix |
| `backend/utils/maps_eta.py` | `get_ride_eta_seconds` and `batch_get_etas` now call `check_budget()` before the Google Distance Matrix HTTP call and `record_call("distance_matrix")` after. | R4 |
| `backend/tests/test_maps_eta_budget_gate.py` (new) | 5 tests: budget-exceeded skips Google entirely for both functions, spend recorded on success for both, no-api-key path never checks budget. | R4 (CLAUDE.md pre-merge gate 4) |
| `backend/tests/test_maps_budget.py` (new) | 6 tests: `estimate_today_usd` sums correctly via one `redis_mget` call, fails open on a Redis error, ignores malformed values; `check_budget` allowed/not-allowed; `distance_matrix` is a registered SKU (direct regression test for the gap this change closes). | R4 + the batching fix |

## 7. Before / after

```python
# Before — 7 sequential redis_get calls, reachable for the first time from a
# 1.2s-timeout dispatch path once this diff's check_budget() wiring landed
async def estimate_today_usd() -> float:
    total = 0.0
    for sku, price in _PRICE_USD.items():
        try:
            raw = await redis_get(_key(sku))
        except Exception:
            raw = None
        if raw is not None:
            total += int(raw) * price
    return total
```

```python
# After — one redis_mget round-trip, same fail-open contract
async def estimate_today_usd() -> float:
    skus = list(_PRICE_USD.items())
    try:
        raw_values = await redis_mget([_key(sku) for sku, _price in skus])
    except Exception:
        raw_values = [None] * len(skus)
    total = 0.0
    for (_sku, price), raw in zip(skus, raw_values, strict=True):
        if raw is not None:
            total += int(raw) * price
    return total
```

**Concrete before/after scenario (CLAUDE.md pre-merge gate 4):** OSRM degrades fleet-wide mid-day
— *before this diff*: every active ride's ETA refresh falls through to Google Distance Matrix with
no budget accounting at all (invisible spend); *after R4 alone*: spend is tracked and the breaker
can trip, but `batch_get_etas`'s dispatch-path check now costs 7 sequential Redis round-trips
inside a 1.2s window; *after the adversarial-review fix*: the same check costs 1 round-trip,
closing the risk the reviewer identified before it ever shipped.

## 8. Rollback plan

`git revert` is sufficient for both the SKU registration and the `redis_mget` batching — no data
mutation, no migration, no schema change, no feature flag needed. `MAPS_DAILY_BUDGET_USD` can be
raised without a redeploy if the new SKU's estimate proves too conservative in practice.

## 9. Verification performed

- [x] Automated tests: 305 tests pass across every file that touches `maps_eta.py`,
      `maps_budget.py`, or the dispatch/WS paths that call them (`test_maps_budget.py` new,
      `test_maps_eta_budget_gate.py` new, `test_maps_eta_osrm.py`, `test_maps_eta_movement_gate.py`,
      `test_eta_hot_path.py`, `test_rides_matching_coverage.py`,
      `test_dispatch_notify_loop_branches.py`, `test_maps_proxy.py` + coverage variant,
      `test_ai_tools_booking.py`, `test_route_distance_coverage.py`,
      `test_compute_route_fallback.py`, `test_address_verification.py`,
      `test_directions_route.py`, `test_loguru_call_conventions.py`, `test_coverage_boost.py`,
      `test_utils_extended.py`).
- [ ] Manual repro steps followed in staging — not done, no staging access in this session.
- [x] Blast-radius grep performed — confirmed every caller of `estimate_today_usd()` (only
      `check_budget()`) and every caller of `check_budget()`/`record_call()` (5 call sites across
      `maps_proxy.py`, `route_distance.py`, `_shared.py`, `ai/tools_booking.py`, and now
      `maps_eta.py`) — none besides the two new call sites needed any change.
- [x] Reviewed against relevant CLAUDE.md conventions — dual-import pattern followed; N+1-style
      anti-pattern (sequential Redis reads on a hot path) caught and fixed before merge, same
      discipline CLAUDE.md already calls out for Supabase reads.
- [x] Adversarial pre-implementation review: alternative considered — a separate budget mechanism
      specific to Distance Matrix — rejected in favor of extending the existing unified `Sku`
      Literal, since fragmenting cost visibility across two breakers defeats the point of one
      daily estimate.
- [x] Adversarial post-implementation review: `spinr-performance-sla-reviewer` run against the
      staged diff — confirmed OSRM-first provider order is untouched by the budget gate, confirmed
      `record_call` placement is correct and consistent with R2's sibling pattern, confirmed no
      loguru-class log-format bug (this module uses stdlib `logging`), and found the sequential-
      Redis-read risk this commit fixes.
- [ ] Feature-flagged — not flagged, for the same reason as R2: this reuses an already-proven
      pattern and only changes behavior in a budget-exhaustion edge case that doesn't occur in
      normal operation.

### What was NOT verified

- **Live Google Maps Platform pricing could not be fetched** (`developers.google.com` is
  `EGRESS_BLOCKED` from this environment — the same limitation the audit's own Module B hit for
  the turn-by-turn proposal). The `distance_matrix` rate ($0.010/call) is a documented estimate
  for the traffic-aware (Advanced) tier this call site's `traffic_model=best_guess` parameter
  triggers, deliberately set higher than the Essentials rate other SKUs use so the breaker
  overestimates (trips early) rather than underestimates (hides real spend) if wrong. Re-verify
  against Google's pricing page before relying on this figure for a real dollar budget decision.
- No production load test of the `redis_mget` batching fix under real dispatch concurrency —
  reasoned from code shape and the existing `redis_mget` docstring's own stated purpose, not
  measured.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, or raise `MAPS_DAILY_BUDGET_USD`).
- [x] Blast radius is stated, not assumed (all 5 `check_budget`/`record_call` call sites listed).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 —
      no difference under normal operation; edge-case behavior stated explicitly).
