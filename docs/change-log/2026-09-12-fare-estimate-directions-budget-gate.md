# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code (audit follow-through, roadmap item R2) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | srikumarimuddana-lab/spinrvm#5290 |
| Related issue or gap ID | `docs/audit/ride-experience/ROADMAP.md` R2 (P1); `cost-inventory-table.md` rows #1/#2 |

## 1. Issue / gap identified

`backend/routes/rides/_shared.py`'s `_fetch_directions_route` — the highest-volume Google
Directions call site in the app (every `/rides/estimate` call, plus every booking confirm
lacking a valid estimate token) — had no daily-spend budget check and no call recording at all,
unlike every sibling Directions/Maps call site in the codebase (`maps_proxy.py`,
`route_distance.py`'s live-route fallback, the AI booking tool). A spend spike on this path
would never trip the existing `maps_budget.py` daily circuit breaker.

## 2. Root cause

This call site predates the `maps_budget.py` circuit breaker (added later for other call sites)
and was never retrofitted — found by the 2026-09-12 ride-experience industry-benchmark audit
(`docs/audit/ride-experience/module-d-backend-cost.md`, `cost-inventory-table.md`), not by an
incident.

## 3. Fix / remediation

Added `await check_budget()` before the HTTP call (soft-fails to `None` — the function's
existing documented contract — on exhaustion, so callers fall through to the haversine distance
exactly as they already do for any other Directions failure) and `await record_call("directions")`
immediately after the HTTP call reaches Google (recorded regardless of response status, matching
`route_distance.py`'s sibling call site placement). Both helpers already existed in
`backend/utils/maps_budget.py`; wired through `backend/routes/rides/_deps.py`'s existing
dual-import re-export pattern.

## 4. Risk & impact on existing functionality

- **What else reads/writes the same state:** `maps_budget.py`'s Redis-backed daily counter is
  shared with `maps_proxy.py`, `route_distance.py`, and `ai/tools_booking.py` — this fix adds a
  fifth reader/writer of the same `"directions"` SKU bucket (already shared with
  `route_distance.py`'s own Directions fallback), not a new bucket. No schema change.
- **Could this regress a working flow?** Only if the daily budget is already near exhaustion —
  in that case, `/rides/estimate` and booking-confirm now correctly fall back to haversine
  instead of continuing to call (and bill) Google past the ceiling. This is the intended fix, not
  a regression: haversine is an already-accepted, already-shipped fallback path (used today on
  every Directions timeout/error), not new code.
- **Blast radius:** single-surface (backend). Callers: `backend/routes/rides/estimates.py`
  (`/rides/estimate`) and `backend/routes/rides/booking.py:815` (booking confirm without a valid
  estimate token) — both already handle a `None` return from this function today; no caller code
  changed.
- **Adversarial review finding, fixed before commit:** an independent `spinr-performance-sla-reviewer`
  pass caught that the initial version of the new log line used `%`-style placeholders, but this
  module's `logger` is loguru (not stdlib `logging`), which only supports `{}` — the exact
  anti-pattern CLAUDE.md documents. Fixed to `{:.2f}/{:.2f}`; verified against
  `tests/test_loguru_call_conventions.py` (8/8 pass) before this commit.
- **Known, pre-existing, not fixed here (out of scope for this change):** the same reviewer noted
  `estimate_today_usd()` makes 6 sequential `redis_get` calls (one per SKU) rather than a single
  `redis_mget` batch — this is pre-existing in `maps_budget.py` and already present at
  `route_distance.py`'s identical call site; this diff makes it relevant to a higher-volume path
  for the first time but does not introduce it. Also noted: `redis_client.py`'s async client has
  no `socket_timeout`, so a reachable-but-slow (not erroring) Redis could add unaccounted latency
  to the fare-estimate path's `_PRICING_ROUTE_WAIT_S` budget — systemic to the shared Redis
  client, not new to this diff. Neither is fixed here per the surgical-changes principle; both are
  worth a follow-up if `maps_budget.py`'s call volume grows further (e.g. once R4/R12 land).

## 5. User-experience effect

- **Who sees a difference:** nobody, under normal operation — the daily Maps budget
  ($5.00/day default) is far from being exhausted by real traffic today; this change only alters
  behavior in the budget-exhausted edge case, where it prevents continued Google billing rather
  than changing what a rider/driver sees (haversine fallback already exists and is invisible to
  the user — the fare is still computed and shown, just via a slightly less precise distance
  basis in that rare case).
- **Visible mid-session?** No.
- **Copy/notification change:** none.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/_shared.py` | `_fetch_directions_route` now calls `check_budget()` before the HTTP request (soft-fails to `None` on exhaustion) and `record_call("directions")` after it completes. | R2 |
| `backend/routes/rides/_deps.py` | Added `check_budget`/`record_call` re-export from `utils.maps_budget`, following the file's existing dual-import pattern. | R2 (dependency wiring) |
| `backend/tests/test_directions_route.py` | Added `TestBudgetGate` (4 new tests: budget-exceeded skips the HTTP call, spend recorded on success, spend recorded even on non-OK status, no-api-key path never checks budget). | R2 (CLAUDE.md pre-merge gate 4 — dry-run against fixtures with a concrete before/after scenario) |

## 7. Before / after

```python
# Before
if not api_key:
    return None
try:
    params: dict = {...}
    async with _httpx.AsyncClient(timeout=DIRECTIONS_TIMEOUT_S) as client:
        resp = await client.get(...)
        data = resp.json()
    if data.get("status") != "OK" or not data.get("routes"):
        ...
```

```python
# After
if not api_key:
    return None
allowed, spent, budget = await check_budget()
if not allowed:
    logger.warning(
        "_fetch_directions_route: daily Maps budget reached ({:.2f}/{:.2f} USD) — falling back to haversine distance",
        spent, budget,
    )
    return None
try:
    params: dict = {...}
    async with _httpx.AsyncClient(timeout=DIRECTIONS_TIMEOUT_S) as client:
        resp = await client.get(...)
        data = resp.json()
    await record_call("directions")
    if data.get("status") != "OK" or not data.get("routes"):
        ...
```

**Concrete before/after scenario (CLAUDE.md pre-merge gate 4):** budget exhausted mid-day —
*before*: `_fetch_directions_route` keeps calling and billing Google on every `/rides/estimate`
regardless of the daily ceiling, invisible to `maps_budget.py`'s own spend estimate; *after*:
`check_budget()` returns `allowed=False`, the function returns `None` without any HTTP call,
`estimates.py`'s existing `select_fare_distance` falls through to the haversine basis (already
an accepted-risk behavior, `spinr_fare_distance_basis_total{basis="haversine_fallback"}` already
tracks this), and no further spend accrues on this call site until the next UTC day's bucket.

## 8. Rollback plan

`git revert` is sufficient: no data mutation, no migration, no schema change. If the budget gate
itself needs to be disabled in an emergency (e.g. a false-positive exhaustion blocking real fare
estimates), `MAPS_DAILY_BUDGET_USD` (an existing `app_settings`/env-driven setting per
`maps_budget.py`) can be raised without a code change or redeploy.

## 9. Verification performed

- [x] Automated tests: `pytest backend/tests/test_directions_route.py` (15/15 pass, including 4
      new budget-gate tests), `test_loguru_call_conventions.py` (8/8 pass, confirms the log-format
      fix), `test_ride_estimate_branches.py` + `test_create_ride_post_insert_branches.py` (21/21
      pass — the two files that reference `_fetch_directions_route` indirectly, confirming no
      regression to existing callers).
- [ ] Manual repro steps followed in staging — not done, no staging access in this session.
- [x] Blast-radius grep performed — confirmed `estimates.py` and `booking.py:815` are the only
      callers, both already handle `None`.
- [x] Reviewed against relevant CLAUDE.md conventions — dual-import pattern followed
      (`_deps.py`); do-not-silently-swallow-errors respected (budget exhaustion is logged, not
      silent); loguru `{}`-only convention violation caught and fixed by adversarial review
      before commit, not after.
- [x] Adversarial pre-implementation review (new CLAUDE.md gate #10): alternative considered —
      a bespoke rate-limiter specific to this call site — rejected in favor of reusing the
      existing `check_budget()`/`record_call()` pattern already proven at 4 other call sites.
- [x] Adversarial post-implementation review: `spinr-performance-sla-reviewer` run against the
      staged diff — found and this commit fixes the loguru placeholder bug; two lower-priority,
      pre-existing findings noted in §4 and deliberately deferred.
- [ ] Feature-flagged — not flagged. Justification: this closes a cost-governance gap by
      reusing an existing, already-proven pattern; the "new" behavior (falling back to haversine
      on budget exhaustion) is not new to the app, only new to this one call site, and only
      triggers in an edge case ($5/day budget exhaustion) that does not occur in normal operation.

### What was NOT verified

- No production Redis load test — the reviewer's "Redis up but slow" latency edge case
  (unbounded `check_budget()` call inside `_PRICING_ROUTE_WAIT_S`'s hand-derived latency budget)
  is reasoned about from code shape, not measured under load.
- No staging/production confirmation that the daily budget circuit breaker actually engages
  end-to-end for this call site (would require deliberately exhausting a real budget).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, or raise `MAPS_DAILY_BUDGET_USD`).
- [x] Blast radius is stated, not assumed (both callers identified and confirmed unaffected).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 —
      no user-visible difference under normal operation; edge-case behavior stated explicitly).
