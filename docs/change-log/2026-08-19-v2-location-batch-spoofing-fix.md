# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | local worktree commit (not yet pushed/PR'd) — see commit SHAs in task report |
| Related issue or gap ID | `docs/audit/2026-08-18-full-fleet-whole-app-audit.md`, ranked blocker #7 ("v2 GPS location-batch path skips spoofing check") |

## 1. Issue / gap identified

The v1 REST single-location-update path and the WebSocket single-ping path both run a GPS
plausibility/spoofing check (`check_location_integrity`) before persisting a driver's position.
The v2 strict-batch REST path (`POST /drivers/location-batch` with a `{ride_id,
recording_session_id, points}` body, handled by `_persist_v2_location_batch` →
`persist_trip_location_batch`) validated coordinate ranges, capture timestamps, and the ride's
time window per point, but ran **no** mock-flag / impossible-speed / accuracy-sanity /
teleportation check at all — a batch of up to 500 points could contain an arbitrary, physically
impossible jump and every point would still be persisted to `driver_location_history`, the table
trip-distance settlement and the SGI per-insurance-period audit trail both read from.

## 2. Root cause

`persist_trip_location_batch` (`backend/utils/breadcrumbs.py:246-397` before this fix) was built
independently of `utils/location_integrity.py` — it has its own validation pipeline (coordinate
range, capture-time sanity, ride-window bounds) that happens to look similar to a spoofing check
but never actually called `check_location_integrity`. Nothing wired the two together, and no test
asserted that it should.

## 3. Fix / remediation

- Added a new pure (no I/O) function `evaluate_gps_plausibility()` to `utils/location_integrity.py`,
  sharing the exact same thresholds/constants and returning the exact same reason codes
  (`mock_location` / `zero_accuracy` / `low_accuracy` / `impossible_speed` / `teleport`) as the
  existing `check_location_integrity()`. `check_location_integrity()` itself is **unchanged** — v1
  and WS keep calling it exactly as before, zero risk of regressing their behavior.
- `persist_trip_location_batch()` now runs `evaluate_gps_plausibility()` on every point that
  survives the existing coordinate/capture-time/window checks, chained across the whole batch: each
  point is compared against the last point that was actually *accepted* before it (not the previous
  point in list order if that one was itself rejected — a rejected point can never become the new
  trusted baseline, mirroring how `check_location_integrity` never overwrites its cached Redis point
  on a failed check).
- Added a new optional `driver_last_known` parameter so the boundary pair — the driver's last known
  DB position *before* this batch → the batch's first point — is checked too, not just pairs inside
  the batch. `_persist_v2_location_batch` (`routes/drivers/location.py`) passes the driver row it
  already fetched (`lat`/`lng`/`updated_at`) — **no extra DB read**.
- A rejected point is added to the batch's existing per-point `rejected` list (same
  `LocationPointRejection` mechanism already used for `invalid_coordinate` /
  `before_ride_window` / etc.) — **per-point rejection, not whole-batch rejection**. This mirrors
  the v2 path's own existing convention (every other validity check in this function already
  rejects individual points and keeps the rest of the batch), which is the more consistent choice
  here than mirroring v1/WS's "reject the live-marker update" behavior, since v2 has no single
  "live marker" concept per call — it is an ordered outbox of many points, and `LocationPointRejection`
  is explicitly documented as "a permanent per-point rejection that an outbox may safely discard."
  A failed check is never silently dropped: the client's ack reports it by `sequence_number` and
  `reason`, same as any other rejection reason.
