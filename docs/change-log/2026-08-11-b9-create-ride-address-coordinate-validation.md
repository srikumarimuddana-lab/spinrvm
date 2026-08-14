# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | (this branch) |
| Related issue or gap ID | `ACTION_ITEMS.md` B9 (explicitly deferred `CreateRideRequest` cross-field validation) |

## 1. Issue / gap identified

`POST /rides` (`create_ride`, `routes/rides/booking.py`) accepted and persisted any `{pickup_address, pickup_lat, pickup_lng, dropoff_address, dropoff_lat, dropoff_lng}` combination with zero cross-field validation — an address label could describe one place while the coordinate pointed at another, and the backend would durably record the mismatched pair on the ride row (the "Glide Crescent wrong-pin incident" class of bug). This was the last explicitly-deferred piece of B9; `POST /addresses` and `POST /favorites` already got the equivalent check in an earlier pass.

## 2. Root cause

No consistency check existed between the free-text address strings and the coordinates a client submits. `validate_ride_location` (called at the top of `create_ride`) only validates coordinate *shape* (lat/lng range, pickup≠dropoff by distance) — it has no knowledge of what the address text says. `CreateRideRequest`'s pydantic field validators (`schemas.py`) are synchronous and cannot make the network call a geocode check requires, so this class of validation was structurally impossible to add as a pydantic validator — it has to live in the route handler.

## 3. Fix / remediation

Added an address↔coordinate consistency check to `create_ride`, reusing the existing `utils/address_verification.py::verify_address_matches_coordinate` helper (already used by `POST /addresses`/`POST /favorites`) rather than writing a new one. Both legs (pickup and dropoff) are checked concurrently via `asyncio.gather` to bound the added latency to one Maps round-trip instead of two. The check is placed **after** the existing geofence gates (pickup/dropoff/stops-inside-service-area) so a request that's already going to be rejected by the free, in-memory polygon check doesn't also spend a paid Maps geocode call.

The check is fail-open by design (same contract as the existing two call sites): it only rejects with a 400 when Google's geocode of the address text is *confident* (ROOFTOP/RANGE_INTERPOLATED, no partial_match) and disagrees with the supplied coordinate by more than 1 km. No API key, exhausted daily Maps budget, network error, or an ambiguous/imprecise geocode all fail open and let the booking proceed unchanged.

## 4. Risk & impact on existing functionality

- **Blast radius: single route handler (`create_ride`), one new guard clause.** No other endpoint calls this function. `verify_address_matches_coordinate` itself is unmodified — this PR only adds two more call sites to an existing, already-shipped helper.
- **Other consumers of `verify_address_matches_coordinate`:** `routes/addresses.py` (`POST /addresses`) and `routes/favorites.py` (`POST /favorites`, via `save_favorite_from_ride`). Neither is touched by this change; the helper function itself is untouched.
- **Maps API budget impact:** every `POST /rides` call now makes up to 2 additional Geocoding API calls (previously 0) — but only for requests that pass the geofence gates first (an out-of-area request no longer reaches this check and is rejected for free, same as before). `utils/maps_budget.py`'s existing daily budget guard applies here exactly as it does for the address/favorites call sites; when the budget is exhausted, this check fails open rather than blocking bookings.
- **Latency impact:** `verify_address_matches_coordinate`'s own HTTP timeout is 3.0s; running both legs concurrently via `asyncio.gather` means the worst case adds ~3s to `create_ride`, not ~6s. This lands inside `create_ride`'s existing sequence of awaited DB/Maps calls (service-area fetch, geofence checks, fare building, dispatch kickoff) — no formal SLA in `CLAUDE.md`'s Performance table names `POST /rides` itself, but the closest downstream constraint (`Dispatch offer → driver phone notification < 2s`) starts its clock *after* `create_ride` returns, so this addition doesn't eat into that budget. Flagging as a real, not hidden, latency cost — not measured against production traffic in this pass (same limitation B6 already documents for the Directions call).
- **State-machine impact:** none. This is a pre-creation guard — on a rejection, no ride row is ever written, no payment hold is placed (the check runs before the vehicle-types/fare-building/preauth block), so there's no partial state to roll back.
- **Test surface checked for regressions** (all passed, no mocking of the new call needed since it fails open safely when unmocked): `test_rides.py`, `test_create_ride_guard_clauses.py`, `test_wav_dispatch.py`, `test_corporate_ride_payment.py`, `test_coverage_rides.py`, `test_admin_rides_coverage.py`, `test_corporate_surge_bypass.py`, `test_p0_ship_blockers.py`, `test_ai_tools_booking.py` — 671 tests, 0 failures, 0 hangs, ~20s combined wall time. Confirmed `get_app_settings()` (which `verify_address_matches_coordinate` calls internally to read the Maps API key) resolves through the same mocked `db_supabase.get_rows` autouse fixture every other test already relies on — no real network call is made in any of these existing, unmodified tests.

