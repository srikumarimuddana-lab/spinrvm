# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Found during a 2026-09-13/14 deep-dive audit into reported turn-by-turn navigation latency (user-reported, this session) |

## 1. Issue / gap identified

`GET /rides/{id}/navigation-steps` has no explicit, provable upper bound on how long it can take to respond when a live Google Directions call is needed (cache miss). The underlying `compute_navigation_steps` call sets `httpx.AsyncClient(timeout=2.0)`, but a bare float `timeout=` in httpx applies **independently** to each connection phase (connect/read/write/pool) — so a slow-but-not-erroring upstream could in principle take meaningfully longer than the "apparent" 2.0s before the endpoint responds, with nothing shown to the driver in the meantime.

## 2. Root cause

Unlike `backend/routes/rides/estimates.py`'s fare-distance Directions call — which races its own httpx-timeout-bound task against an explicit outer `asyncio.wait(..., timeout=_PRICING_ROUTE_WAIT_S)` (`DIRECTIONS_TIMEOUT_S + 0.5`) so the endpoint has one single, provable worst-case bound — `compute_navigation_steps` in `backend/utils/route_distance.py` was simply `await`ed directly with no outer bound in `backend/routes/rides/tracking.py`'s `get_navigation_steps` handler. This wasn't a deliberate inconsistency; the two Directions call sites (fare-distance vs. turn-by-turn steps) were built at different times (fare-distance predates this feature) and the steps path never got the same defensive wrapper.

## 3. Fix / remediation

