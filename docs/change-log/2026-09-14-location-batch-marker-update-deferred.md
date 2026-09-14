# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Found during a 2026-09-13/14 deep-dive audit into reported car-marker lag/latency complaints (user-reported, this session); documented p95 measured 2026-09-11 (see `routes/drivers/location.py`'s own comment at `_persist_v2_location_batch`) |

## 1. Issue / gap identified

`POST /drivers/location-batch`'s v2 (ride-active) path is on the repo's <150ms driver-location-write P95 SLA, but measured p50 232ms / p95 288ms on 2026-09-11 — roughly double target. After the durable breadcrumb persist (which the response payload actually reflects), the handler also awaited a GPS-integrity check (1-2 Redis round trips), a conditional live-marker DB write, and a presence refresh — none of which the HTTP response depends on — all inline, on the critical path.

## 2. Root cause

`_persist_v2_location_batch` was written with the live-marker update (GPS-integrity check → `drivers.lat/lng` write → presence refresh) sequenced synchronously after the durable persist, simply because that's the order the two concerns were implemented in. The response (`result.ack.to_dict()`) is fully computed from `persist_trip_location_batch`'s result before any of that later work starts — nothing in the marker-update chain feeds back into what's returned to the client.

## 3. Fix / remediation

- Added `background_tasks: BackgroundTasks` to `update_location_batch` (the route handler) and threaded it into `_persist_v2_location_batch` only — the idle-batch path (`_persist_v2_idle_batch`) and the legacy v1 path are untouched.
- Extracted the GPS-integrity-check → marker-write → presence-refresh sequence into a new `_apply_v2_live_marker_update` function, scheduled via `background_tasks.add_task(...)` instead of awaited inline. It runs after the HTTP response is sent (FastAPI/Starlette's standard `BackgroundTasks` semantics — the same pattern already used in this router package by `tax_exports.py`'s `email_t4a_summary`/`email_earnings_export`).
- Each internal step inside the deferred task keeps its own `try/except` with `logger.error(..., exc_info=True)` so a failure there still surfaces loudly (this repo's "do not silently swallow DB errors" rule) — it just no longer fails the HTTP response for a batch that was already durably persisted.
- The presence-refresh branch for the "no usable last point" case (`elif driver.get("is_online")`) is also deferred via `background_tasks.add_task(_deps.mark_present, ...)` for consistency, though `mark_present` itself already swallows and logs its own errors (`utils/driver_presence.py`) and is a single fast Redis SET.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `_persist_v2_location_batch` and the `update_location_batch` route signature.** Grepped every caller of `_persist_v2_location_batch` (exactly one: the route handler) and every other consumer of `_apply_v2_live_marker_update`'s inputs (`check_location_integrity`, `_write_marker_if_due`, `_deps.mark_present`) — none of their own implementations changed, only who calls them and when. `_persist_v2_idle_batch` (Period-1 deadhead accumulator path) was deliberately left untouched — its `driver.get("period1_accum_km")` read-then-write is a regulated SGI accumulator figure, and deferring it would widen an already-present (pre-existing, not introduced here) read-modify-write race window without a documented locking story; out of scope for this latency fix.
- **Real behavior change, called out per Rule 5 (no silent behavior change to a live-tested flow):** previously, an exception raised by `check_location_integrity` or `_write_marker_if_due` propagated out of `_persist_v2_location_batch` as an unhandled exception → FastAPI's global handler → a 500 response, **even though the durable breadcrumb persist had already succeeded**. That meant the driver-app's outbox could interpret a successfully-persisted batch as failed and retry it. After this fix, such a failure is logged loudly (`logger.error(exc_info=True)`) but the client still receives its ack. This is treated as a reliability improvement, not a regression, but it is a genuine, deliberate change in what makes this endpoint return non-2xx — flagging explicitly rather than letting it pass as a pure "no functional change" refactor.
- **Response payload is byte-for-byte unchanged** — `result.ack.to_dict()` is computed and returned identically; only the marker/presence side effects moved off the request path.
- **Dispatch/rider-map staleness window**: `drivers.lat/lng` (read by dispatch and the rider map) is now written slightly later relative to the HTTP response than before — on the order of the deferred task's own runtime (Redis + a conditional Supabase write), typically single-digit-to-low-double-digit ms, scheduled essentially immediately after the response is sent. This does not change how fresh the position is when the *next* location-batch request supersedes it, and matches the same eventual-consistency model the WS live-ping path already has (see the companion PR #5369 in this same audit, which reorders `buffer_ride_breadcrumb` for the same reason).
- **No interaction with the ride state machine or money paths.**

## 5. User-experience effect

Driver-facing latency improvement on the mobile-network round-trip for every location-batch request during an active ride — the driver-app's outbox should see faster acks, reducing the odds of hitting the client-side retry/backoff path under marginal connectivity. No visible UI change, no new screen, no new error message. Rider/admin map freshness is unaffected in the steady state; marker writes still happen, just a few ms after the driver's own ack rather than before it.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/location.py` | Added `BackgroundTasks` import; new `_apply_v2_live_marker_update` background-task function; `_persist_v2_location_batch` now takes `background_tasks` and defers the integrity-check/marker-write/presence-refresh sequence instead of awaiting it inline; `update_location_batch` takes and threads through `background_tasks` | Removes non-response-gating work from the <150ms driver-location-write SLA path |
| `backend/tests/test_location_batch.py` | Updated 8 call sites to pass `background_tasks=BackgroundTasks()`; `test_v2_batch_persists_before_updating_the_live_marker` and `test_v2_batch_skips_live_marker_update_when_integrity_check_rejects` now assert the task is scheduled (and, for the first, that running it performs the marker write) instead of assuming it ran inline | Keeps existing regression coverage meaningful under the new deferred-execution model |
| `backend/tests/test_location_batch_revoked_session.py` | Added `background_tasks=BackgroundTasks()` to the one direct call | Required parameter |
| `backend/tests/test_period1_accumulation_endpoint.py` | Added `background_tasks=BackgroundTasks()` to the one direct call (idle-batch path, unaffected by the behavior change) | Required parameter |
| `backend/tests/test_drivers_extended.py` | Added `background_tasks=BackgroundTasks()` to 5 direct calls (legacy v1 path, unaffected) | Required parameter |
| `backend/tests/test_p3_background_location.py` | Added `background_tasks=BackgroundTasks()` to 3 direct calls (legacy v1 path, unaffected) | Required parameter |

## 7. Before / after

```python
# Before
if latest is not None:
    lat = ...
    lng = ...
    if lat is not None and lng is not None:
        trusted, reason = await check_location_integrity(driver["id"], lat, lng, ...)
        if trusted:
            update_data = {...}
            await _write_marker_if_due({"id": driver["id"]}, update_data, str(driver["id"]), "rest_v2_trip")
if driver.get("is_online"):
    await _deps.mark_present(driver["id"])
return result.ack.to_dict()
```

```python
# After
lat = lng = None
if latest is not None:
    lat = ...
    lng = ...
if lat is not None and lng is not None:
    background_tasks.add_task(
        _apply_v2_live_marker_update,
        driver["id"], request.ride_id, lat, lng,
        latest.heading, latest.speed, latest.accuracy, latest.mocked,
        bool(driver.get("is_online")),
    )
elif driver.get("is_online"):
    background_tasks.add_task(_deps.mark_present, driver["id"])
return result.ack.to_dict()
```

## 8. Rollback plan

`git-revert-safe` — no schema, migration, or flag involved. Reverting restores the inline-awaited sequence with no other side effect; the deferred task's own internal logic is unchanged from the code it replaced (same functions, same call arguments), so a revert is a pure structural undo.

## 9. Verification performed

- [x] Full targeted suite: `test_location_batch.py`, `test_location_batch_revoked_session.py`, `test_period1_accumulation_endpoint.py`, `test_drivers_extended.py`, `test_p3_background_location.py` — 147 passed, 1 pre-existing xfail, 0 failed.
- [x] Broader regression sweep: `test_breadcrumb_persistence.py`, `test_breadcrumbs_late_tail.py`, `test_location_integrity_coverage.py`, `test_ride_complete_coverage.py`, `test_ride_completion_location.py`, `test_e2e_route_tail_recovery.py`, `test_forced_upgrade_middleware.py` — 151 passed, 0 failed.
- [x] `ruff check` on all changed files — clean.
- [x] `python3 -c "import ast; ast.parse(...)"` on `location.py` — syntax OK.
- [x] Blast-radius grep: `_persist_v2_location_batch` has exactly one caller (the route handler); `_persist_v2_idle_batch` and the legacy v1 branch are unmodified and unaffected.
- [ ] `spinr-performance-sla-reviewer` and `spinr-realtime-reliability-reviewer` run in parallel against this diff — pending at commit time; any finding lands as a follow-up commit on this same PR before merge.

**What was NOT verified:** no live/staging measurement of the actual p95 improvement — the fix is reasoned from the code path (Redis round trips + a conditional Supabase write removed from the response's critical path) and confirmed behaviorally via unit tests, not measured end-to-end against the same Fly-yyz-to-ca-central-1 network path the original 232ms/288ms figures were measured on. The exact latency win is therefore an estimate, not a re-measured number.

## 10. Alternatives considered

- **Parallelize (asyncio.gather) the integrity check and marker write instead of deferring them**: would shave some latency (the two Redis calls in `check_location_integrity` could overlap with `should_write_marker`'s Redis check) but the conditional Supabase `update_one` in `_write_marker_if_due` still can't start until `check_location_integrity` resolves (its result gates whether the write happens at all), so this only partially removes the sequential cost and keeps all of it on the critical path. Rejected — full deferral removes the entire chain from the response path, and this codebase already has an established `BackgroundTasks` pattern for exactly this kind of non-response-gating side effect.
- **Fire-and-forget via `asyncio.create_task` instead of `BackgroundTasks`**: rejected — `BackgroundTasks` is the framework-native mechanism that Starlette guarantees runs to completion after the response is sent (within the same request's ASGI lifecycle), whereas a bare `create_task` can be silently cancelled if the event loop tears down the request scope aggressively, and offers no discoverability for tests (`bg.tasks` inspection, already used by `test_t4a_email.py`). Matches existing repo convention.