- A failed check now also logs (`logger.warning`, matching v1/WS's existing convention for
  teleport/mock detection) with `driver_id`, `ride_id`, `sequence_number`, and the short reason code
  — never a raw lat/lng (PIPEDA: "Raw GPS coordinates... must never appear in logs — log geohashed
  area at most"). Covered by `test_v2_invalid_coordinate_never_enters_logs`-style assertions in the
  new tests (`test_v2_batch_rejects_a_physically_implausible_jump_between_its_own_points`, etc. —
  none assert on log content directly, but the log line's format was written to only ever
  interpolate IDs/short codes, never `lat`/`lng`).

### Performance: no per-point I/O added

`check_location_integrity` (used by v1/WS) does 1-2 Redis round trips per call (`redis_get` +
`redis_set`). Calling it once per point in a 500-point batch would have meant up to ~1000 sequential
Redis round trips on this endpoint — a real risk against the 150ms driver-location-write SLA target
even though Redis is much cheaper per call than a DB round trip. **This fix deliberately does not do
that.** `evaluate_gps_plausibility()` is a pure function with zero I/O; the batch loop keeps a
running "previous point" in memory and does exactly one Redis-free pass over the batch. The only
state read from anywhere outside the loop is the single `driver_last_known` dict the caller already
had in hand from its existing driver-row fetch (`routes/drivers/location.py`'s
`_persist_v2_location_batch` already does `driver_rows = await db_supabase.get_rows("drivers", ...)`
before this call) — **zero additional DB or Redis calls**, in or out of the loop.
`test_evaluate_plausibility_never_touches_redis` asserts this directly (patches `redis_get`/
`redis_set` to raise `AssertionError` if called, and confirms the function still returns the correct
result).

Note: this endpoint's batch is typically small in practice (the driver app's outbox flushes every
5-15s, so a normal batch is a handful of points; 500 is the hard cap for a pathological/recovering
client), and the 150ms SLA in `CLAUDE.md` most directly targets the live-marker single-ping write,
not this bulk/background batch endpoint — but the fix was written to add zero I/O per point
regardless, rather than relying on that distinction.

## 4. Risk & impact on existing functionality

**Blast radius — who else calls the changed functions:**

- `evaluate_gps_plausibility` is a brand-new function; nothing else calls it yet, and
  `check_location_integrity` (the function v1/WS actually use) was not modified — zero risk to
  those two call sites (`routes/drivers/location.py`'s legacy/v1 batch handler, and
  `routes/websocket.py` lines ~782 and ~1010).
- `persist_trip_location_batch` gained one new **optional** keyword parameter
  (`driver_last_known: Optional[Dict[str, Any]] = None`) — every existing call site that doesn't
  pass it keeps its old behavior for the boundary check (simply skipped, same as before this fix
  existed), so this is additive, not a signature break. Grepped every caller:
  - `routes/drivers/location.py::_persist_v2_location_batch` — **updated** to pass
    `driver_last_known` (the ranked-blocker-#7 fix target).
  - `routes/drivers/ride_complete.py` (~line 304) — the ride-completion single-fix-point call.
    **Not modified.** It calls with a single point and no `driver_last_known`, so it gets the new
    per-point plausibility check (since the check runs unconditionally inside the loop, not gated on
    `driver_last_known` being present) but not the boundary-pair check against the driver's prior
    position. This is in scope of the general per-point check (a spoofed completion fix with an
    impossible speed/accuracy/mock flag is now also caught, which is a strict improvement, not a
    behavior change to anything that previously passed), but wiring its own `driver_last_known` was
    out of scope for ranked blocker #7 (the audit named only `routes/drivers/location.py:109-170`)
    — flagged here rather than silently left inconsistent; a reasonable fast-follow.
  - Test-only callers (`backend/tests/test_breadcrumb_persistence.py`,
    `test_location_batch.py`) — updated/extended as part of this change, not a production caller.
- `_persist_v2_location_batch`'s own callers: only `update_location_batch` (`routes/drivers/location.py`,
  the `POST /drivers/location-batch` handler) — no other route.

**Could this regress a flow that currently works?**

- A batch entirely of ordinary, closely-spaced points is unaffected — `evaluate_gps_plausibility`
  only rejects on mock flag, `accuracy == 0` or `> 500m`, `speed > ~83.3 m/s (300 km/h)`, or a
  `>10km` jump inside `<10s` elapsed between two accepted points — the same thresholds already live
  and accepted in production for v1/WS traffic. Regression-covered by
  `test_v2_batch_with_all_plausible_points_still_succeeds` and every pre-existing
  `persist_trip_location_batch`/`update_location_batch` test (all still pass — see Verification).
