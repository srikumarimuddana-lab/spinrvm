# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/c64-ws-location-batch-plausibility` (see PR link in task report) |
| Related issue or gap ID | `ACTION_ITEMS.md` C64 ("WebSocket `location_batch` handler only integrity-checks the last point in a batch, not every point"), split 2026-09-04 from A40's finding #7; see also `docs/change-log/2026-08-19-v2-location-batch-spoofing-fix.md` (the REST v2 precedent this fix mirrors) |

## 1. Issue / gap identified

`backend/routes/websocket.py`'s `location_batch`/`driver_location_batch` message handler ran
`check_location_integrity()` only on the *last* point of an uploaded batch (used for the live
driver marker). Every earlier point in the same WS batch was persisted via
`persist_ride_breadcrumbs` with **no spoofing/plausibility check at all** — a spoofed or
physically-impossible earlier point could reach `driver_location_history`, the table trip-distance
settlement and the SGI insurance-period audit trail both read from.

## 2. Root cause

The REST v2 `POST /drivers/location-batch` path had this exact gap and was fixed on 2026-08-19
(`docs/change-log/2026-08-19-v2-location-batch-spoofing-fix.md`) by adding a new pure function,
`evaluate_gps_plausibility()`, and sweeping it across every consecutive point pair inside
`persist_trip_location_batch()`. That fix's own "What was NOT verified" section explicitly named
the WS batch handler as a similar-shaped but out-of-scope gap, since it is a different code path
(`routes/websocket.py`'s legacy-point branch of `persist_ride_breadcrumbs`, not
`persist_trip_location_batch`) — nothing had wired the two together for WS traffic. C64 tracks
that follow-up.

## 3. Fix / remediation

- Added `_filter_plausible_batch_points(driver_id, points)` to `backend/routes/websocket.py` —
  the same chained-sweep pattern as the REST v2 fix, using the same pure, no-I/O
  `evaluate_gps_plausibility()` function (`utils/location_integrity.py`, unchanged by this fix).
  Each point in the batch is checked against the last point that actually *passed* before it (not
  simply the previous point in list order) — a rejected point never becomes the new trusted
  baseline, exactly mirroring `persist_trip_location_batch`'s behavior.
- The WS `location_batch`/`driver_location_batch` handler now calls this filter on `dict_points`
  and passes the **filtered** list to `persist_ride_breadcrumbs`, instead of the raw list.
- The **existing** `check_location_integrity()` call on `last_pt` (the live-marker write, a few
  lines below) is untouched: it still reads `dict_points[-1]` — the raw, unfiltered last point —
  exactly as before. This fix is additive to the breadcrumb-persist path only, per the task's
  explicit instruction and C64's acceptance criteria ("no behavior change to the live-marker
  write").
- A point whose coordinates can't be parsed is passed through unfiltered (not rejected by this
  sweep) — `persist_ride_breadcrumbs`'s own existing coordinate validation is still the authority
  for that case, matching how the REST v2 fix layers its plausibility check *after* its own
  coordinate/window checks rather than duplicating them.
- A rejected point is logged (`logger.warning`) with `driver_id` and the short reason code only —
  never raw lat/lng, per PIPEDA ("Raw GPS coordinates... must never appear in logs").

## 4. Risk & impact on existing functionality

**Blast radius — who else calls the changed code:**

- `_filter_plausible_batch_points` is a brand-new function with exactly one call site (the WS
  `location_batch`/`driver_location_batch` branch in `routes/websocket.py`). Nothing else calls it.
- `evaluate_gps_plausibility()` itself is **unchanged** (already shared with the REST v2 path since
  2026-08-19) — this fix adds a second caller, not a modified signature or behavior.
