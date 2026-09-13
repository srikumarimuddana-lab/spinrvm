# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code (claude-sonnet-5) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | (this commit — see repo HEAD) |
| Related issue or gap ID | ACTION_ITEMS.md C105 |

## 1. Issue / gap identified

`GET /maps/directions` (`backend/routes/maps_proxy.py::get_directions`, R7's dark-launched
client-side map-line proxy) has no result cache, so two clients requesting the same leg around
the same time (e.g. the driver dashboard's origin→pickup fetch and the rider's
`driver-arriving.tsx` driver-leg fetch) each pay for and count a separate Directions API call
against the shared `"directions"` budget bucket.

## 2. Root cause

The endpoint was added for R7 without a cache, unlike its sibling live-route fallback
(`backend/utils/route_distance.py::_compute_route_via_google`), which already caches on a
rounded-coordinate Redis key with a 30s TTL for exactly this dedupe purpose. R7 shipped this way
deliberately — `spinr-performance-sla-reviewer`'s review flagged it as C105 but judged it
acceptable to ship without a cache while `app_settings.directions_proxy_enabled` defaults off and
no environment has fleet-wide traffic through the endpoint. The gap was tracked as "close before
broad rollout," not a launch blocker.

## 3. Fix / remediation

Added a short-TTL Redis result cache to `get_directions`, checked before `_ensure_budget()`/
before any Google call:

