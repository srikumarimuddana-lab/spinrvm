# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-03 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers (`location_integrity.py`, safety-adjacent GPS-spoofing checks) / rides (`route_gap_monitor.py`, `route_distance.py`) |
| PR / commit link | (branch: `claude/a1c-subtier-c-batch-locintegrity-routegap-routedist`) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1c Sub-tier C — `utils/location_integrity.py` (Batch 10, explicitly left open when the zoho-distrecon-obs batch closed its other two files) and Batch 12 (`utils/route_gap_monitor.py`, `utils/route_distance.py`) of the 13-batch itemization (PR #3335) |

## 1. Issue / gap identified

Three files sat in the Sub-tier C 60–80% coverage band:

- `backend/utils/location_integrity.py` — 75.00% (52 stmts, server-side GPS
  spoofing detection on the driver location-update hot path: mock-flag,
  accuracy sanity, impossible-speed, and teleportation heuristics). **No test
  file existed for this module at all.**
- `backend/utils/route_gap_monitor.py` — 77.78% (108 stmts, the 15-second
  active-trip GPS-gap monitor background loop).
- `backend/utils/route_distance.py` — 78.12% (489 stmts, the largest file in
  this batch and second-largest in the whole Sub-tier C list — road-snapped
  trip distance/geometry for billing and SGI map review).

## 2. Root cause

`location_integrity.py`'s ~75% baseline came entirely from indirect exercise
via other test suites that happen to call `check_location_integrity` in
passing — no dedicated test ever exercised the accuracy-sanity branches, the
teleport-detection window logic, or either Redis soft-failure path
(`redis_get`/`redis_set` raising).

`route_gap_monitor.py`'s existing `test_route_gap_monitor.py` covered
`assess_location_gap`'s state branches, one idempotent-open tick, one resolve
tick, the lifespan-loop registration check, the migration DDL shape, and the
driver location-health nudge well, but never touched `_now()`,
`_configured_threshold_seconds`'s two validation failures (non-integer
setting, non-positive setting), `_open_gap_event`'s no-gap-start no-op, a
tick scanning a ride with no `id` or an `unknown`-state ride, or any branch
of the `route_gap_monitor_loop` wrapper itself (success/heartbeat, tick
exception recorded as a metric, `CancelledError` re-raised without being
treated as a failure).

`route_distance.py`'s two existing test files
(`test_route_distance.py`, `test_route_distance_osrm.py`) covered the OSRM/
Google Roads happy paths, provider selection, and several soft-failure
branches well (measured baseline with those two files alone was 66%; the
78.12% ACTION_ITEMS.md figure reflects additional indirect coverage from
`test_compute_route_fallback.py`, `test_e2e_route_tail_recovery.py`,
`test_live_route.py`, `test_maps_eta_osrm.py`, `test_phase_distance_parity.py`,
and `test_trip_distance.py`), but left substantial gaps: small-helper edge
cases (`_downsample`/`_cap_polyline` with `max_count < 2`, `_osrm_timestamp`'s
missing/naive-tz/unsupported-type inputs, `_overlapping_chunks` validation,
`_observed_segment_points`'s dataclass/dict/fallback-key branches),
`compute_segmented_road_route`'s three failure branches (insufficient
points, provider unavailable, invalid provider geometry),
`_compute_route_via_osrm`'s exception and short-polyline paths,
`snap_endpoint_via_osrm`'s malformed-input/HTTP-error/exception/short-distance
branches, `compute_gap_route_via_osrm` and its Google-Directions twin
`compute_gap_route_via_google` (including the non-finite-coordinate and
collapsed-polyline edge cases), `_decode_encoded_polyline` (untested
entirely), `_compute_route_via_google`'s cache-hit/cache-read-exception/
budget-gate/HTTP-error/status-not-OK/short-polyline/cache-write-exception
branches, and `snap_to_road`'s full OSRM-then-Google fallback chain
(untested entirely).

## 3. Fix / remediation

Test-only change across three new files (no application code modified):

- `backend/tests/test_location_integrity.py` (16 tests, new file — module had
  none before) — mock-flag rejection, both accuracy-sanity branches
  (zero/over-max), impossible-speed rejection, teleport detection within the
  window, no-teleport for a small jump and for a jump outside the window,
  malformed/empty cached-point handling, both Redis soft-failure paths
  (`redis_get` raising, `redis_set` raising — must never break the caller),
  and the `_haversine_km` helper.