- `persist_ride_breadcrumbs()` itself is **unchanged**. Grepped every caller (3 total):
  - `routes/websocket.py` line ~1061 (this fix's target) — now receives a filtered list instead of
    the raw batch.
  - `utils/breadcrumb_buffer.py`'s `buffer_ride_breadcrumb`/flush path (the single-ping buffering
    flow) — **not touched**. It calls `persist_ride_breadcrumbs` with its own internally-buffered
    points, a separate code path from the `location_batch` message type this fix targets. Its
    points still get zero plausibility check — out of scope for C64, which named only the
    `location_batch`/`driver_location_batch` handler.
  - `routes/drivers/location.py` line ~738 (REST v1 legacy batch handler) — **not touched**, same
    reasoning: C64's `Files` field named only `routes/websocket.py`.
  - Deliberately scoped this way (surgical change, matching the REST v2 fix's own precedent of
    adding a new function/call site rather than modifying the shared `persist_ride_breadcrumbs`
    which all three callers share) — modifying `persist_ride_breadcrumbs` itself would have
    widened the blast radius to the single-ping buffer flow and the REST v1 legacy path, which are
    both out of C64's stated scope and not exercised by this task's tests.
- `check_location_integrity()` — **not modified**, not touched at all by this diff (its call site
  in the WS handler for the live-marker write is unchanged; its call site for single `driver_location`
  pings, ~line 850, is also untouched).

**Could this regress a flow that currently works?**

- A batch entirely of ordinary, closely-spaced points is unaffected — `evaluate_gps_plausibility`
  only rejects on mock flag, `accuracy == 0` or `> 500m`, `speed > ~83.3 m/s (300 km/h)`, or a
  `>10km` jump inside `<10s` elapsed between two accepted points, the same thresholds already
  governing v1/WS single-ping and REST v2 batch traffic in production today. Regression-covered by
  `test_filter_plausible_batch_points_all_plausible_points_pass_through` and every pre-existing
  `location_batch` test in `test_websocket_coverage.py` / `test_websocket_live_location.py` (all
  still pass — see Verification).
- An empty filtered list (all points rejected) is handled the same as an already-existing empty-list
  path: `persist_ride_breadcrumbs([])` returns `0` (its own existing early-return), so `inserted`
  becomes `0` and the client's ack reports `count: 0` — no new failure mode, no exception.
- The live-marker fan-out to riders/admins (below the persist call) is **unaffected**: it still
  reads `dict_points[-1]` directly, not the filtered list, so a batch whose last point happens to be
  implausible still gets the exact same `check_location_integrity()` gate it always had — this fix
  changes nothing about that path's behavior, positive or negative.
- **Interaction with the ride state machine / insurance periods**: none — this only changes which
  `driver_location_history` rows get written from a WS batch; it does not read or write
  `rides.status`, `driver_insurance_periods`, or any wallet/fare table.
- **Interaction with background loops**: none directly touched. Downstream, fewer spoofed WS
  breadcrumbs reaching `driver_location_history` is a strict improvement for trip-distance
  settlement and `route_finalizer.py`'s input, not a new dependency.
- **Performance**: `evaluate_gps_plausibility()` is a pure, no-I/O function (no Redis, no DB) — the
  sweep runs once in memory over the WS batch (capped at the same rate-limited size as before this
  fix; no new cap was added or needed). No new Redis/DB round trips are added to the
  driver-location-write path, so the 150ms location-write SLA target in `CLAUDE.md` is not at risk.

## 5. User-experience effect

