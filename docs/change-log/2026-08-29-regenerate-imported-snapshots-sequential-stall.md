# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-29 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Real production stall during the 08-22 migration's route/snapshot backfill step — `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` |

## 1. Issue / gap identified

Clicking "Regenerate Snapshots" for 62 newly-imported rides left the UI
spinning with no success/failure message. Checked directly against
production: 50 of 62 rides had their `route_snapshot_url` written, then
**zero progress for 13+ minutes** — the request had genuinely stalled
server-side, not just been slow.

## 2. Root cause

The exact same bug class found and fixed three times already this session
in the CSV importers, in a fourth location: `admin_regenerate_imported_snapshots()`
processed its `rides` list in a plain sequential `for` loop, awaiting a real
Google Static Maps API call, a Supabase Storage upload, and a DB write for
each ride in turn (plus an explicit 0.3s pacing sleep). At real per-ride
network latency this exceeds whatever request/proxy timeout sits in front
of the backend, and the connection is torn down mid-loop with no partial
result returned to the caller — the browser is left waiting on a response
that will never arrive.

This route was already bounded (`limit <= 500`, existing safety check
confirmed earlier this session) — the bound protects against an unbounded
scan, but does nothing about wall-clock time per row, which is what
actually failed here.

## 3. Fix / remediation

Extracted the per-ride body into `_process_one()` and ran the ride list via
`asyncio.gather()` bounded by a new `asyncio.Semaphore(_SNAPSHOT_CONCURRENCY)`
(module constant, `= 8`). Unlike the three prior fixes (which used a
`ThreadPoolExecutor` because they wrapped blocking sync Supabase calls),
this route's work is already native `async`/`await` (a real async HTTP call
for Google Static Maps, `run_in_executor`-wrapped calls for the OSM
fallback and storage upload) — an `asyncio.Semaphore` is the correct
primitive here, not a thread pool. Concurrency is kept modest (8, not the
CSV importers' 20) since each ride fans out a real call to the Google
Static Maps API and the existing 0.3s per-ride pacing sleep is preserved
to avoid hammering it.

New regression test
(`test_regenerate_processes_rides_concurrently_not_sequentially`) proves
the fix by tracking the actual maximum number of rides in flight at once
(via a fake Google-render function that increments/decrements a shared
counter around a real `asyncio.sleep(0)` yield point) rather than racing a
wall clock — asserts it reaches exactly `_SNAPSHOT_CONCURRENCY`. Confirmed
to fail without the fix (reverted the route change locally, re-ran, saw a
failure — reverting `_SNAPSHOT_CONCURRENCY`'s definition raises an
`ImportError` at test-collection time, which is itself proof the fix is
required — restored, re-ran, saw it pass).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped the repo: `admin_regenerate_imported_snapshots`
  has exactly one route definition and no other backend caller.
- **Behavior-preserving for the success/failure contract**: the response
  shape (`{total, success, failed, renderer, errors}`) and the audit-log
  call are unchanged; only how the per-ride work is scheduled changed.
  `errors` ordering may no longer exactly match the input `rides` order
  (rides now complete out of order across concurrent workers) — cosmetic,
  not used for anything positional downstream.
- **`success`/`failed` counters are safe to mutate from concurrent
  coroutines without a lock**: asyncio coroutines on one event loop are
  cooperatively scheduled, never running two Python bytecode instructions
  simultaneously, so `success += 1` inside `_process_one()` cannot race
  across concurrently-gathered tasks.
- All 92 tests in `tests/test_admin_rides_coverage.py` pass, including 3
  pre-existing tests for this exact endpoint (happy path, no-super-admin
  403, no-rides-skips-audit) plus the new regression test.

## 5. User-experience effect

- **Internal admin only** (super_admin-gated). Before: a run large enough
  to exceed the timeout left the operator with an indefinitely spinning
  button and no way to tell whether it succeeded, failed, or was still
  running — they had to check the database directly to find out (as this
  session did). After: the same run completes ~8x faster in wall-clock
  terms (bounded by `_SNAPSHOT_CONCURRENCY`), well within the window that
  stalled at 50/62 rides.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/rides.py` | `admin_regenerate_imported_snapshots()`'s per-ride loop replaced with `asyncio.Semaphore`-bounded concurrent execution via `_process_one()` + `asyncio.gather()`; added `_SNAPSHOT_CONCURRENCY = 8` module constant | Fix the real production stall found live during this session's route/snapshot backfill step |
| `backend/tests/test_admin_rides_coverage.py` | New regression test proving true bounded concurrency (max-in-flight tracking, not wall-clock timing); added `import asyncio` | Lock in the fix against regression |

## 7. Before / after

```python
# Before
for ride in rides:
    ...
    png_bytes = await render_ride_snapshot_google(...)
    ...
    await db.update_one("rides", {"id": ride_id}, {"route_snapshot_url": url})
    success += 1
    await asyncio.sleep(0.3)
```

```python
# After
semaphore = asyncio.Semaphore(_SNAPSHOT_CONCURRENCY)

async def _process_one(ride: dict) -> None:
    nonlocal success, failed
    async with semaphore:
        ...
        png_bytes = await render_ride_snapshot_google(...)
        ...
        await db.update_one("rides", {"id": ride_id}, {"route_snapshot_url": url})
        success += 1
        await asyncio.sleep(0.3)

await asyncio.gather(*(_process_one(r) for r in rides))
```

## 8. Rollback plan

`git-revert-safe` — purely a scheduling/concurrency change; same per-ride
logic, same writes, same response shape.

## 9. Verification performed

- [x] Root-caused via direct production SQL (not guessed): confirmed 50/62
      rides had `route_snapshot_url` written, then zero progress for 13+
      minutes on the same batch.
- [x] `pytest tests/test_admin_rides_coverage.py` — 92 passed, including
      all pre-existing tests for this endpoint.
- [x] `ruff check` / `ruff format --check` — clean.
- [x] New regression test confirmed to fail without the fix, pass with it.
- [x] Blast-radius grep: exactly one route definition, no other callers.

## What was NOT verified

- Not yet re-run against real production — this fix has not been exercised
  against the remaining 12 rides via a live click yet; that's the
  operator's next step, after which production will be re-checked directly
  (same pattern as every prior verification this session).
- Did not measure real-world wall-clock improvement against the actual
  Google Static Maps API (no network access in this environment) — the
  regression test proves genuine concurrent scheduling, not a specific
  speedup factor.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed (one route, no other callers)
- [x] No silent behavior change to the response contract callers rely on
      (shape unchanged; only per-ride error ordering, which nothing
      downstream depends on positionally, may shift)
