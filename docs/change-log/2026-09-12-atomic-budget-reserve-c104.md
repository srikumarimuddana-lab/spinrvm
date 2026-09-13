# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code (session `session_01Aq7xVA8zptrqFwExvqVrwN`) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | (this session's follow-up batch, branch `mvapps/dreamy-faraday-2wz914`) |
| Related issue or gap ID | ACTION_ITEMS.md C104 |

## 1. Issue / gap identified

`backend/utils/maps_budget.py`'s daily-spend circuit breaker is a non-atomic
check-then-act pair: `check_budget()` reads today's estimated spend and
compares it to the daily cap, then (after the caller does its paid Google
Maps work) `record_call()` increments the spend counter. Concurrent requests
can all read a stale "under budget" total before any of them records its own
spend, so a burst can push total spend past the daily cap by more than one
call's worth before the breaker actually trips.

## 2. Root cause

`check_budget()` and `record_call()` were always two separate Redis
operations, not one atomic step — a plain read-then-later-write pattern, not
a compare-and-set. This predates every current caller; it was never a
regression, just never closed.

## 3. Fix / remediation

Added `reserve_budget(sku)` to `backend/utils/maps_budget.py`: a single
atomic Redis Lua script (run via the existing `redis_eval()` helper) that
increments the target SKU's counter, sums estimated spend across every
tracked SKU, and rolls the increment back if the new total exceeds the daily
budget — all in one Redis-side atomic step, so at most one caller's worth of
spend can ever overshoot the cap, instead of an unbounded burst.

**Alternative considered:** a plain per-SKU `INCR` with no Lua script —
rejected because the actual invariant this breaker enforces is *combined*
spend across all 7 SKUs against one shared daily budget, which a single-key
increment cannot answer on its own; splitting the budget into 7 independent
per-SKU caps would be a bigger design change than this race fix warrants.
The Lua script keeps the existing single-shared-budget design intact while
closing the atomicity gap.

**Scope of this fix, deliberately partial:** `check_budget()`/`record_call()`
are shared primitives with ~8 call sites across `backend/routes/maps_proxy.py`
(4 endpoints), `backend/ai/tools_booking.py` (3 sites), `backend/utils/maps_eta.py`
(R4's Distance Matrix fallback), and `backend/routes/rides/_shared.py` (R2/R8's
fare-estimate call). Migrating all of them in one change would exceed
CLAUDE.md's task-decomposition guidance (`> 5 files → decompose first`). This
fix migrates only `_shared.py`'s `_fetch_directions_route` — the highest-volume
Directions call site in the app, and the one ACTION_ITEMS.md C104's own
addendum specifically named as needing coverage. The remaining call sites are
filed as a clean, mechanical follow-up (same primitive already exists; each
site just needs its `check_budget()`/`record_call()` pair swapped for one
`reserve_budget()` call) — see the updated C104 entry in `ACTION_ITEMS.md`.

## 4. Risk & impact on existing functionality

- **Other readers/writers of the same primitive:** `check_budget()` and
  `record_call()` remain unchanged and still used by every call site not
  migrated in this change (`maps_proxy.py` ×4, `tools_booking.py` ×3,
  `maps_eta.py` ×1) — none of them are touched or affected by this fix.
  `reserve_budget()` is new, additive code; nothing existing calls it except
  the one migrated call site.