- **Cache key**: uniform fine precision on every coordinate — 5 decimals (~1 m grid) for origin,
  destination, and each waypoint — same scheme as `routes/rides/_shared.py::
  _fare_directions_cache_key`. This deliberately does **not** mirror
  `_compute_route_via_google`'s coarse-origin (~110 m) scheme, even though the task that spawned
  this fix (and C105's own write-up) suggested exactly that. Before implementing, I checked all 5
  real client call sites (grepped `fetchDirectionsRoute(` across `rider-app`/`driver-app`) rather
  than take the "origin is generally moving" premise on faith, and it doesn't hold: 3 of the 5 —
  `rider-app/app/ride-options.tsx` (pickup→dropoff route preview, pre-booking), `rider-app/app/
  ride-in-progress.tsx` (pickup→dropoff trip-route line, fetched once per ride ID, explicitly
  *not* tied to GPS pings per its own comment), and `rider-app/app/driver-arrived.tsx`
  (pickup→dropoff) — pass a **fixed, per-ride pickup as the origin**, paired with a fixed
  dropoff. Only `rider-app/app/driver-arriving.tsx` and `driver-app/app/driver/(tabs)/index.tsx`
  pass a genuinely moving driver GPS position as origin.

  Coarsening the origin for the 3 fixed-pickup call sites would let two *different* bookings'
  distinct-but-nearby pickups (e.g. two entrances on the same block, ~50–100 m apart) collide in
  the cache whenever their dropoffs also round together within the TTL window — plausible in
  practice for a popular shared destination (an airport, a downtown office tower). The result
  would be silently serving one rider's confirmed pickup→dropoff route/distance to a different
  rider's screen. `route_distance.py::_compute_route_via_google` can safely coarsen its origin
  because its only caller is a live driver GPS position — a narrower, controlled call graph than
  this generic, externally-facing proxy. `get_directions` has no such guarantee and no way to
  learn from the query params which pattern a given request is, so it uses the safe uniform-fine
  scheme instead. This costs some cache-hit rate for the two genuinely-moving-origin call sites
  (a jittery GPS ping less often re-rounds to the same 5-decimal bucket than it would to a
  3-decimal one) — a pure efficiency trade-off, not a correctness one; those two call sites still
  get a cache hit whenever the driver is stationary or hasn't moved enough to cross a ~1 m
  boundary within the 30s window.

  Waypoints are included in the key in order (order is never optimized, so a different stop
  sequence is a different route and must not collide).
- **TTL**: `_DIRECTIONS_CACHE_TTL_S = 30`, matching `_LIVE_ROUTE_CACHE_TTL_S`.
- **Fail-open**: cache get/set failures are caught, logged at `warning` (`exc_info=False`,
  matching this file's existing `reverse_geocode` cache-write pattern — this module uses stdlib
  `logging`, not loguru, so `exc_info=` is safe here), and the request proceeds exactly as if
  there were no cache.
- **Never cache a degenerate result**: a response with `distance_km: None` or an empty
  `coordinates` list is never written to cache (mirrors the R8 money-auditor finding in
  `_shared.py::_fetch_directions_route` — a stale bad entry would otherwise keep being served for
  the whole TTL window).
- **Response shape**: added a `"cached": bool` field, matching this file's `reverse_geocode`
  convention (the other caching endpoint in this module). `places_autocomplete`/`places_details`
  don't cache results at all, so they have no such field — not a relevant precedent here.

Explicitly out of scope (per the task and C105's own write-up): `_HTTP_TIMEOUT` is unchanged.

**Alternative considered:** an asymmetric key — coarse (~110 m) origin, fine (~1 m) destination —
mirroring `_compute_route_via_google`, as the originating task instructions and C105's own
write-up both suggested. Rejected after checking the real call sites (see above): the premise
that this endpoint's origin is "generally" a moving position turned out to be true for only 2 of
5 call sites, and false for the other 3 in a way that creates a real cross-booking collision
risk, not just a minor precision nit. This was caught by a mandatory adversarial review pass
(CLAUDE.md's pre-merge gate, rule 10) run against the diff before committing — the review traced
the design's own stated assumption against the actual `rider-app`/`driver-app` code and found it
didn't hold for `ride-options.tsx`. Uniform fine precision (this fix's chosen approach) trades a
small amount of cache-hit rate on the two moving-origin call sites for correctness across all 5;
given the failure mode of the coarse alternative is a wrong route silently shown to a rider (not
a crash or a 5xx), correctness won.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `get_directions` has no other backend callers — grepped
  `backend/` for `get_directions`/`maps_proxy` and found only `backend/server.py` (mounts the
  router), the two existing test files (`test_maps_proxy.py`, `test_maps_proxy_coverage.py` —
  the latter doesn't exercise `get_directions`), and unrelated comments in
  `backend/ai/tools_booking.py`/`backend/routes/admin/rides.py` referencing the *pattern*, not
  calling this function. The only real callers are the 5 client-side call sites in rider-app/
  driver-app enumerated in §3 (out of scope — this is a backend-only cache, transparent to them
  beyond the new `cached` field, which they don't need to read).
- **Shares the `"directions"` Maps budget bucket** with `_fetch_directions_route` (fare-estimate,
  R8) and `_compute_route_via_google` (live-route fallback) — this change only *reduces* calls
  against that shared budget (cache hits skip `record_call` entirely), it does not add a new
  consumer or change how the budget is computed.
- **No interaction with the ride state machine, wallet/allowance deltas, or Stripe flows** — this
  is a read-only map-rendering proxy, not a pricing or dispatch path. `distance_km`/
  `duration_minutes` returned here are informational for the client-side polyline fallback only;
  they do not feed fare calculation (that's `_fetch_directions_route` in `_shared.py`, a
  separate cache with separate precision, untouched by this change).
- **Existing cache-key collision risk with `_compute_route_via_google` or
  `_fetch_directions_route`**: none — each uses its own key prefix (`maps_directions:` vs.
  `live_route:google:` vs. `fare_directions:google:`), so no cross-contamination between the
  three Directions caches in the codebase.
- A pre-existing, out-of-scope race (C104, referenced from the same C105 finding) exists in
  `check_budget`/`record_call` being non-atomic — this change does not worsen it (a cache hit
  skips both calls entirely, which if anything narrows the window slightly) and does not attempt
  to fix it; tracked separately.

## 5. User-experience effect

None visible to riders/drivers. This is a backend-only proxy behind `app_settings.
directions_proxy_enabled`, which is off by default; the endpoint's response shape gained one new
field (`cached`) that no shipped client currently reads. No screen's behavior, timing, or copy
changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/maps_proxy.py` | Added `_DIRECTIONS_CACHE_TTL_S`/`_DIRECTIONS_CACHE_PRECISION` constants, a `_directions_cache_key()` helper, and cache read/write around `get_directions`'s Google call; added `"cached"` field to the response; added `import json` | Close C105 — dedupe concurrent viewers of the same leg, matching the sibling live-route cache |
| `backend/tests/test_maps_proxy.py` | Updated the existing exact-dict assertion in `test_directions_decodes_route_and_records_budget_call` to include `"cached": False`; added 6 new tests covering cache hit/miss, degenerate-result non-caching, waypoint-order key differentiation, and fail-open on cache read/write errors | Cover the new caching behavior per this task's testing requirements |
| `ACTION_ITEMS.md` | Marked C105 `[x]` CLOSED with a pointer to this commit/CIL | Close out the tracked finding |
| `docs/change-log/2026-09-12-directions-proxy-cache-c105.md` | New file (this document) | Mandatory Change Impact Log for a change touching a live-tested (dark-launched) backend endpoint |

## 7. Before / after

```python
# Before
async def get_directions(...):
    """..."""
    await _ensure_budget()
    api_key = await _maps_key()

    o_lat, o_lng = _parse_latlng(origin, "origin")
    d_lat, d_lng = _parse_latlng(destination, "destination")

    params: dict = {
        "origin": f"{o_lat},{o_lng}",
        "destination": f"{d_lat},{d_lng}",
        "key": api_key,
    }
    if waypoints:
        stops = [_parse_latlng(pair, "waypoints") for pair in waypoints.split("|")]
        params["waypoints"] = "|".join(f"{lat},{lng}" for lat, lng in stops)

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(..., params=params)
            data = resp.json()
    except Exception as e:
        ...
    await record_call("directions")
    ...
    return {
        "coordinates": coordinates,
        "distance_km": ...,
        "duration_minutes": ...,
    }
```

```python
# After
async def get_directions(...):
    """... (docstring extended to describe the cache) ..."""
    o_lat, o_lng = _parse_latlng(origin, "origin")
    d_lat, d_lng = _parse_latlng(destination, "destination")
    stops = [_parse_latlng(pair, "waypoints") for pair in waypoints.split("|")] if waypoints else []

    cache_key = _directions_cache_key(o_lat, o_lng, d_lat, d_lng, stops)
    try:
        cached = await redis_get(cache_key)
        if cached:
            return {**json.loads(cached), "cached": True}
    except Exception:
        logger.warning("[maps_proxy] directions cache get failed", exc_info=False)

    await _ensure_budget()
    api_key = await _maps_key()
    params: dict = {"origin": f"{o_lat},{o_lng}", "destination": f"{d_lat},{d_lng}", "key": api_key}
    if stops:
        params["waypoints"] = "|".join(f"{lat},{lng}" for lat, lng in stops)

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(..., params=params)
            data = resp.json()
    except Exception as e:
        ...
    await record_call("directions")
    ...
    result = {"coordinates": coordinates, "distance_km": ..., "duration_minutes": ...}
    if result["distance_km"] is not None and coordinates:
        try:
            await redis_set(cache_key, json.dumps(result), ttl=_DIRECTIONS_CACHE_TTL_S)
        except Exception:
            logger.warning("[maps_proxy] directions cache set failed", exc_info=False)
    return {**result, "cached": False}
```

## 8. Rollback plan

Purely additive, code-only change with no migration and no data written outside a 30-second-TTL
Redis cache key. To roll back:
- `git revert` this commit and redeploy — safe here (unlike a money/state-machine change) because
  nothing durable is written; the worst a bad revert-window leaves behind is up to 30s of stale
  cached route entries, which self-expire.
- No redeploy needed for an emergency mitigation either: the endpoint is already gated by
  `app_settings.directions_proxy_enabled` (defaults off) — flipping that off in the admin
  dashboard stops all traffic to `get_directions` (cached or not) without touching this commit at
  all.

## 9. Verification performed

- [x] Automated tests run (unit, mocked Redis/httpx): `pytest backend/tests/test_maps_proxy.py -v`
      — 27 passed (21 pre-existing + 6 new), 0 failed. Also ran
      `pytest backend/tests/test_loguru_call_conventions.py -v` (8 passed) to confirm
      `maps_proxy.py`'s stdlib-`logging` `exc_info=False` usage isn't a loguru-convention
      violation (it isn't — this module never imports loguru).
- [ ] Manual repro steps followed in staging — not done; no staging environment access from this
      session.
- [x] Blast-radius grep performed: searched `backend/` for `get_directions`/`maps_proxy` (see
      §4) — only `server.py`'s router mount and the two test files are real backend callers.
- [x] Reviewed against relevant `CLAUDE.md` conventions: Redis fail-open pattern (mirrors
      `_fetch_directions_route`/`_compute_route_via_google`), query-filter escaping rules (not
      applicable — no `db_supabase` filter dicts touched), loguru gotcha (not applicable — stdlib
      `logging`, confirmed via the loguru-convention test run above).
- [x] Feature-flagged: no new flag needed — the endpoint is already behind
      `app_settings.directions_proxy_enabled`, and this change doesn't alter the response shape
      for any currently-shipped client (added field, not a changed/removed one).
- Lint/format: `ruff check backend/routes/maps_proxy.py backend/tests/test_maps_proxy.py` —
  all checks passed. `ruff format --check` on both files — already formatted, no diff.
- [x] Adversarial review before commit (CLAUDE.md rule 10): ran `/code-review` at medium effort
  against the full working-tree diff. It found the asymmetric-precision design (the first draft
  of this fix, following the originating task's own suggestion) had a real cross-booking
  collision risk on 3 of 5 real call sites — see §3's "Alternative considered." Fixed by
  switching to uniform fine precision before this commit; all 27 tests re-run and green after the
  fix.
- Real production build: **not applicable** — this is a `backend/` (Python) change, not
  `admin-dashboard`/`rider-app`/`driver-app`; there is no `npm run build` step for this surface.

## 10. What was NOT verified

- Not tested against a real Redis instance or real Google Directions API — only
  `mock_redis`/mocked `httpx.AsyncClient` fixtures, per this repo's standard unit-test
  conventions. No integration or staging check was run.
- Not exercised under real concurrent load — the "dedupe concurrent viewers" benefit is inferred
  from the cache mechanism (same TTL/pattern as the already-shipped `_compute_route_via_google`
  and `_fetch_directions_route` caches), not measured against a live burst of near-identical
  requests.
- `_HTTP_TIMEOUT` (separately flagged in C105 as a maybe-later item) was explicitly left
  unchanged, as instructed — not evaluated here.
- No visual/UI surface touched (backend-only), so the visual-regression disclosure in
  `CLAUDE.md` §6 doesn't apply to this change.

## Sign-off

- [x] Rollback plan is concrete and testable (flag flip or plain revert; no data-level concern)
- [x] Blast radius is stated, not assumed (isolated — see §4)
- [x] No silent behavior change to an already-shipped flow: the endpoint is dark-launched and
      off by default; the new `cached` field is additive, not a removal/rename of an existing one