Driver-facing, but only on the (rare) path where a non-last batch point fails the plausibility
check: that point is silently excluded from `driver_location_history` (the WS batch ack only ever
reported a total `count`, never per-point detail, so this is not a new visible signal — the ack
count simply reflects fewer accepted points than before, same as it already does for points
`persist_ride_breadcrumbs` itself rejects for invalid coordinates or a stale ride window). Not
visible mid-session as any UI change; the driver app does not surface per-point rejection reasons.
No rider-facing change (the live-marker/ETA fan-out is unaffected, per section 4). No
corporate-admin or internal-admin facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/websocket.py` | Added `_filter_plausible_batch_points()` (new helper, chained `evaluate_gps_plausibility()` sweep matching the REST v2 pattern) and wired it into the `location_batch`/`driver_location_batch` handler so `persist_ride_breadcrumbs` receives the filtered list instead of the raw batch. Added `evaluate_gps_plausibility`/`parse_iso_utc` to the dual-import block (both try/except branches). The existing `check_location_integrity()` call on `last_pt` is unchanged. | This was the exact gap C64 named — the WS batch path checked only the last point, unlike the REST v2 path fixed 2026-08-19. |
| `backend/tests/test_websocket_coverage.py` | Added 3 new tests: a unit test proving an implausible non-last point is dropped and does not become the new trusted baseline for later points, a no-regression test for an all-plausible batch, and an end-to-end WS test proving the breadcrumb persist only receives the plausible points while the live-marker `check_location_integrity()` call still runs against the raw, unfiltered last point. | Required regression coverage per C64's acceptance criteria and the task. |
| `docs/change-log/2026-09-04-c64-ws-location-batch-plausibility.md` | This file. | Mandatory Change Impact & Risk Log for a regulatory GPS-trace-integrity fix on a live-tested surface. |

## 7. Before / after

```python
# Before — backend/routes/websocket.py, location_batch/driver_location_batch handler
                    # Shared persistence with the REST path: server-derived phase
                    # per point (from its own timestamp vs the ride milestones),
                    # stale / other-ride discard, and the 500-point cap. Never
                    # trusts the client's ride_id/tracking_phase.
                    inserted = await persist_ride_breadcrumbs(driver_id, dict_points)
                    # Live marker from the most recent point (best-effort). ...
                    last_pt = dict_points[-1]
```

```python
# After
                    # Shared persistence with the REST path: server-derived phase
                    # per point (from its own timestamp vs the ride milestones),
                    # stale / other-ride discard, and the 500-point cap. Never
                    # trusts the client's ride_id/tracking_phase.
                    # C64: sweep every consecutive point pair for GPS
                    # plausibility BEFORE the breadcrumb persist -- previously
                    # only the last point (below, for the live marker) was
                    # checked, so an implausible earlier point in the batch
                    # reached the regulatory GPS-trace record unchecked. Uses
                    # the original (unfiltered) dict_points for the live
                    # marker below, unchanged.
                    plausible_points = _filter_plausible_batch_points(driver_id, dict_points)
                    inserted = await persist_ride_breadcrumbs(driver_id, plausible_points)
                    # Live marker from the most recent point (best-effort). ...
                    last_pt = dict_points[-1]
```

## 8. Rollback plan

No feature flag was added — like the REST v2 precedent this mirrors, the audit/backlog item rates
this a security/GPS-integrity gap on a regulatory trace that should not ship dark, and this is a
pure code change with **no migration, no schema change, and no data mutation of anything already
written**. Rollback options if this regresses:

- **Fastest**: `git revert` the commit. Reverting removes the filter call and restores the exact
  prior behavior (the raw, unfiltered batch is passed to `persist_ride_breadcrumbs`, which still
  runs its own pre-existing coordinate/window/mocked-flag checks — this is not a return to a
  completely unchecked state, just to the pre-C64 level of checking). No live data (Stripe charges,
  wallet deltas, ride state, or already-persisted breadcrumb rows) is touched by this change or its
  reversion — `git revert` is a complete, safe rollback per `CLAUDE.md`'s caveat that a plain
  revert is *not* sufficient only for changes already applied to live data, which this is not.
- **No redeploy needed to reduce impact in the interim**: the same thresholds
  (`MAX_SPEED_KMH`, `MAX_ACCURACY_METERS`, `TELEPORT_THRESHOLD_KM`, `TELEPORT_MIN_SECONDS` in
  `utils/location_integrity.py`) already govern v1/WS single-ping and REST v2 batch traffic today,
  so if a false-positive rejection rate turns out to be a real problem, the same widening
  discussion applies fleet-wide, not to this change in isolation — not attempted here, out of
  scope.

## 9. Verification performed

- [x] Automated tests run — unit and endpoint-level (via FastAPI `TestClient` over a real
  WebSocket connection with mocked dependencies), no integration/e2e for this module (none exist
  for `routes/websocket.py` beyond the mocked-Supabase/Redis tier). Ran via
  `/tmp/spinr-venv/bin/pytest` (pre-existing venv in this environment, `--no-cov` to skip the
  global coverage gate irrelevant to a targeted run):
  - `backend/tests/test_websocket_coverage.py` (includes the 3 new tests)
  - `backend/tests/test_websocket_live_location.py`
  - `backend/tests/test_breadcrumb_persistence.py`
  - `backend/tests/test_location_integrity_coverage.py`
  - `backend/tests/test_location_batch.py`
  - `backend/tests/test_idle_location_batch.py`
  - `backend/tests/test_location_batch_revoked_session.py`
  - `backend/tests/test_websocket_auth.py`, `test_websocket_auth_ack.py`,
    `test_websocket_per_user_rate_limit.py`, `test_websocket_token_revocation.py`
  - Also ran the task's specified filter directly: `pytest tests/ -k "location_batch or websocket
    or gps_plausibility"` — **138 passed, 1 skipped** (the 1 skip is a pre-existing, unrelated RLS
    test that self-skips without a real Postgres connection).
  - Combined targeted run above: **114 passed, 0 failed**.
