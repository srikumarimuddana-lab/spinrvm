# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code (audit follow-through, roadmap item R8) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | srikumarimuddana-lab/spinrvm#5290 |
| Related issue or gap ID | `docs/audit/ride-experience/ROADMAP.md` R8, source REC-D-01 part 2 |

## 1. Issue / gap identified

`_fetch_directions_route` (`backend/routes/rides/_shared.py`) — the source of the road
distance every fare estimate is priced on — had no cache. R2 (earlier this session) added
budget accounting to this call site, but every `/rides/estimate` call still hit Google
directly, including repeated calls for the same pickup/dropoff pair during a single booking
session (e.g. a rider re-opening the ride-options screen, or the client re-quoting after a
transient error).

## 2. Root cause

This call site predates `route_distance.py`'s live-route cache and was never given an
equivalent. Caching it required a different key scheme than the existing sibling cache, which
is likely why it wasn't simply copied over: `route_distance.py`'s cache rounds *origin* to a
~110m grid because it serves a moving driver's imprecise GPS position — reusing that scheme
here would let two riders whose pins are ~100m apart get billed on the same cached road
distance, since this call site's endpoints are fixed, rider-chosen, and directly determine the
bill.

## 3. Fix / remediation

Added a Redis cache to `_fetch_directions_route`, keyed at **5 decimals (~1m) on every
coordinate** (pickup, dropoff, and each waypoint if present — waypoint order is part of the
key since a different stop sequence for the same origin/destination is a different route),
TTL **30 seconds**. Cache is checked before the budget gate (so a hit costs nothing against
the daily breaker) and populated only on a genuinely successful response (a budget-exhausted,
non-OK-status, or exception path is never cached, so a transient Google failure doesn't get
served back to the next caller for 30 seconds). A new `_fare_directions_cache_key()` helper
builds the key; `_FARE_DIRECTIONS_CACHE_TTL_S` / `_FARE_DIRECTIONS_CACHE_PRECISION` are the two
tunables, both documented in-line with the reasoning for why they must not match
`route_distance.py`'s constants.

Redis errors (get or set) are caught and logged as warnings, never raised — a Redis outage
degrades this function to exactly its pre-cache behavior (always call Google), the same
defensive pattern `route_distance.py`'s own cache already uses.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one function.** `_fetch_directions_route` has three callers in
  the codebase: `estimates.py`'s fare-estimate path (the primary target of this fix),
  `booking.py`'s safety-net re-derive when no valid estimate token is present, and
  `_fetch_directions_polyline` (a thin wrapper with its own callers, unaffected beyond
  inheriting the cache). Grepped for every caller before changing the function — no other
  reader of `_shared.py`'s `redis_get`/`redis_set`/`json` imports exists (both were added
  fresh to this file for this change), so nothing else could be affected by the import
  addition itself.
- **Why this does not weaken the fare lock (corrected per adversarial review, see §9):** the
  estimate-token mechanism (`sign_estimate_token` / `resolve_booking_distance`,
  `estimates.py:580`) already pins the exact quoted distance for the token-present booking
  path at the moment a quote is shown — that value is what actually gets charged for that
  path, independent of anything this cache does. **One charge-determining path does read this
  cache**, corrected from an earlier draft of this section that said "never": `booking.py`'s
  no-token safety-net re-derive (`booking.py:815`, used when a booking confirm arrives without
  a valid estimate token) calls this exact function and bills directly on its `distance_km`.
  In the healthy case this is harmless — a cache hit there bills the same distance an earlier
  `/rides/estimate` call already showed the rider, which is arguably *more* consistent than an
  independent fresh call. The risk this review caught and the fix (§3/§9) closes: a
  `distance_km=None` result must never be cached, or that safety-net path could inherit a
  stale null-distance answer from an unrelated earlier estimate call and silently bill
  haversine instead of the road route.