- `backend/tests/test_route_gap_monitor_coverage.py` (11 tests) — `_now()`,
  `_configured_threshold_seconds`'s non-integer and non-positive rejections,
  `_open_gap_event`'s no-op when there's no gap start, a tick that skips an
  id-less ride while still scanning the rest, a tick that counts a
  no-start-time/no-capture ride as `unknown`, and
  `route_gap_monitor_loop`'s three paths (tick succeeds → heartbeat recorded
  → sleep; tick raises → error metric recorded → loop continues; tick raises
  `CancelledError` → re-raised immediately, **not** counted as a failure
  metric).
- `backend/tests/test_route_distance_coverage.py` (52 tests) — every branch
  enumerated in §2: the `max_count < 2` edge of both downsample helpers,
  `_osrm_timestamp`'s three untested branches, `_compute_via_google_roads`'s
  missing-coordinate skip and zero-distance rejection, `_overlapping_chunks`
  validation, `_observed_segment_points`'s three input shapes,
  `compute_segmented_road_route`'s three failure branches,
  `_compute_route_via_osrm`'s exception/short-polyline paths,
  `snap_endpoint_via_osrm`'s five failure branches, both
  `compute_gap_route_via_*` functions' full validation/sanity-gate/dedup
  chains (including non-finite coordinates and collapsed-polyline edges),
  `_decode_encoded_polyline` (Google's own reference example plus a
  truncated-input edge case), `_compute_route_via_google`'s seven branches,
  and `snap_to_road`'s six-scenario OSRM→Google fallback chain.

**One self-review fix during this pass:** an early draft of
`test_open_gap_event_is_a_noop_when_the_decision_has_no_gap_start` patched
`route_gap_monitor.db_supabase.insert_many_ignore_conflicts` with a direct
attribute assignment and a manual `del` in a `finally` block instead of
`monkeypatch.setattr(...)`. That `del` removed the function from the real,
shared `db_supabase` module for the rest of the test process (not just this
test's local view), which broke an unrelated, otherwise-passing test
(`test_e2e_route_tail_recovery.py::test_stranded_tail_recovers_full_route_and_distance_without_touching_fare`)
whenever both files ran together — the ride-route finalizer's breadcrumb
insert silently fell back to a degraded "plain insert" path once the batched
function no longer existed on the module, changing `missing_tail` from
`False` to `True`. Caught by running the new files together with the
existing route-adjacent suite (§9) before committing, not by the standalone
run alone. Fixed by switching to `monkeypatch.setattr`, which pytest
guarantees is restored after the test regardless of outcome.

**No bugs found** in any of the three target files' own application code —
every branch exercised behaves per its own docstring's stated contract
(soft-fail-to-None on any provider error, best-effort Redis writes, replay-
safe monitor tick).

## 4. Risk & impact on existing functionality

**Blast radius: test-only, zero application code touched in any of the three
target files.** Before writing tests: grepped `ACTION_ITEMS.md` for all three
filenames (confirmed they're itemized as open — `location_integrity.py`
explicitly flagged "not in this batch's scope... remains open" when the
zoho-distrecon-obs batch closed alongside it; `route_gap_monitor.py` and
`route_distance.py` itemized together as Batch 12, still open) and ran
`git branch -r | grep a1c-subtier-c` plus `mcp__github__search_pull_requests`
for any open PR mentioning any of the three filenames — no concurrent branch
or PR touches any of them. A stale local branch of this task's own name
(leftover from a prior attempt whose worktree was auto-cleaned) contained no
diff against any of the three target files or new test files and was deleted
before starting fresh.

- `location_integrity.py`'s `check_location_integrity` is called from the
  driver location-update write path (`routes/websocket.py` /
  `routes/drivers/location.py`-equivalent breadcrumb ingestion) on every
  location update — grepped for callers; the new test file only adds
  coverage of the existing function's documented branches, it does not
  change the function's signature, return shape, or Redis key format.
- `route_gap_monitor.py`'s `route_gap_monitor_loop` is one of the 18
  background loops registered in `backend/core/lifespan.py`; the new tests
  mock `route_gap_monitor_tick`, `_record_heartbeat`, `_metric_inc`, and
  `asyncio.sleep` directly — no real Supabase, Redis, or timing dependency,
  and the startup wiring in `lifespan.py` itself was not touched. The
  self-review fix in §3 shows this loop's DB-layer test isolation matters in
  practice: a mis-scoped patch here can silently degrade an unrelated
  ride-route finalizer test (`test_e2e_route_tail_recovery.py`) that shares
  the same `db_supabase.insert_many_ignore_conflicts` function via
  `utils/breadcrumbs.py` — the fix (switching to `monkeypatch.setattr`)
  eliminates that cross-file coupling risk going forward for this test file.
- `route_distance.py`'s functions (`compute_road_route`,
  `compute_segmented_road_route`, `compute_route`, `snap_to_road`,
  `snap_endpoint_via_osrm`, `compute_gap_route_via_osrm`/`_via_google`) are
  called from the ride-completion path, the live-route WebSocket/polling
  endpoint, and the completed-route gap-recovery finalizer
  (`utils/route_finalizer.py`-equivalent) — grepped for callers; all provider
  calls in the new tests are mocked via `httpx.AsyncClient` stand-ins or
  direct `patch.object` on the module's own helper functions, matching the
  existing convention in `test_route_distance.py`/`test_route_distance_osrm.py`.
  No real network call is made by any new test.