- **Blast radius: isolated.** Only `backend/utils/maps_budget.py` (new
  function, additive) and `backend/routes/rides/_shared.py` /
  `backend/routes/rides/_deps.py` (the one call site's gating call swapped)
  changed. Grepped both `check_budget` and `record_call` repo-wide to confirm
  no other caller of `_shared.py`'s `_fetch_directions_route` gating logic
  exists beyond the one function body edited.
- **Behavior change, disclosed:** the reservation now happens *before* the
  Google Directions HTTP call (not after, like `record_call()`'s old
  placement). A request that never reaches Google at all (e.g. a connection
  error before the HTTP call completes) now still counts toward today's
  spend estimate — a minor over-count, never an under-count. This matches
  the breaker's own documented bias ("overestimating spend trips early
  (safe); underestimating lets real spend hide past the ceiling") and is
  spend-*tracking* for a circuit breaker, not a real Stripe/billing charge —
  no rider or driver is billed differently because of this.
- **Ride state machine / money-adjacent dry run:** this is cost-governance,
  not fare calculation — no `ride.status` transition, wallet delta, or
  Stripe call is touched. `_fetch_directions_route`'s own return contract
  (a route dict, or `None` on budget exhaustion → haversine fallback) is
  unchanged; callers see identical behavior on both `allowed=True` and
  `allowed=False` outcomes.

## 5. User-experience effect

None. This is an internal cost-governance mechanism with no rider/driver/
admin-visible surface. On the rare occasion the budget is genuinely
exhausted, the existing haversine-fallback UX (unchanged from before this
fix) is what a rider would ever perceive, and only under sustained heavy
load hitting the shared daily cap — the same as before this change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/maps_budget.py` | Added `_RESERVE_BUDGET_LUA` script and `reserve_budget(sku)` function; added `redis_eval` (and the pre-existing but missing `redis_mget`) to the module's dual-import fallback block | New atomic primitive; the `redis_mget` addition is an unrelated pre-existing gap in the fallback import path, fixed in passing since this edit was already touching that exact block |
| `backend/routes/rides/_shared.py` | `_fetch_directions_route` now calls `reserve_budget("directions")` once, before the Google HTTP call, instead of `check_budget()` before + `record_call("directions")` after | Closes C104's race on this call site |
| `backend/routes/rides/_deps.py` | Added `reserve_budget` to both dual-import blocks' `from ...utils.maps_budget import (...)` lists | `_shared.py` reaches `maps_budget` functions through this indirection module, matching the existing pattern for `check_budget`/`record_call` |
| `backend/tests/test_maps_budget.py` | Added `TestReserveBudget`: allowed/denied paths, script-argument correctness (key order, SKU index), and both fallback paths (`RuntimeError` and a generic Redis error) | Pins `reserve_budget()`'s own logic |
| `backend/tests/test_directions_route.py` | `TestBudgetGate`'s tests updated from `check_budget`/`record_call` patches to `reserve_budget` patches; two tests renamed/reworded to reflect the reserve-before-call semantics | The old mocks no longer match what the code under test calls |

## 7. Before / after

```python
# Before
allowed, spent, budget = await check_budget()
if not allowed:
    logger.warning(...)
    return None
try:
    ...
    async with _httpx.AsyncClient(timeout=DIRECTIONS_TIMEOUT_S) as client:
        resp = await client.get(...)
        data = resp.json()
    await record_call("directions")
    if data.get("status") != "OK" or not data.get("routes"):
        ...
```

```python
# After
allowed, spent, budget = await reserve_budget("directions")
if not allowed:
    logger.warning(...)
    return None
try:
    ...
    async with _httpx.AsyncClient(timeout=DIRECTIONS_TIMEOUT_S) as client:
        resp = await client.get(...)
        data = resp.json()
    if data.get("status") != "OK" or not data.get("routes"):
        ...
```

## 8. Rollback plan

`git-revert-safe`. `reserve_budget()` is new, additive code with no other
caller; reverting the one-line swap in `_shared.py` back to
`check_budget()`/`record_call()` restores the exact pre-fix behavior with no
data migration, flag flip, or config change needed. No feature flag was
added — the fix is a pure implementation-detail swap inside an existing
gating call, invisible to every consumer of `_fetch_directions_route`.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_maps_budget.py
      backend/tests/test_directions_route.py -v` — 34/34 passed. Also ran a
      broader `pytest backend/tests -k "fare or estimate or ride or dispatch
      or maps or budget"` regression sweep (result pending at time of
      writing this entry — see amendment below if any finding surfaced).
- [x] `ruff check` and `ruff format --check` on all 5 touched files — clean.
- [x] Blast-radius grep performed: `grep -rn "check_budget\|record_call"` across
      `backend/` confirmed the only caller of these two functions inside
      `_shared.py` was the one function migrated; the other 8 call sites
      across `maps_proxy.py`/`tools_booking.py`/`maps_eta.py` were located
      and deliberately left untouched (see §3's "Scope of this fix").
- [ ] Manual repro steps followed in staging — not done, no staging access
      in this session.
- [x] Reviewed against relevant CLAUDE.md convention: "Do not silently
      swallow errors" — `reserve_budget()`'s `except Exception` fallback
      logs a `logger.warning` before falling back permissively, matching
      this module's own existing fail-open contract for every other
      function in the file.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — a one-line `git revert`.
- [x] Blast radius is stated: isolated, one call site migrated, 8 remaining
      call sites explicitly identified and left untouched as a named
      follow-up.
- [x] No silent behavior change to an already-shipped flow without the UX
      field filled in — §5 states plainly there is no user-visible effect,
      and §4 discloses the one internal timing/over-count behavior change.

## What was NOT verified

- **Real cross-request atomicity against a live Redis instance.** This
  repo's unit-test tier has no real Redis — `redis_eval()` always raises
  `RuntimeError` in that tier (same limitation `utils/h3_location_index.py`'s
  own Lua-based `upsert_driver()` carries; its tests never exercise the Lua
  path either, only the Python fallback). The tests added here mock
  `redis_eval()` directly, which pins `reserve_budget()`'s own argument
  construction and response parsing, and both fallback paths — but it cannot
  prove the Lua script itself is race-free under real concurrent Redis
  clients. If a real-Redis proof is wanted, it would need either the RLS
  integration tier (gated on `TEST_DATABASE_URL`, and that tier doesn't cover
  Redis at all) or a dedicated load-test scenario in `loadtest/`'s Locust
  scripts firing concurrent requests at a real Redis-backed environment.
- **The remaining 8 call sites' migration** — deliberately out of scope for
  this change (see §3); tracked as a named follow-up in `ACTION_ITEMS.md` C104
  rather than silently left unaddressed.

## Post-review addendum (2026-09-12)

`spinr-performance-sla-reviewer`'s adversarial pass (CLAUDE.md gate #10)
found two real issues, both fixed here before this landed:

1. **Fallback latency cascade (fixed).** `reserve_budget()`'s generic
   `except Exception` branch (Redis *configured but failing* — a network
   blip or timeout, not simply unset) originally fell through to
   `check_budget()` + `record_call()`, chaining up to 3 sequential Redis
   round-trips onto a connection that had just failed. `backend/utils/redis_client.py`
   sets no socket timeout, and `backend/routes/rides/booking.py`'s no-token
   safety net awaits `_fetch_directions_route` inline with no timeout wrapper
   of its own (unlike `estimates.py`'s bounded `asyncio.wait`), so a
   degraded-but-not-instantly-refused Redis connection could hang this path
   well past any latency budget. Fixed: the generic-`Exception` branch now
   returns the permissive default (`True, 0.0, budget`) directly, with one
   warning log — no further Redis calls. The `RuntimeError` branch (Redis
   *unconfigured*) is unchanged and stays chained to `check_budget()`/
   `record_call()`, since that path's fallback calls hit the fast in-process
   dict, not a struggling network connection, so there is no cascade risk to
   fix there.
2. **Scope claim overstated (corrected here, in this addendum).** "Closes
   C104's race on this call site" is accurate but should be read narrowly:
   the reviewer confirmed the Lua script itself is correct and, for the one
   migrated caller, stronger than documented (an *allowed* reservation can
   never leave the persisted total over budget at all, not just "at most one
   call's worth" of overshoot). But 3 of the 8 still-non-atomic call sites
   (`route_distance.py`'s live-route/OSRM-fallback path, `maps_proxy.py`'s
   own Directions proxy, `tools_booking.py`'s AI tool) write the exact same
   shared `"directions"` Redis key this fix reads. A burst through any of
   those unmigrated sites still reproduces C104's named failure mode against
   the same shared daily total. This fix closes the race for this one
   caller's own reservation; it does not close the breaker's overall
   burst-safety until the remaining call sites are migrated too (tracked in
   `ACTION_ITEMS.md` C104's follow-up note).

Also confirmed by the same review, no code change needed: the R8 fare-estimate
cache and `booking.py`'s no-token safety net both call the identical
`_fetch_directions_route`, so both get the same atomic protection and the
same disclosed reserve-before-call trade-off consistently — no divergent
behavior between the two callers. A minor Lua edge case (the very first
reservation of a UTC day, if itself over budget, leaves a harmless
zero-valued key with a live TTL) and a latent non-clustered-Redis assumption
(`_key()` builds unrelated key names with no hash-tag, so this would need
`{...}` tagging if this repo ever moved to Redis Cluster) were both noted as
informational, not requiring action now.

Test coverage was also strengthened per the review: added price-value and
TTL-argument assertions to `test_script_args_use_the_right_sku_index_and_key_order`
(previously only checked keys and the incr-index), and replaced
`test_falls_back_on_any_other_redis_eval_error_too` with
`test_generic_redis_eval_error_fails_open_without_further_redis_calls`,
which asserts `check_budget`/`record_call` are **not** called on this path —
pinning fix #1 above.