- A driver with no last-known DB position yet (e.g. very first location write of a session) gets no
  boundary check (nothing to compare the first point against) — same "trusted by default on no
  prior data" behavior as `check_location_integrity` when Redis has no cached point.
- `driver_last_known` with missing/invalid `lat`/`lng`/`updated_at` (e.g. a driver row that has
  never had a location write, so `lat`/`lng` are `None`) is guarded — the code only seeds the
  boundary comparison when both coordinates are present, pass `_valid_lat_lng` (not `None`, not the
  `(0, 0)` no-fix default), and `updated_at` parses to a real timestamp; otherwise it behaves exactly
  as if `driver_last_known` were omitted.
- **Interaction with the ride state machine / insurance periods**: none — this only changes which
  `driver_location_history` rows get written; it does not read or write `rides.status`,
  `driver_insurance_periods`, or any wallet/fare table.
- **Interaction with the 16 background loops**: none directly touched. Downstream, fewer spoofed
  breadcrumbs reaching `driver_location_history` means trip-distance settlement and route
  finalization (`route_finalizer.py`) see a cleaner input — a strict improvement, not a new
  dependency.
- A rejected point changes `LocationBatchAck.accepted_count` / `rejected` / (indirectly)
  `acked_through` is unaffected — `acked_through` is computed purely from submitted
  `sequence_numbers`, independent of acceptance, exactly as it already was for the pre-existing
  `invalid_coordinate` etc. rejection reasons.

## 5. User-experience effect

Driver-facing, but only on the (rare) path where a batch point fails the plausibility check: the
driver app's ack response now reports that point as `rejected` (reason `mock_location` /
`zero_accuracy` / `low_accuracy` / `impossible_speed` / `teleport`) instead of silently accepting
and persisting it. Per `LocationPointRejection`'s existing contract ("a permanent per-point
rejection that an outbox may safely discard"), the client is expected to drop it, not retry it — no
new retry storm. Not visible mid-session as any UI change; the driver app does not surface batch ack
rejection reasons to the driver today (best-effort background telemetry). No rider-facing change.
No corporate-admin or internal-admin facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/location_integrity.py` | Added `evaluate_gps_plausibility()` — pure, no-I/O sibling of `check_location_integrity()` sharing its constants/thresholds/reason codes. `check_location_integrity()` itself unchanged. | Lets the batch loop run the same heuristic without a Redis round trip per point. |
| `backend/utils/breadcrumbs.py` | `persist_trip_location_batch()` gained an optional `driver_last_known` parameter and a per-point plausibility check chained across the batch (and the pre-batch boundary pair when `driver_last_known` is supplied). Rejected points join the existing `rejected` list; the running "previous point" only advances on acceptance. | This was the exact gap ranked blocker #7 named (`utils/breadcrumbs.py:246-397`) — the v2 batch path had validation but no spoofing check. |
| `backend/routes/drivers/location.py` | `_persist_v2_location_batch()` now passes `driver_last_known={"lat": ..., "lng": ..., "updated_at": ...}` from the driver row it already fetched. | Wires the fix in at the one route the audit named (`routes/drivers/location.py:109-170`); zero extra DB read since the row was already in hand. |
| `backend/tests/test_breadcrumb_persistence.py` | Added 3 new tests: implausible jump within a batch is caught, an all-plausible batch still succeeds, and a jump from the driver's pre-batch last-known position is caught (and a rejected point doesn't become the new trusted baseline). | Required test coverage per the task. |
| `backend/tests/test_location_integrity_coverage.py` | Added unit tests for `evaluate_gps_plausibility()` covering every reason code, the no-prev-point/zero-elapsed/elapsed-too-large skip paths, and an explicit assertion that it never touches Redis. | Direct coverage of the new pure function. |
| `backend/tests/test_location_batch.py` | Updated `test_v2_batch_persists_before_updating_the_live_marker`'s mock `persist()` signature to accept the new `driver_last_known` keyword (and assert its value), since the real call site now always passes it. | Pre-existing test's mock had a fixed keyword-only signature (`*, active_ride`) that would otherwise raise `TypeError` on the new kwarg. |

## 7. Before / after

```python
# Before — backend/utils/breadcrumbs.py, persist_trip_location_batch(), per-point loop
        monotonic_ms = point.get("monotonic_ms")
        if monotonic_ms is not None and (isinstance(monotonic_ms, bool) or not isinstance(monotonic_ms, int)):
            rejections.append(LocationPointRejection(sequence_number, "invalid_monotonic_time"))
            continue

        rows.append({...})   # <-- no GPS plausibility check anywhere in this function