- **"Do not silently swallow errors" convention** — the new Redis
  soft-failure tests in `location_integrity.py` and the cache-read/cache-write
  exception tests in `route_distance.py`'s `_compute_route_via_google` assert
  the *existing* code's documented best-effort behavior (both modules'
  docstrings/comments state a Redis or provider outage must never break the
  hot-path caller); no new swallow point was introduced by this pass.

## 5. User-experience effect

None — test-only change, no rider/driver/corporate-admin/internal-admin
facing behavior change of any kind.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_location_integrity.py` | New file — 16 tests | Close the coverage gap on `utils/location_integrity.py` (75.00% → 96%); no prior test file existed |
| `backend/tests/test_route_gap_monitor_coverage.py` | New file — 11 tests | Close the coverage gap on `utils/route_gap_monitor.py` (77.78% → 95%) |
| `backend/tests/test_route_distance_coverage.py` | New file — 52 tests | Close the coverage gap on `utils/route_distance.py` (78.12% → 99%) |
| `ACTION_ITEMS.md` | A1c Sub-tier C — marked Batch 10's `location_integrity.py` and Batch 12 closed with before/after numbers | Track progress per the existing series format |
| `docs/change-log/2026-08-03-a1c-subtier-c-batch-locintegrity-routegap-routedist-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface (GPS integrity is safety-adjacent; route distance feeds billing) |

## 7. Before / after

Not applicable — purely additive test files; no existing application-code
behavior-changing diff to show. (The one in-flight self-review fix described
in §3 is itself a test-only correction — see that section for the concrete
before/after of the patch mechanism.)

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration, no feature flag needed.

## 9. Verification performed

- [x] New test files run alone (per this batch's explicit instruction to
  defer full-suite verification to a later consolidated pass):
  - `pytest tests/test_location_integrity.py -o addopts="" -q` → **16 passed**.
  - `pytest tests/test_route_gap_monitor_coverage.py -o addopts="" -q` → **11 passed**.
  - `pytest tests/test_route_distance_coverage.py -o addopts="" -q` → **52 passed**.
