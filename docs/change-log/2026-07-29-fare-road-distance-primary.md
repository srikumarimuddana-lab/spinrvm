# Change Impact & Risk Log — make the road route the billing basis in practice (stop haversine undercharging)

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/fare-road-distance-primary` |
| Related issue or gap ID | Rider report: 4325 Wakeling St → 2000 Aurora Blvd priced 15.5 km / CA$38.41 via AI vs 16.6 km / CA$40.78 via the normal flow |

## 1. Issue / gap identified

The same trip, through the same pricing function, was billed on two different distances. The shorter figure was the straight-line (haversine) fallback, not the road route. Because haversine is always ≤ road distance, every fallback undercharges — and under 0% commission that shortfall comes out of the driver's fare.

Reported delta reconciles exactly: 1.1 km × CA$2.00/km (Regina) × 1.02 insurance × 1.05 GST = CA$2.36 ≈ the CA$2.37 observed.

## 2. Root cause

Two independent paths to the fallback, both silent:

1. **The polyline gated the price.** `_fetch_directions_route` returned `None` when the overview polyline was missing or decoded to <2 points — *before* reading `legs[].distance`. Google answering `status: OK` with a perfectly good road distance but no overview geometry threw the distance away and dropped the fare to haversine. The polyline only draws the map line; it has nothing to do with pricing.
2. **The road route lost a race.** The Directions call is awaited for a bounded `_PRICING_ROUTE_WAIT_S` (= `DIRECTIONS_TIMEOUT_S + 0.5`, i.e. 2.0 s). Miss the deadline → `road_km is None` → haversine. This makes pricing non-deterministic: identical inputs bill differently depending on network timing. The AI quote path loses this race more often than the app path because `get_fare_quote` runs pickup-reconcile and dropoff-verification geocodes against the same Maps budget and HTTP client before the estimate.

Neither emitted an error — only a metric — so it ran unnoticed.

## 3. Fix / remediation

- **Decouple price from polyline** (`_shared.py`): parse `legs[].distance`/`duration` regardless of the overview geometry; return `polyline: []` when it is unusable. Only return `None` when there is neither a billable distance nor a drawable line.
- **Give the road route a real chance**: `DIRECTIONS_TIMEOUT_S` 1.5 s → 3.0 s (pricing wait 2.0 s → 3.5 s). Explicit product decision — road route is the billing basis; haversine is a guardrail for a dead upstream, not a second pricing mode.
- **Make the fallback loud** (`estimates.py`): `logger.error` when a road-mode estimate bills `haversine_fallback`, so it is alertable rather than metric-only. Also upgraded the Directions exception log from `warning` to `error` with `exc_info`, per CLAUDE.md's no-silent-swallow rule on money paths.

## 4. Risk & impact on existing functionality

- Blast radius: `_fetch_directions_route` is the single road-distance source. Consumers: `estimates.py` (pricing), `_fetch_directions_polyline` shim, and the polyline readers in `utils/route_distance.py`, `utils/route_finalizer.py`, `utils/trip_distance.py`, `utils/route_reconstruction_projection.py`, `routes/drivers/_shared.py`. **Every one already reads `.get("polyline") or []`**, so `polyline: []` is safe; the shim returns `None` exactly as before. Verified by grep, listed here rather than asserted.
- **Latency is the real trade-off.** Worst case on "tap → price shown" rises from 2.0 s to 3.5 s against a CLAUDE.md P95 target of 300 ms. It costs nothing on a warm call — the route task runs concurrently and usually resolves with no added latency — so this extends only the slow tail, which is precisely the population that was mispricing. The existing ratchet test `test_pricing_wait_stays_within_the_estimate_latency_budget` was updated (2.0 → 3.5) with the rationale recorded in its docstring, deliberately keeping it a ratchet so the next bump is also a conscious decision.
- Direction of the change is **upward** on fares that previously fell back — riders in that tail now pay the correct (higher) road-based price. Not a price increase: it is the price the road distance always implied, and the same figure the normal app flow already charged.
- No schema, state-machine, or client change. `select_fare_distance`, the sanity band (0.95–3.0×), and the estimate-token distance lock are untouched, so quote↔booking consistency is unchanged.

## 5. User-experience effect

Riders whose estimates previously hit the fallback now see the correct, slightly higher road-distance fare — matching what the normal booking flow already showed. A small number of estimates may take up to ~1.5 s longer in the degraded-upstream tail. Drivers stop absorbing the shortfall. No UI change.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/routes/rides/_shared.py` | Polyline no longer gates distance; `DIRECTIONS_TIMEOUT_S` 1.5→3.0; exception log warning→error | Stop discarding valid road distances; let slow-but-valid routes win |
| `backend/routes/rides/estimates.py` | `logger.error` on `haversine_fallback` | Make the undercharge alertable |
| `backend/tests/test_directions_route.py` | 2 regression tests (missing / degenerate polyline keep the distance) | Pin the money bug |
| `backend/tests/test_fare_road_basis_default.py` | Ratchet ceiling 2.0→3.5 with recorded rationale | Keep the deliberate-decision gate |

## 7. Before/after

```python
# before — a missing map line discarded the billable distance
encoded = route.get("overview_polyline", {}).get("points", "")
if not encoded:
    return None
pts = _decode_polyline(encoded)
if len(pts) < 2:
    return None

# after — geometry is optional, distance is not
encoded = route.get("overview_polyline", {}).get("points", "")
pts = _decode_polyline(encoded) if encoded else []
if len(pts) < 2:
    pts = []
```

## 8. Rollback plan

`git revert` — stateless, no schema or persisted state. If the latency proves unacceptable before a revert can ship, `fare_distance_basis` in `app_settings` still switches pricing mode without a deploy (though setting it to `haversine` reinstates the undercharge, so the correct lever is reverting the timeout, not the basis).

## 9. Verification performed

- `pytest -k "estimate or fare or shared or distance"` — 390 passed after the ratchet update (1 pre-existing failure was the ratchet itself, now updated deliberately).
- `pytest tests/test_directions_route.py tests/test_fare_road_basis_default.py` — 17 passed.
- **Confirmed the new tests fail without the fix**: reverted the polyline change in place, watched both `*_still_returns_road_distance` tests fail, restored.
- Full backend suite + `ruff` — see PR.

## 10. What was NOT verified

- **No live Google Directions call** from this environment, so the claim that the reported 15.5 km was specifically a `haversine_fallback` (rather than a road route over different coordinates) is inferred from the arithmetic and the code paths, not observed. `spinr_fare_distance_basis_total{basis="haversine_fallback"}` in production is the direct confirmation and should be checked — it also quantifies the revenue already lost.
- **The 3.0 s timeout is a judgment call, not a measurement.** I have no p99 latency data for Directions from this environment; if the real p99 is well under 1.5 s, then timeouts were not the dominant path and the polyline bug was, in which case the timeout bump is harmless but unnecessary.
- Not tested against live Supabase or a real Maps key; unit suites use mocks.
- No load test of the latency impact on the estimate endpoint.