## 5. User experience effect

Rider-facing. A rider whose client sends a mismatched address-text/coordinate pair (the bug class this closes) now gets a clear 400 ("Pickup/Dropoff address and location don't match: …") instead of the ride being created against the wrong location — this is a **behavior change** for that narrow case, but it's the intended fix, not a side effect. Not visible mid-session to a rider already on an active ride (this only runs at booking time, before ride creation). No UI copy was added on the backend side; the rider-app would need its own handling of this 400 to surface it well, which is out of scope for this backend-only pass — flagging as a follow-up, not silently assumed to be covered.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/_deps.py` | Added `verify_address_matches_coordinate` to both halves of the dual-import block | Make the existing B9 helper available to the rides-booking package |
| `backend/routes/rides/booking.py` | Added `import asyncio`; added `verify_address_matches_coordinate` to the `_deps` re-export list; added the address↔coordinate guard clause after the geofence gates | Implements B9's deferred `CreateRideRequest` cross-field validation |
| `backend/tests/test_create_ride_guard_clauses.py` | Added 3 tests: pickup-mismatch→400, dropoff-mismatch→400, fails-open-doesn't-block | Regression coverage for the new guard, following this file's existing guard-clause test pattern |

## 7. Before / after

```python
# Before (routes/rides/booking.py, after the stop-geofence gate)
    # Vehicle types are also needed by fare building — fetch once, reuse.
    vehicle_types = await _deps.db_supabase.get_rows("vehicle_types", {"is_active": True}, limit=100)
```

```python
# After
    # Address<->coordinate consistency (B9): reject a confident text/pin
    # mismatch before any fare/dispatch work begins ...
    _pickup_match, _dropoff_match = await asyncio.gather(
        verify_address_matches_coordinate(body.pickup_address, body.pickup_lat, body.pickup_lng),
        verify_address_matches_coordinate(body.dropoff_address, body.dropoff_lat, body.dropoff_lng),
    )
    _pickup_ok, _pickup_mismatch_reason, _ = _pickup_match
    _dropoff_ok, _dropoff_mismatch_reason, _ = _dropoff_match
    if not _pickup_ok:
        raise HTTPException(status_code=400, detail=f"Pickup address and location don't match: {_pickup_mismatch_reason}")
    if not _dropoff_ok:
        raise HTTPException(status_code=400, detail=f"Dropoff address and location don't match: {_dropoff_mismatch_reason}")

    # Vehicle types are also needed by fare building — fetch once, reuse.
    vehicle_types = await _deps.db_supabase.get_rows("vehicle_types", {"is_active": True}, limit=100)
```

## 8. Rollback plan

`git-revert-safe` — the change is additive (a new guard clause) with no data/schema/migration footprint. A plain `git revert` removes the guard clause and its two new imports; no live-data remediation is needed since a rejected booking never wrote a ride row in the first place.

## 9. Verification performed

- [x] Automated tests run: new tests in `test_create_ride_guard_clauses.py` (3 new, 13/13 in that file passing) plus the 671-test wider regression sweep listed in §4 above — all passed via `pytest ... -q --no-cov`.
- [x] `ruff check` on all three touched files — all checks passed.
- [ ] Manual repro against staging/real Supabase — not done; verified against `mock_supabase_client` fixtures per this repo's unit-test convention, consistent with how the original `POST /addresses`/`POST /favorites` B9 work was verified.
- [x] Blast-radius grep performed: confirmed the only other two callers of `verify_address_matches_coordinate` (`routes/addresses.py`, `routes/favorites.py`) are untouched by this diff; confirmed no other route calls `create_ride` directly.
- [x] Reviewed against relevant CLAUDE.md conventions: state-machine (no ride row written on rejection, nothing to roll back), fail-open money-adjacent design (mirrors the already-shipped call sites), dual-import pattern followed for the new `_deps.py` entries.
- [ ] Feature-flagged — not applicable; this is a narrow, additive correctness guard on a bug class already fixed identically at two other endpoints, not a new user-visible feature.

**What was NOT verified:**
- No manual/staging repro against a real Google Maps API key — verification is entirely against mocked/unmocked-but-fail-open paths in the existing test suite, same boundary as the original B9 pass.
- Real-world Maps-budget/latency impact under production traffic is not measured in this pass (same open item as B6 already documents for the Directions call) — the two extra geocode calls per booking are a real, not hidden, cost that should be watched once this ships.
- The rider-app's handling of the new 400 response was not implemented or tested — backend-only change; a poor client-side experience for this specific rejection (e.g., a raw error string instead of a friendly retry prompt) is a real gap this pass doesn't close.
- Full backend suite (`pytest tests/`, all ~9000+ tests) was not run for this change — verified against a targeted 671-test slice covering every ride-booking-adjacent file found via `grep`, not the entire suite.
