# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude Code (session `session_01Aq7xVA8zptrqFwExvqVrwN`) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `mvapps/dreamy-faraday-2wz914-c104-callsites` |
| Related issue or gap ID | ACTION_ITEMS.md C104 (follow-up closure) |

## 1. Issue / gap identified

C104's first fix (2026-09-12) added the atomic `reserve_budget(sku)` primitive
to `backend/utils/maps_budget.py` and proved it on one call site
(`routes/rides/_shared.py`'s fare-estimate Directions call), but explicitly
left the other non-atomic `check_budget()`-before/`record_call()`-after call
sites unmigrated as a named follow-up. Until every writer of a given SKU's
shared daily-spend key uses the atomic primitive, that SKU's circuit breaker
can still be overshot by a concurrent-request burst through any unmigrated
caller — "closes C104" only ever meant "for the one migrated caller."

## 2. Root cause

Same as the original C104 entry: `check_budget()` (read) and `record_call()`
(increment) are two separate Redis operations. Every caller that hadn't yet
been swapped to `reserve_budget()` still carried the race.

## 3. Fix / remediation

Migrated every remaining production call site to `reserve_budget(sku)`,
mechanically, one commit per file (or tightly-coupled file group):

- `backend/routes/maps_proxy.py` — 4 endpoints (autocomplete, details,
  reverse-geocode, directions) via `_ensure_budget(sku)`.
- `backend/ai/tools_booking.py` — 4 call sites (not 3, as ACTION_ITEMS.md's
  original count said): `_geocode_with_locality_retry`'s up-to-two geocode
  calls, `_lookup_place_candidates`'s Places Text Search call,
  `_rank_named_place_candidates_by_route`'s per-candidate concurrent
  directions calls, and `get_rider_location`'s reverse-geocode call.
  `_places_available()`'s own `check_budget()` advisory pre-check is left
  unchanged by design — it makes no paid call itself, it just short-circuits
  before the (now atomically-reserved) real calls deeper in the stack.
- `backend/utils/maps_eta.py` — 2 call sites (not 1, as ACTION_ITEMS.md's
  original count said): `get_ride_eta_seconds`'s Distance Matrix fallback and
  `batch_get_etas`'s batched dispatch-time ETA lookup.
- `backend/utils/route_distance.py` — 2 call sites:
  `_compute_route_via_google` (live-route line's OSRM fallback) and
  `compute_navigation_steps` (turn-by-turn maneuver fetch). Not in
  ACTION_ITEMS.md's original 8-site enumeration by file, but explicitly named
  in the same entry's "Practical scope" note as sharing the exact
  `"directions"` key the other sites write.
- `backend/utils/address_verification.py` — 1 call site
  (`verify_address_matches_coordinate`, B9's save-time address/coordinate
  mismatch check). **Not previously named anywhere in ACTION_ITEMS.md's C104
  entry** — found only by grepping the whole repo for every remaining
  `await check_budget()`/`await record_call(` call before considering this
  follow-up done.

**Alternative considered:** stop at the literal "8 remaining call sites"
enumerated in ACTION_ITEMS.md (`maps_proxy.py` ×4 + `tools_booking.py` ×3 +
`maps_eta.py` ×1) and file `route_distance.py` and `address_verification.py`
as a separate future follow-up, since they weren't named in that specific
bullet list. **Rejected** because the same ACTION_ITEMS.md entry's own
"Practical scope" note already names `route_distance.py` as one of the
sites sharing the unmigrated `"directions"` race, and a full repo grep is
what surfaced `address_verification.py` was missed entirely — leaving either
unmigrated after this pass would mean declaring the follow-up "done" while
a known, named gap (and one only just discovered) stays open against the
exact same shared Redis keys this whole effort exists to close. The
mechanical pattern is identical and already proven across 4 prior commits
this session, so the incremental cost was low relative to leaving the gap.

Every migrated call site follows the same shape: `reserve_budget(sku)`
called once, immediately before the actual paid HTTP call, replacing the
`check_budget()`-before/`record_call()`-after pair. No caller-visible
contract changed — every function's return type and fallback behavior on
`allowed=False` is identical to before.

## 4. Risk & impact on existing functionality

- **Blast radius, confirmed via repo-wide grep:** `grep -rn "await check_budget()\|await record_call("` across `backend/` (excluding stale
  agent worktrees under `.claude/worktrees/`) now returns only:
  `tools_booking.py:590` (the intentionally-unchanged `_places_available()`
  advisory gate), `maps_budget.py`'s own internal definitions (the
  `check_budget()`/`record_call()` function bodies themselves, kept for
  backward compatibility and for the fallback path inside `reserve_budget()`
  when `redis_eval()` raises `RuntimeError`), and comments/docstrings. No
  production call site still uses the non-atomic pair for an actual paid
  call.
- **`check_budget`/`record_call` are not deleted** — they remain in
  `maps_budget.py` because `reserve_budget()`'s own `except RuntimeError`
  branch (Redis unconfigured — this repo's entire unit-test tier) falls back
  to calling them internally, and `_places_available()` still uses
  `check_budget()` directly as its cheap advisory pre-check. Nothing else
  reads them anymore.
- **`backend/routes/rides/_deps.py`** still re-exports `check_budget,
  record_call, reserve_budget` from `maps_budget.py` for `routes/rides/`
  files to import — `check_budget`/`record_call` are now unused through this
  path (only `_shared.py` has a comment referencing them, not real code).
  Left alone: `_deps.py` is an auto-generated dependency hub used by ~15
  files in `routes/rides/` and `routes/drivers/`, and removing an export
  from it is unrelated cleanup outside this fix's scope — noted here per
  CLAUDE.md's "notice dead code, don't delete it unless asked."
  Not deleted.
- **Test-only discovery, not a functional finding:** the earlier
  `maps_proxy.py` migration (commit `3e6fb54`, prior session segment) had
  already broken `backend/tests/test_maps_proxy_coverage.py` — a second,
  separate coverage test file for the same routes module that this session's
  work had not checked. Fixed in the first commit of this batch before doing
  any new migration work, so no known-broken test was carried forward.
- **No shared-primitive signature change.** `reserve_budget(sku)` already
  existed from the prior C104 fix; this batch only changes call sites, not
  the primitive itself.
- **State machine / money:** none of these call sites touch `ride.status`,
  wallet deltas, or Stripe. This is cost-governance for third-party Maps API
  spend, same category as the original C104 fix.

## 5. User-experience effect

None, for the same reason as the original C104 fix: budget-exhausted
behavior (haversine ETA fallback, "place lookup not available" AI-tool
message, `503` from the proxy endpoints, fail-open on address verification)
is unchanged — only the internal atomicity of the spend-tracking step
changed. No rider/driver/admin-visible behavior differs on either the
`allowed=True` or `allowed=False` path.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/maps_proxy.py` | `_ensure_budget(sku)` now calls `reserve_budget(sku)` instead of `check_budget()` + a separate `record_call(sku)` per endpoint | Closes the race on all 4 proxy endpoints |
| `backend/tests/test_maps_proxy.py` | No changes needed (transparent `RuntimeError` fallback) | — |
| `backend/tests/test_maps_proxy_coverage.py` | Renamed `check_budget`/`record_call` patches to `reserve_budget`; fixed 2 direct `_ensure_budget()` calls to pass the required `sku` arg | This file was missed by the earlier `maps_proxy.py` migration and was left broken |
| `backend/ai/tools_booking.py` | 4 paid-call sites now reserve atomically right before their HTTP call; `_places_available()`'s advisory `check_budget()` gate is unchanged | Closes the race on all of this file's actual paid calls, including a true intra-request concurrent-candidate burst in `_rank_named_place_candidates_by_route` |
| `backend/tests/test_ai_tools_booking.py` | 36 `record_call` patches renamed to `reserve_budget` with a `(True, spent, budget)` return value (bare `AsyncMock()` would break the code's tuple-unpack) | The old mocks no longer match what the code calls |
| `backend/utils/maps_eta.py` | Both `get_ride_eta_seconds` and `batch_get_etas` reserve atomically before their Distance Matrix call | Closes the race on both real call sites (ACTION_ITEMS.md only named one) |
| `backend/tests/test_maps_eta_budget_gate.py` | `check_budget`/`record_call` patches renamed to `reserve_budget` across all 5 tests | Same reason |
| `backend/utils/route_distance.py` | `_compute_route_via_google` and `compute_navigation_steps` reserve atomically before their Directions call | Closes the race on the two Directions writers this file owns |
| `backend/tests/test_route_distance_coverage.py`, `test_compute_route_fallback.py`, `test_navigation_steps.py` | Patches/helper closures renamed from `check_budget`+`record_call` to a single `reserve_budget` mock; `recorded` list semantics preserved (only an *allowed* reservation appends to it) | Same reason |
| `backend/utils/address_verification.py` | `verify_address_matches_coordinate` reserves atomically before its geocode call | Previously-unnamed call site found via full-repo grep |
| `backend/tests/test_address_verification.py` | `check_budget`/`record_call` patches renamed to `reserve_budget` | Same reason |

## 7. Before / after

```python
# Before (repeated shape across all 9 call sites)
allowed, spent, budget = await check_budget()
if not allowed:
    logger.warning(...)
    return None  # or equivalent budget-exhausted fallback
...
async with httpx.AsyncClient(timeout=...) as client:
    resp = await client.get(URL, params=params)
await record_call(sku)
...
```

```python
# After
allowed, spent, budget = await reserve_budget(sku)
if not allowed:
    logger.warning(...)
    return None  # or equivalent budget-exhausted fallback
...
async with httpx.AsyncClient(timeout=...) as client:
    resp = await client.get(URL, params=params)
...
```

## 8. Rollback plan

`git-revert-safe`, same as the original C104 fix. Each of the 5 commits in
this batch is an isolated, mechanical swap back to the old
`check_budget()`/`record_call()` pair (both functions still exist,
unremoved, in `maps_budget.py`) with no data migration, flag flip, or config
change needed. Commits can be reverted individually per file/group without
affecting the others, since none of the 5 touched files depend on each
other's changes.

## 9. Verification performed

- [x] `pytest` run per file/group immediately after each migration, before
      committing: `test_maps_proxy.py` + `test_maps_proxy_coverage.py`
      (63 passed), `test_maps_eta_budget_gate.py` + 3 sibling ETA test files
      (17 passed), `test_route_distance_coverage.py` +
      `test_compute_route_fallback.py` + `test_navigation_steps.py` +
      `test_directions_route.py` (combined with the other maps/ETA files:
      306 passed total across the full combined run), `test_address_verification.py`
      (9 passed), `test_ai_tools_booking.py` + `test_ai_tools_core.py` (177
      passed together with the maps proxy/budget suites).
- [x] `ruff check` and `ruff format --check` on every touched file — clean.
- [x] Blast-radius grep performed repo-wide for `await check_budget()` and
      `await record_call(` — confirmed zero remaining production call sites
      outside the two intentionally-unchanged spots (`_places_available()`'s
      advisory gate, `maps_budget.py`'s own primitive definitions).
- [ ] Full `pytest -m "not slow"` regression sweep — started in background;
      not yet confirmed complete at the time this entry was written (see
      amendment below once it finishes).
- [ ] Manual repro / staging check — not done, no staging access in this
      session.
- [x] Reviewed against CLAUDE.md's "do not silently swallow errors" —
      no error-handling behavior changed; every migrated site's existing
      fail-open/fail-loud posture (warning-log-then-fallback, or fail-open
      per `address_verification.py`'s documented contract) is unchanged.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — per-commit `git revert`.
- [x] Blast radius stated: 5 files' production call sites, all previously
      identified in the original C104 entry except `address_verification.py`
      (newly found) and the true count for `tools_booking.py`/`maps_eta.py`
      (4 and 2 respectively, not 3 and 1 as originally estimated).
- [x] No silent behavior change to an already-shipped flow — §5 confirms no
      user-visible effect on any path.

## What was NOT verified

- **Real cross-request atomicity against a live Redis instance** — same
  limitation as the original C104 fix; this repo's unit-test tier has no
  real Redis, so `reserve_budget()`'s Lua script itself is still only proven
  against mocked `redis_eval()` calls, not genuine concurrent Redis clients.
- **The full `pytest -m "not slow"` background run's final result** at the
  time of writing (see §9) — the targeted per-file runs covering every
  touched file all passed, but the full-suite confirmation was still in
  flight.
- **`backend/routes/rides/_deps.py`'s now-unused `check_budget`/`record_call`
  re-exports** — left in place, not verified to be safe to remove (would
  need a check across every file importing from `_deps.py`, out of scope for
  this call-site migration).