- Added `NAV_STEPS_COMPUTE_TIMEOUT_S = 2.5` to `backend/routes/rides/tracking.py` — the same `+0.5s` margin-over-the-underlying-timeout convention already reviewed and shipped in `estimates.py`.
- Wrapped the `compute_navigation_steps(...)` call in `asyncio.wait_for(..., timeout=NAV_STEPS_COMPUTE_TIMEOUT_S)`. On `asyncio.TimeoutError`, logs a warning and sets `steps = None` — the exact same fallback value the function already returns for every other failure mode (no API key, budget exhausted, provider error, malformed response), which the existing code already turns into `{"steps": [], "destination": ...}`. No new response shape, no new client-side branch needed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one endpoint.** `NAV_STEPS_COMPUTE_TIMEOUT_S` is a new, endpoint-local constant — it does not touch `route_distance.py`'s shared `_TIMEOUT_S` (used by several unrelated call sites: fare-distance fallback, snap-to-road, etc.), so nothing else's timing behavior changes.
- **Zero live production impact today.** `driver_turn_by_turn_enabled` defaults `false` and has never been flipped on (confirmed via `ACTION_ITEMS.md` C106's 2026-09-13 NO-GO decision) — this endpoint returns `{"steps": [], "destination": None}` immediately for every ride while the flag is off, so this change has no effect on any currently-served request.
- **Does not eliminate the underlying cold-fetch latency** — being transparent about scope: the common-case cost (a genuinely uncached first request per ride+leg paying Google's real response time, typically well under the bound) is unchanged; this fix only converts an open-ended worst case into a provable, bounded one and gives the endpoint a single documented ceiling instead of relying on httpx's per-phase timeout arithmetic. A deeper latency fix (prefetching steps at `driver_assigned`/`in_progress` transition time, before the driver-app's own poll arrives) would address the common-case cost, but touches the ride state machine — deliberately out of scope here given the feature is still dark-launched with zero live exposure; see §10.
- No interaction with any background loop, the ride state machine, or money/wallet deltas.

## 5. User-experience effect

None today (flag off). Once/if `driver_turn_by_turn_enabled` is ever flipped on, the only visible difference from before is a hard ceiling on how long the driver-app can wait for a fresh reroute/initial fetch before it shows "no steps yet" instead of continuing to wait indefinitely on a pathologically slow upstream response — same "no banner yet" experience as any other Directions failure already produces today, just bounded.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/tracking.py` | Added `NAV_STEPS_COMPUTE_TIMEOUT_S` constant; wrapped `compute_navigation_steps` await in `asyncio.wait_for` with a `TimeoutError` fallback | Bounds worst-case endpoint latency; closes the gap where httpx's per-phase timeout doesn't provide one single provable bound |
| `backend/tests/test_navigation_steps.py` | New test: `test_slow_compute_times_out_and_returns_empty_steps` | Proves a hanging `compute_navigation_steps` call degrades to empty steps within the patched-down timeout instead of hanging indefinitely |

## 7. Before / after

```python
# Before
steps = await compute_navigation_steps(float(o_lat), float(o_lng), float(dest_lat), float(dest_lng))
result = {"steps": steps or [], "destination": destination}
```

```python
# After
try:
    steps = await asyncio.wait_for(
        compute_navigation_steps(float(o_lat), float(o_lng), float(dest_lat), float(dest_lng)),
        timeout=NAV_STEPS_COMPUTE_TIMEOUT_S,
    )
except asyncio.TimeoutError:
    _deps.logger.warning(...)
    steps = None
result = {"steps": steps or [], "destination": destination}
```

## 8. Rollback plan

`git-revert-safe` — reverting returns the endpoint to its previous unbounded-await behavior. No data, migration, or flag involved; this is pure request-handling logic with no persisted state of its own.

## 9. Verification performed

- [x] New test `test_slow_compute_times_out_and_returns_empty_steps` — a `compute_navigation_steps` fake that sleeps 10s, with `NAV_STEPS_COMPUTE_TIMEOUT_S` patched to 0.05s, correctly returns `{"steps": [], "destination": "dropoff"}` within a 2s outer test timeout.
- [x] Full `backend/tests/test_navigation_steps.py` re-run — 15/15 passing (7 `compute_navigation_steps`-level tests unaffected, 8 endpoint-level tests including the new one).
- [x] Broader regression: `pytest -k "tracking or live_route or navigation" -m "not slow"` — 60 passed, 1 skipped (pre-existing/unrelated), 0 failed.
- [x] `ruff check` / `ruff format --check` on both changed files — clean.
- [x] Blast-radius grep performed: `NAV_STEPS_COMPUTE_TIMEOUT_S` is a new symbol with one reader (this call site); `compute_navigation_steps`'s only other caller found is this same handler.
- [ ] `spinr-performance-sla-reviewer` and `spinr-edge-case-reviewer` run in parallel against this diff — pending at commit time; any finding will land as a follow-up commit on this same PR before merge.

**What was NOT verified:** no real Google Directions API call was exercised (mocked throughout, this repo's standard backend unit-test convention); no live/staging test since the feature is dark-launched with no environment where it's reachable end-to-end. `asyncio.wait_for`'s cancellation of the underlying task mid-flight (e.g. if it times out just as `compute_navigation_steps` was about to write its own Redis cache entry) was reasoned about, not exercised by an integration test — the worst case is a lost cache write on that one call, retried on the next request, matching the existing "only cache a real result" comment already in the code for the ordinary-failure path.

## 10. Alternatives considered

- **Prefetch navigation steps at `driver_assigned`/`in_progress` transition time** (background task fired from the ride-state-transition handlers in `routes/rides/matching.py`/wherever `in_progress` is set), so the Redis cache is warm before the driver-app's own poll arrives: genuinely addresses the common-case cold-fetch cost this fix does not. Rejected for *this* change — it touches the ride state machine (CLAUDE.md: "a wrong assumption there is a regression on a live-tested surface, not a style nit") for a feature that is currently fully dark-launched with zero live exposure, which is disproportionate risk/effort right now. `ACTION_ITEMS.md` C106 already names the correct trigger to revisit this: "before the flag flips on, or when Phase 2 scoping begins."
- **Lower `route_distance.py`'s shared `_TIMEOUT_S`**: rejected — that constant is shared by unrelated call sites (fare-distance fallback, snap-to-road); changing it to help this one endpoint would risk regressing those.
