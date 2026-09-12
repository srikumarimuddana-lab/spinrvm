# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | PR A of Phase 1, `docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md` §7.3 — first-party in-app turn-by-turn navigation. |

## 1. Issue / gap identified

Driver-app has no turn-by-turn maneuver data available anywhere — every routing call (OSRM and Google Directions alike) requests or reads only polyline + distance/duration, never per-step instructions. There is no backend endpoint a client could poll for "what's the next turn."

## 2. Root cause

Not a regression — a gap. This is new capability, not a fix to existing behavior. (Included in the Change Impact Log per CLAUDE.md's gate for a "new gap closed," and because this is Phase 1 of an already-reviewed proposal touching a live-tested surface — rides.)

## 3. Fix / remediation

- **`backend/schemas.py`**: added `driver_turn_by_turn_enabled: bool = False` to `AppSettings` — the feature flag, off by default (dark-launch).
- **`backend/utils/route_distance.py`**: added `compute_navigation_steps(from_lat, from_lng, to_lat, to_lng)` — a new, standalone Google Directions call with `steps=true`, **deliberately not part of** `compute_route()`'s OSRM-first/Directions-fallback chain used by the 6s-polled `/live-route` line (see the function's own doc comment for why: OSRM's step quality is rougher, and baking `steps=true` into a 6s-polled call would multiply Directions call volume by that cadence). Budget-gated via the existing `check_budget()`/`record_call()` (same mechanism `_compute_route_via_google` already uses), Redis-cached 5 minutes on the origin/destination coordinate pair (cross-ride/cross-viewer dedupe, same shape as `_compute_route_via_google`'s own cache). Parses `legs[].steps[]` into `{instruction, maneuver, distanceMeters, startLocation, endLocation}[]`, sanitizing Google's `html_instructions` (raw HTML meant for a webpage) via a small tag-strip + entity-unescape helper (`_strip_html_instructions`).
- **`backend/routes/rides/tracking.py`**: added `GET /{ride_id}/navigation-steps` — same auth/ownership/leg-selection pattern as the existing `/live-route` endpoint (rider or assigned driver, admin allowed; pickup leg pre-trip, dropoff leg in-trip). Returns `{"steps": [...], "destination": "pickup"|"dropoff"|null}`, empty while the flag is off or there's no active leg/live driver position. Owns its own ride+leg-scoped Redis cache (`nav_steps_leg:{ride_id}:{destination}`, 30 min TTL) — **deliberately keyed on ride+leg alone, not the driver's live position** like `/live-route`'s cache, since the step list for a leg is fixed once the leg starts (PR B's frontend tracks progress against it locally) and re-deriving it on every few metres of GPS movement would be wasteful and would keep resetting step-index tracking. Accepts an optional `force_refresh` query param that bypasses this ride-scoped cache (not the budget check) — the hook PR D will use for a genuine off-route recalculation.
- **`backend/routes/rides/__init__.py`**: registered `get_navigation_steps` alongside the existing `get_live_route` re-export.

## 4. Risk & impact on existing functionality