- [x] Each new file run together with its module's pre-existing test
  file(s), with real coverage measurement:
  - `pytest tests/test_location_integrity.py --cov=utils.location_integrity
    --cov-report=term-missing -o addopts=""` → **16 passed**,
    `utils/location_integrity.py` **75.00% → 96%** (52 stmts, 2 missing —
    lines 23-24, the dual-import fallback's primary `try` branch,
    structurally near-impossible to exercise once the module is cached in
    `sys.modules` — same documented pattern as prior Sub-tier B/C files).
  - `pytest tests/test_route_gap_monitor.py
    tests/test_route_gap_monitor_coverage.py --cov=utils.route_gap_monitor
    --cov-report=term-missing -o addopts=""` → **24 passed**,
    `utils/route_gap_monitor.py` **77.78% → 95%** (108 stmts, 5 missing —
    lines 18-22, same dual-import fallback pattern).
  - `pytest tests/test_route_distance.py tests/test_route_distance_osrm.py
    tests/test_route_distance_coverage.py tests/test_compute_route_fallback.py
    tests/test_e2e_route_tail_recovery.py tests/test_live_route.py
    tests/test_maps_eta_osrm.py tests/test_phase_distance_parity.py
    tests/test_trip_distance.py --cov=utils.route_distance
    --cov-report=term-missing -o addopts=""` → **118 passed, 2 skipped**,
    `utils/route_distance.py` **78.12% → 99%** (489 stmts, 3 missing — lines
    55-57, same dual-import fallback pattern).
  - Combined (all three modules' full test surface together, confirming no
    cross-file pollution after the §3 fix): **158 passed, 2 skipped**,
    combined 649 stmts / 10 missing / **98%**.
- [x] Blast-radius grep performed — see §4; every real caller category of
  the three target modules enumerated; `git branch -r` and GitHub PR search
  performed for concurrent work before starting (§4).
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — dual-import
  pattern (respected, not simplified away — the 10 remaining uncovered lines
  across the batch are entirely this pattern's fallback branch), "do not
  silently swallow errors" (asserted existing intentional best-effort
  behavior, did not introduce any new swallow point), patch-target
  convention (`patch.object(li, "redis_get"/"redis_set", ...)` and
  `monkeypatch.setattr(route_gap_monitor.db_supabase, ...)` — the module-level
  binding in the module under test), PIPEDA GPS-logging rule (no debug
  logging added anywhere in the new test files; lat/lng literals appear only
  as ordinary test fixture values, matching the existing convention in
  `test_route_distance_osrm.py`).
- [ ] Full backend suite (`pytest tests/ -q`) — **explicitly deferred per
  this batch's task instructions**, which asked for standalone verification
  of the new files only, to conserve tokens across several concurrent
  coverage-backlog batches; a consolidated full-suite run across all
  in-flight batches is planned separately.
- [ ] Manual repro against real Supabase/Redis/OSRM/Google Roads — not
  applicable; every DB/Redis/HTTP call is mocked throughout, matching this
  test tier's existing convention for all three modules' pre-existing
  suites.
- [ ] Feature-flagged — not applicable; test-only, no deployable behavior
  difference.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed — test-only, every touched/added
  file enumerated in §6, every real caller category of each target module
  enumerated in §4, and the one cross-test-pollution risk found during
  self-review is documented with its concrete fix in §3
- [x] No silent behavior change to an already-shipped flow — zero
  application code modified in this pass

## What was NOT verified

- **The full backend test suite was not run for this batch** — per this
  batch's task instructions, only the three new test files (standalone, and
  combined with each module's pre-existing test files, and finally all
  together) were run. A consolidated full-suite pass across all in-flight
  A1c coverage batches is deferred to a later session, per instruction.
- Not exercised against real Supabase, real Redis, a real OSRM instance, or
  the real Google Roads/Directions APIs — every test mocks the relevant
  client/DB call, consistent with this repo's existing convention for this
  whole test tier (unit, not integration).
- The 10 remaining uncovered lines across the batch (2 in
  `location_integrity.py`, 5 in `route_gap_monitor.py`, 3 in
  `route_distance.py`) are each the primary `try` branch of the module's
  dual-import block. Once a module is imported successfully once per test
  process and cached in `sys.modules`, coverage.py cannot re-attribute those
  exact lines as hit on a second logical "run" within the same process in
  every configuration — the same structurally-near-impossible-to-reach-via-
  this-harness class already documented for other files in this backlog
  (see e.g. the Batch-11 and zoho-distrecon-obs entries in `ACTION_ITEMS.md`).
  Not chased further, per those same prior entries' precedent.
- No visual regression tooling is applicable here — this batch touches
  backend Python only, no frontend surface.
- Whether the wider A1c backlog's other in-flight batches (route-adjacent or
  otherwise) interact with this batch's test files was checked only for the
  specific pollution risk found and fixed in §3, not exhaustively — a fully
  isolated `pytest-randomly`/parallel run across the entire `tests/`
  directory was not performed (explicitly out of scope per this batch's
  deferred-full-suite instruction).