- **Could this regress a working flow?** No known regression path. The one behavior change
  visible to a caller: two `/rides/estimate` calls for the same pickup/dropoff (to 5 decimals)
  within 30 seconds now get the exact same road-distance response instead of two independent
  (but numerically identical, since Google's routing for a fixed pair is itself deterministic)
  Google responses. This is a latency/cost improvement, not a correctness change.
- **Interaction with the ride state machine / money paths:** none — this function only
  supplies a distance figure into fare calculation; it does not read or write `rides.status`,
  wallet state, or Stripe. No background loop reads this cache.

## 5. User-experience effect

- **Who sees a difference:** nobody directly. A cache hit is faster than a live Google call
  (no ~100-300ms round trip), which can only help the fare-estimate P95 SLA (< 300ms target),
  never hurt it — and only on the fraction of calls that hit cache.
- **Visible mid-session?** No new visible behavior — the rider still sees a fare estimate at
  the same call site, just potentially faster on a repeat quote for the same pins.
- **Copy/notification change:** none.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/_shared.py` | Added `_fare_directions_cache_key()`, `_FARE_DIRECTIONS_CACHE_TTL_S`/`_FARE_DIRECTIONS_CACHE_PRECISION`, and cache read/write around `_fetch_directions_route`'s existing Google call. | R8 |
| `backend/tests/test_directions_route.py` | Added an autouse `_isolated_directions_cache` fixture (existing tests in this file reuse the same fixed coordinates, so a shared in-process cache would leak state between them) and a new `TestFareDirectionsCache` class (6 tests: cache hit skips HTTP+budget, cache miss then hit, distinct nearby pins don't collide, waypoints change the key, Redis get/set failures both degrade gracefully). | R8 |

## 7. Before / after

```python
# Before
async def _fetch_directions_route(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng, api_key, waypoints=None):
    if not api_key:
        return None
    allowed, spent, budget = await check_budget()
    if not allowed:
        return None
    # ... always calls Google ...
```

```python
# After
async def _fetch_directions_route(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng, api_key, waypoints=None):
    if not api_key:
        return None
    cache_key = _fare_directions_cache_key(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng, waypoints)
    try:
        cached = await redis_get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        logger.warning("_fetch_directions_route: cache get failed", exc_info=False)
    allowed, spent, budget = await check_budget()
    if not allowed:
        return None
    # ... calls Google only on a cache miss ...
    # ... on success: await redis_set(cache_key, json.dumps(result), ttl=30) ...
```

**Concrete before/after scenario (the pin-drag repeat-quote case the roadmap named):** a rider
opens ride-options, drags their pickup pin slightly and back within the same ~1m cell twice in
5 seconds (a slow drag, or the client re-quoting after a UI hiccup). *Before*: two live Google
Directions calls, two budget-breaker decrements, ~$0.01 combined spend, two ~100-300ms round
trips. *After*: one live call on the first quote, the second quote (same 5-decimal pickup/
dropoff) served from the 30-second cache — zero additional Google spend, zero additional
budget-breaker decrement, and a faster response for the rider.

## 8. Rollback plan

`git revert` is sufficient — the change is purely additive around an existing call (no schema,
no migration, no data mutation). There is no feature flag: this is unconditional for every
call, matching `route_distance.py`'s own unconditional live-route cache (its precedent already
established caching this class of call as safe-by-default, not something needing dark-launch).
If the cache itself were ever suspect (e.g. a key-collision bug discovered later), the fastest
mitigation short of a revert is manually flushing the `fare_directions:google:*` key prefix
from Redis — no code change needed, since a fresh cache simply refills from live calls again.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_directions_route.py
  backend/tests/test_ride_estimate_branches.py backend/tests/test_estimate_token.py
  backend/tests/test_estimate_intent_projection.py
  backend/tests/test_estimate_ghost_driver_filter.py backend/tests/test_rides_extended.py
  backend/tests/test_e2e_ride_lifecycle.py backend/tests/test_create_ride_post_insert_branches.py
  -m "not slow" --no-cov -q` → 102 passed (21 in `test_directions_route.py` alone, including
  the 6 new cache-specific tests).
- [x] `ruff check` + `ruff format --check` on both changed files → clean.
- [x] Blast-radius grep performed: confirmed `_fetch_directions_route`'s only 3 callers
  (`estimates.py`, `booking.py`'s safety-net re-derive, `_fetch_directions_polyline`'s wrapper)
  before changing it; confirmed no other test file calls the real (unmocked) function with
  assumptions about call-count that a cache would break — `test_create_ride_post_insert_branches.py`
  mocks it out entirely, unaffected.
- [x] Dry run against `mock_supabase_client`-style fixtures per pre-merge gate 4: this
  function has no direct Supabase dependency (it's a pure Google-Directions-call helper), so
  the equivalent dry run here is the new `TestFareDirectionsCache` suite exercising the cache
  hit/miss/failure paths directly against the real `_local` in-process Redis fallback
  (`redis_client.py`'s dict-backed store when `REDIS_URL` is unset) — the same mechanism
  production uses when Redis is unconfigured, and the same code path the real Redis client
  hits when configured (only the storage backend differs).
- [x] Reviewed against relevant CLAUDE.md conventions: dual-import pattern for the new
  `redis_client` import; money-path error handling (Redis failures logged and degraded, never
  silently swallowed into a wrong fare — the underlying Google-call path and its own error
  handling are completely untouched by this change).
- [x] Adversarial post-implementation review (CLAUDE.md gate #10): `spinr-money-auditor` run
  against the actual diff. **One BLOCKER found and fixed before this landed**: the original
  cache-write guard (`if distance_km is None and not pts: return None`) only skipped caching
  when *neither* a distance *nor* a polyline existed — it did not skip caching a response that
  decoded a polyline but hit the malformed-leg fallback (`distance_km=None`). Since
  `booking.py`'s no-token safety-net re-derive bills directly on this function's
  `distance_km`, that gap could have let a stale null-distance cache entry silently force a
  later booking confirm onto the haversine fallback — the exact undercharge class the
  surrounding code comments already warn about. **Fixed** by gating the `redis_set()` call
  itself on `distance_km is not None`, and added
  `test_none_distance_result_is_never_cached` pinning the fix. The reviewer also flagged this
  CIL's own §4 (an earlier draft claimed the cache "never" affects quote-to-charge
  consistency) as incomplete — corrected in §4 above. Two non-blocking items verified clean:
  the 5-decimal precision claim (confirmed correct at Saskatoon's latitude), and
  waypoint/order/rounding consistency between the cache key and the real Google request.
- [x] Adversarial pre-implementation review (CLAUDE.md gate #10): alternative considered —
  reuse `route_distance.py`'s existing `_compute_route_via_google`/cache helper directly
  instead of writing a new one — rejected because that function's cache key rounds origin to a
  ~110m grid by design (correct for a moving driver, wrong for two fixed rider-chosen
  endpoints that directly set the bill), and its result shape (`eta_seconds`+`distance_km`,
  capped/re-encoded polyline) doesn't match `_fetch_directions_route`'s contract
  (`duration_s`+full polyline, legs summed for multi-stop). A shared cache *primitive*
  (get/set/key-building) was still avoided in favor of one small dedicated key function,
  matching the roadmap's own explicit instruction not to copy the other cache's key scheme.

### What was NOT verified

- No production/staging observation of actual cache hit-rate or spend reduction — this is a
  fresh, unreleased change; the "negative spend delta" the roadmap predicts is a design
  expectation (fewer redundant calls during a booking session), not yet a measured number.
- Concurrent-request race on the same cache key (two simultaneous identical estimate calls
  both missing cache and both calling Google) was not specifically tested — this is a
  performance/cost duplication risk, not a correctness one (both calls would return the same
  answer), and is the same class of gap C104 already tracks for `maps_budget.py`'s
  check-then-increment pattern more broadly; not a new risk this change introduces.

### Post-merge addendum: CI caught a real defect this session's own verification missed

`backend/tests/test_loguru_call_conventions.py::test_no_exc_info_kwarg_in_loguru_calls` — a
codebase-wide static scanner CLAUDE.md documents (checked for in this session's other loguru
edits, but not re-run as part of R8's own verification sweep before commit) — failed CI on this
commit: the two new cache-failure `logger.warning(...)` calls above passed `exc_info=False`, a
stdlib-logging kwarg loguru-bound loggers don't support (silently swallowed as an unused
`str.format` keyword, per CLAUDE.md's documented gotcha). Functionally harmless — no traceback
was ever captured either way, since neither call opted into one — but it violates the repo's own
lint-equivalent guardrail and would have shipped past `ruff check` undetected (this rule is
pytest-only, not a ruff rule). **Fixed** by removing `exc_info=False` from both lines (behavior-
identical; the annotation was never doing anything). Verified via
`pytest tests/test_loguru_call_conventions.py` (8/8 passing) and a full re-run of
`test_directions_route.py` (22/22).
**Process note:** this session's R8 verification ran the targeted test files, `ruff check`,
and a broader dispatch/estimate/ride sweep, but never the full backend suite — the one check
that would have caught this before it reached CI. Recorded here rather than silently amended,
since it's a real gap in what "verified" meant for this change at commit time.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, or a Redis key-prefix flush with
      no code change).
- [x] Blast radius is stated, not assumed (§4 — all 3 callers of the cached function traced).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 —
      the only visible effect is faster repeat quotes, stated explicitly).