- [x] `ruff check` run on both modified Python files — clean (one pre-existing, unrelated `F841` in
  `test_websocket_coverage.py` at a different test not touched by this diff, confirmed via `git
  stash` to predate this change).
- [x] `ruff format --check` run on both modified files — already formatted, no changes needed.
- [ ] Manual repro steps followed in staging — **not performed**; no staging Supabase/Redis/live
  WebSocket device access in this sandbox (see "What was NOT verified" below).
- [x] Blast-radius grep performed — see section 4 above; every caller of `persist_ride_breadcrumbs`
  and every reference to `check_location_integrity`/`evaluate_gps_plausibility` in
  `backend/routes/` and `backend/utils/` enumerated by name.
- [x] Reviewed against relevant `CLAUDE.md` conventions — PIPEDA logging (no raw GPS in the new log
  line), "do not silently swallow errors" (a rejected point is dropped from the persist list, not
  silently mis-attributed; a genuine persistence failure below this point still raises/surfaces
  exactly as before, unchanged), dual-import pattern (both try/except branches updated identically,
  verified in the diff), GPS-trace regulatory retention (`regulatory-sk.md` / CLAUDE.md's
  "Saskatchewan Regulatory" section — this fix strengthens, not weakens, the integrity of what gets
  retained as the 3-year GPS-trace / 7-year insurance-period audit record).
- **Was a real production build run?** N/A — this is a `backend/` (Python) change only; no
  `admin-dashboard`/`rider-app`/`driver-app` (`npm run build`) surface was touched.
- **Was pytest run via a venv?** Yes — `/tmp/spinr-venv/bin/pytest`, a pre-existing virtualenv
  found in this environment (not created fresh for this task).

## 10. What was NOT verified

- **No live Supabase/Redis exercised** — all tests use the repo's standard mocked
  `db_supabase`/Redis patches and FastAPI's in-process `TestClient` WebSocket harness; nothing was
  run against a real database, Redis instance, or network WebSocket connection, staging or
  otherwise.
- **No real driver-app client or real device GPS trace tested against this handler** — verified at
  the unit level (`_filter_plausible_batch_points` called directly with synthetic points) and at
  the FastAPI `TestClient` WebSocket level (full message round-trip through the real handler code
  with dependencies mocked), but no end-to-end mobile client, no real WebSocket load, and no replay
  of an actual recorded GPS trace.
- **Latency was not measured** — the "no new I/O" claim is verified structurally (the new function
  calls only the already-pure `evaluate_gps_plausibility`, itself already proven I/O-free by the
  REST v2 fix's `test_evaluate_plausibility_never_touches_redis`) and by code review of the loop,
  not by benchmarking the WS endpoint's actual P95 before/after under load. No load/perf test exists
  for this WS handler in this repo.
- **`utils/breadcrumb_buffer.py`'s single-ping buffering flow and `routes/drivers/location.py`'s
  REST v1 legacy batch handler were not given this same sweep** — both remain unchanged, as flagged
  explicitly in section 4; C64's `Files` field named only `routes/websocket.py`'s
  `location_batch`/`driver_location_batch` handler, so widening to those two other
  `persist_ride_breadcrumbs` callers was treated as out of scope for this fix rather than silently
  left inconsistent. Flagging here so it isn't lost, mirroring how the original 2026-08-19 fix
  flagged this WS gap rather than silently bundling it in.