- **Blast radius**: `AppSettings` is read by `get_app_settings()` everywhere in the backend, but a new field with a schema default is additive — every existing caller that doesn't know about `driver_turn_by_turn_enabled` is unaffected (confirmed: `get_app_settings()` merges schema defaults with the DB row, so no migration or DB write is required for the flag to exist and default to `False`).
- **`compute_navigation_steps` is a new, isolated function** — it does not modify `compute_route`, `_compute_route_via_google`, or any other existing routing path. Grepped: no other call site references it yet (only the new endpoint).
- **New endpoint, new route** — does not modify `/live-route` or any other existing `rides` route. The router registration change (`__init__.py`) is purely additive (one more name in an import list and `__all__`).
- **Budget impact**: this endpoint is a new consumer of the shared $5/day cross-SKU Maps budget circuit breaker (`maps_budget.py`), same one `/live-route`'s Google Directions fallback already shares with autocomplete/geocode/etc. Call volume is bounded by design: once per ride leg (cached 30 min ride-scoped, 5 min cross-ride), not on any poll cadence — unlike `/live-route`'s 6s poll, this endpoint's own natural call rate from a client would only be "once per screen mount," and the ride-scoped cache absorbs any repeat polling from either rider or driver clients.
- **PII**: request/response carries only route-geometry coordinates already present elsewhere in this same domain (pickup/dropoff/driver position) — no new PII surface. Logs use the existing `logger.warning`-on-failure pattern already used throughout `route_distance.py`, no raw coordinates logged at error level (mirrors the existing `/live-route` cache's own PIPEDA-conscious hashing discipline — this endpoint's cache keys are coordinate-rounded, not raw-position-hashed, since unlike `/live-route` they don't need to track continuous driver movement).

## 5. User-experience effect

**None yet, by design.** The flag defaults to `False`; the endpoint returns an empty `steps` array while off, and no frontend code in this PR calls it (PR B/C/D do). This PR is pure backend groundwork.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/schemas.py` | Added `driver_turn_by_turn_enabled: bool = False` to `AppSettings` | Feature flag, dark-launch default |
| `backend/utils/route_distance.py` | Added `compute_navigation_steps()`, `_strip_html_instructions()`, `_NAV_STEPS_CACHE_TTL_S` | Budget-gated, cached Google Directions `steps=true` fetch, decoupled from the 6s live-route poll |
| `backend/routes/rides/tracking.py` | Added `GET /{ride_id}/navigation-steps`, `navigation_steps_cache_key()`, `NAV_STEPS_CACHE_PREFIX`/`NAV_STEPS_CACHE_TTL_SECONDS` | New endpoint, ride+leg-scoped cache layer |
| `backend/routes/rides/__init__.py` | Registered `get_navigation_steps` | Makes the new endpoint importable/testable the same way `get_live_route` is |
| `backend/tests/test_navigation_steps.py` | New: 14 tests across both layers (route_distance-level: parsing/sanitizing/budget/cache; endpoint-level: flag-off, leg selection, ride-scoped caching independent of position, force_refresh, failed-fetch-not-cached) | Proves the new code paths; mirrors `test_compute_route_fallback.py` and `test_live_route.py`'s own established patterns |

## 7. Before / after

```python
# Before — no way to get turn-by-turn data from the backend at all.
```

```python
# After — a new, separately-cached endpoint
@router.get("/{ride_id}/navigation-steps")
async def get_navigation_steps(ride_id: str, force_refresh: bool = False, ...):
    app_settings = await get_app_settings() or {}
    if not app_settings.get("driver_turn_by_turn_enabled"):
        return {"steps": [], "destination": None}
    # ... same auth/leg-selection as /live-route ...
    steps = await compute_navigation_steps(o_lat, o_lng, dest_lat, dest_lng)
    return {"steps": steps or [], "destination": destination}
```

## 8. Rollback plan

`git-revert-safe` — additive schema field (no migration, no data written), additive function, additive endpoint, additive router registration. Setting `driver_turn_by_turn_enabled` back to `False` (or simply not enabling it — it's off by default) fully disables the new behavior without any code change; a full `git revert` is also safe since nothing here is read by any other existing code path.

## 9. Verification performed

- [x] New unit/integration tests: `backend/tests/test_navigation_steps.py` — 14/14 passing, covering both `compute_navigation_steps` (parsing, sanitization, no-API-key, budget-exhausted, cache-hit, provider-error) and the endpoint (flag-off, pickup/dropoff leg routing, empty-when-inactive, empty-when-no-position, ride-scoped-cache-ignores-position-change, force_refresh, failed-fetch-not-cached-and-retried).
- [x] Existing regression tests re-run clean: `test_live_route.py` (8), `test_compute_route_fallback.py` (7), `test_route_distance_osrm.py` (27), `test_ai_admin_settings.py` (13, schema-adjacent) — all passing, confirming this PR's additions don't disturb the existing routing/settings paths.
- [x] `ruff check` and `ruff format --check` clean on all changed files (this repo's CI enforces both).
- [x] Targeted subset of the broader suite (`-k "ride or route or settings or navigation or app_settings"`, excluding `tests/rls` and `tests/direct_pool` which need infrastructure this environment doesn't have) run as a broader smoke pass.
- [ ] Full backend suite (`pytest -m "not slow"`) — run in the background due to its size (~15k tests); result to be confirmed before this PR is marked ready.

**What was NOT verified:** a real Google Directions API call with `steps=true` against production Google Maps Platform — this environment has no live API key, so `compute_navigation_steps`'s HTTP-layer behavior is verified against a faked HTTP client with a hand-built response body, not a real API round-trip. The response shape assumed (`legs[].steps[].html_instructions/maneuver/distance/start_location/end_location`) matches Google's publicly documented Directions API response format but was not cross-checked against a live call. No admin-dashboard UI was built to toggle the new flag — it's toggleable via the existing generic `AppSettings`-backed admin settings mechanism (`routes/admin/settings.py`) without additional code, but that mechanism itself was not exercised end-to-end for this specific new field in this pass.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flag stays off by default; `git revert` safe regardless)
- [x] Blast radius is stated, not assumed (grepped: `AppSettings` merge is additive-safe; `compute_navigation_steps` and the new endpoint have no other callers yet)
- [x] No silent behavior change to an already-shipped flow (§5: zero visible effect while the flag is off, which it is by default)