```

```python
# After
        monotonic_ms = point.get("monotonic_ms")
        if monotonic_ms is not None and (isinstance(monotonic_ms, bool) or not isinstance(monotonic_ms, int)):
            rejections.append(LocationPointRejection(sequence_number, "invalid_monotonic_time"))
            continue

        elapsed_seconds = (
            (captured_at - prev_captured_at).total_seconds() if prev_captured_at is not None else None
        )
        trusted, integrity_reason = evaluate_gps_plausibility(
            lat, lng,
            prev_lat=prev_lat, prev_lng=prev_lng, elapsed_seconds=elapsed_seconds,
            speed=point.get("speed"), accuracy=point.get("accuracy"), mocked=point.get("mocked"),
        )
        if not trusted:
            logger.warning("location-batch point failed GPS plausibility check "
                            "driver_id=%s ride_id=%s sequence_number=%s reason=%s",
                            driver_id, ride_id, sequence_number, integrity_reason)
            rejections.append(LocationPointRejection(sequence_number, integrity_reason or "gps_implausible"))
            continue
        prev_lat, prev_lng, prev_captured_at = lat, lng, captured_at   # only a trusted point advances the baseline

        rows.append({...})
```

## 8. Rollback plan

No feature flag was added — the audit rated this a security/fraud gap that should not ship dark
(unlike a UX-visible change, a silently-open GPS-spoofing hole in a live-tested surface is the risk
side of the additive/flagged tradeoff, not the safe side). Rollback options if this regresses:

- **Fastest**: `git revert` the commit. This is a pure code change with **no migration, no schema
  change, and no data mutation** — reverting removes the check and restores the exact prior
  behavior (accept every point that passes the pre-existing coordinate/window checks). Unlike a
  fix that touches ride state or a wallet delta, this is safe as a plain code revert per CLAUDE.md's
  "`git revert` is not a rollback plan for anything already applied to live data" caveat — no live
  data (Stripe charges, wallet deltas, ride state) is written or mutated by this change; the only
  effect of reverting is that some GPS points that were being rejected go back to being accepted.
- **No redeploy needed to reduce impact in the interim**: if a false-positive rejection rate turns
  out to be a real problem in production before a revert can ship, the same thresholds
  (`MAX_SPEED_KMH`, `MAX_ACCURACY_METERS`, `TELEPORT_THRESHOLD_KM`, `TELEPORT_MIN_SECONDS` in
  `utils/location_integrity.py`) already govern v1/WS traffic today, so a widening there would need
  its own review — not attempted here, out of scope.

## 9. Verification performed

- [x] Automated tests run — unit only (no integration/e2e for this module; none exist for
  `utils/breadcrumbs.py`/`utils/location_integrity.py` beyond unit-level mocked-Supabase/Redis
  tests). Ran via `/tmp/spinr-venv/bin/pytest` (a pre-existing venv in this environment):
  - `backend/tests/test_location_batch.py`
  - `backend/tests/test_location_integrity.py`
  - `backend/tests/test_location_integrity_coverage.py`
  - `backend/tests/test_breadcrumb_persistence.py`
  - `backend/tests/test_breadcrumbs_late_tail.py`
  - `backend/tests/test_p3_background_location.py`
  - `backend/tests/test_live_breadcrumbs.py`
  - `backend/tests/test_location_batch_revoked_session.py`
  - Result: **113 passed, 1 xfailed, 0 failed** (the 1 pre-existing `xfail` is unrelated to this
    change).
  - Also ran the broader touch-adjacent set: `test_dual_import_parity.py` (confirms the dual-import
    try/except pattern in `utils/breadcrumbs.py` still round-trips correctly),
    `test_ride_complete_coverage.py`, `test_ride_completion_location.py`,
    `test_active_ride_cache.py`, `test_phase_distance_parity.py`,
    `test_period1_accumulation_endpoint.py` — all passed, confirming the `ride_complete.py` caller
    and the Period-1 distance/active-ride-cache code paths that also touch `breadcrumbs.py` are
    unaffected.
- [x] `ruff check` run on every modified file — all clean.
- [ ] Manual repro steps followed in staging — **not performed**; no staging Supabase/Redis access
  in this sandbox (see "What was NOT verified" below).
- [x] Blast-radius grep performed — see section 4 above; every caller of `persist_trip_location_batch`
  and `evaluate_gps_plausibility` enumerated by name.
- [x] Reviewed against relevant CLAUDE.md conventions — PIPEDA logging (no raw GPS in the new log
  line), "do not silently swallow errors" (a failed check is reported via `rejected`, not dropped;
  a genuine persistence failure below this point still raises/503s exactly as before, unchanged),
  dual-import pattern (unchanged, still round-trips), Performance SLA (see section 3's "no per-point
  I/O added" — the driving design constraint of this fix).
- [ ] Feature-flagged — **not flagged**; see Rollback plan above for the reasoning (this closes a
  fraud/spoofing gap rather than changing a UX-visible flow, and `git revert` is a safe, complete
  rollback since no data is mutated).
- **Was a real production build run?** N/A — this is a `backend/` (Python) change only; no
  `admin-dashboard`/`rider-app`/`driver-app` (`npm run build`) surface was touched.
- **Was pytest run via a venv?** Yes — `/tmp/spinr-venv/bin/pytest`, a pre-existing virtualenv found
  in this environment (not created fresh for this task).

## 10. What was NOT verified

- **No live Supabase/Redis exercised** — all tests use the repo's standard mocked
  `db_supabase`/`redis_get`/`redis_set` patches; nothing was run against a real database or Redis
  instance, staging or otherwise.
- **No real driver-app client tested against this endpoint** — the fix is verified at the
  unit-test level (function-in, ack-out) only; no end-to-end call through FastAPI's routing/auth
  layer, and no real device GPS trace was replayed against it.
- **Latency was not measured** — the "no per-point I/O" claim is verified structurally (the new
  function is provably pure — `test_evaluate_plausibility_never_touches_redis` asserts this by
  making any Redis call raise) and by code review of the loop, not by benchmarking the endpoint's
  actual P95 before/after under load. No load/perf test exists for this endpoint in this repo
  (`E2` — load/chaos testing is a standing, tracked gap per `docs/audit/2026-08-18-full-fleet-whole-app-audit.md`).
- **`routes/drivers/ride_complete.py`'s single-completion-point call site was not given its own
  `driver_last_known` wiring** — flagged explicitly in section 4 as a reasonable fast-follow, not
  silently left inconsistent, but out of the ranked-blocker-#7 scope (the audit named only
  `routes/drivers/location.py:109-170`).
- **The WebSocket batch handler's own similar gap was not touched** — `routes/websocket.py`'s
  `location_batch` message type calls `persist_ride_breadcrumbs()` for the *whole* batch but only
  runs `check_location_integrity()` on the *last* point (for the live-marker update), meaning
  earlier points in a WS batch still get no plausibility check. This is a similar-shaped gap but a
  **different code path** than the one ranked blocker #7 named (`routes/drivers/location.py:109-170`,
  `utils/breadcrumbs.py:246-397`) and was left out of scope for this fix rather than silently
  bundled in — flagging it here so it isn't lost.
