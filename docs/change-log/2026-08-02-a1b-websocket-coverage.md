# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch (WS auth + fan-out is dispatch/rides-adjacent) |
| PR / commit link | (this branch: `claude/websocket-coverage`) |
| Related issue or gap ID | ACTION_ITEMS.md A1b Track 2, Sub-tier A |

## 1. Issue / gap identified

`backend/routes/websocket.py` (WS handshake/auth, the ~660-line message
receive loop, heartbeat/token-revocation re-checks, and driver-disconnect
handling) sat at 50.44% coverage (569 statements, 282 missing) per the
Track 2 full-repo scoping pass — flagged Sub-tier A alongside
`repositories/ride_repo.py` for being dispatch/rides-adjacent despite
technically living in the "breadth" track. Nine existing WS test files
covered auth handshake edge cases, per-user rate limiting, live-location
durability/revocation guards (structurally, not behaviourally — see that
file's own header note that "the endpoint has no seam to drive directly"),
health, and fanout metrics — but none exercised the main receive loop's
message-type branches end-to-end (size guard, malformed JSON,
`driver_location`/`location_batch` persistence and fan-out,
`ride_status_update`/`chat_message`/`get_nearby_drivers`/admin-snapshot
handling), the disconnect/exception cleanup tail, or several
`heartbeat_task`/`_handle_driver_ws_disconnect` edge branches.

## 2. Root cause

No prior session had driven the receive loop itself through
`TestClient.websocket_connect`, likely because of the handshake's mocking
surface (Firebase/JWT verification, driver-profile lookup, admin-role
gate, presence, and heartbeat all fire before the loop is even reachable).
The gap concentrated in: the message-size and malformed-JSON guards, the
`driver_location`/`location_batch` happy-path persistence and rider/admin
fan-out (throttled DB write, breadcrumb buffering with the session-revoked
skip, ETA cache hit/miss), the batch rate-limit and session-revoked-ack
paths, the `WebSocketDisconnect`/generic-`Exception` cleanup tail
(breadcrumb flush, throttle-slot release), and several `heartbeat_task` /
`_handle_driver_ws_disconnect` branches (stale-pong close, token-version
and Firebase-watermark revocation closes, the "newer socket already
reconnected" skip, the idle-driver-skips-broadcast branch, and the
best-effort audit-log-failure-still-broadcasts branch).

## 3. Fix / remediation

Test-only change. Added `backend/tests/test_websocket_coverage.py` (35
tests) driving the WS endpoint end-to-end via `TestClient.websocket_connect`
for every reachable receive-loop branch listed above, plus direct
unit-level tests for `_handle_driver_ws_disconnect`, `heartbeat_task`,
`_read_token_version`, `_read_firebase_session_revoked`, and the two small
pure functions `_parse_live_coordinate`/`_valid_live_coordinates`. No
application code changed.

One real testing subtlety worth recording: sending `location_batch` with
`points: []` never produces a `location_batch_ack` response — the ack is
nested *inside* the `if driver_id and isinstance(points, list) and points:`
block, so an empty (but well-typed) list silently no-ops instead of acking
with `count: 0`. This is a genuine minor behavioral quirk (see §11 —
**bug noted, not fixed** per this session's scope) discovered while writing
these tests; a first draft of two tests relied on an empty-list round-trip
as a "no crash" liveness probe and consistently blocked for the full 10 s
heartbeat interval before receiving a stray `ping` instead of the expected
ack. Fixed in the tests by using a non-list `points` value (`"not-a-list"`)
for the early-guard ack path instead, which does not exercise the
empty-list gap.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** New test file only. Grepped for every real
  caller/consumer:
  - `backend/server.py:150` — `from routes.websocket import router as
    websocket_router`, the only mount point (`app.include_router(...)`).
  - `backend/socket_manager.py` — the `ConnectionManager` class this module
    calls into (`manager.connect`, `.send_personal_message`,
    `.broadcast_to_admins`, `.broadcast_driver_location_to_admins`,
    `.update_driver_location`, `.note_user_message`,
    `.forget_driver_location_throttle`) is a dependency *of*
    `routes/websocket.py`, not a caller of it — no reverse blast radius.
  - `backend/utils/breadcrumb_buffer.py`, `backend/utils/breadcrumbs.py`,
    `backend/utils/location_integrity.py`,
    `backend/utils/session_revocation.py`, `backend/utils/driver_presence.py`,
    `backend/utils/maps_eta.py`, `backend/settings_loader.py` — all
    dependencies *of* this module, called through mocked seams in the new
    tests; none of their own call sites were touched.
  - No other route module imports from `routes/websocket.py` directly
    (verified via `grep -rn "from routes.websocket import\|from
    .websocket import"` across `backend/`) — the router mount in
    `server.py` is the sole external caller of `websocket_endpoint`.
- **Ride state machine**: not touched — this file only *reads* `ride.status`
  to decide fan-out eligibility (`_RIDER_LOCATION_STATUSES`,
  `_ETA_PICKUP_STATUSES`) and to echo the canonical DB status back to
  participants (`ride_status_update` handler); it never writes ride state.
  New tests assert the existing read-only behavior, not a change to it.
- **Insurance periods / presence**: `mark_present`/`clear_presence` and the
  driver-disconnect admin-broadcast path are exercised by the new tests
  purely as mocked seams — no change to when/how presence is written.
- **Money-adjacent**: none — this module contains no fare or Decimal
  arithmetic.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_websocket_coverage.py` | New file — 35 tests | Close coverage gap on `routes/websocket.py` (50.44% → 80.3%) |
| `docs/change-log/2026-08-02-a1b-websocket-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface (dispatch/rides realtime channel) |
| `ACTION_ITEMS.md` | Updated Track 2 Sub-tier A's `routes/websocket.py` bullet to "done, 80.3%" | Track progress per the existing series format (matches the `ride_repo.py` entry style) |

## 7. Before / after

Not applicable — purely additive test file; no existing behavior-changing
diff in `routes/websocket.py` itself.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_websocket_coverage.py -q --no-cov` — 35 passed.
- [x] Coverage measured: `pytest tests/ -q --cov=routes.websocket --cov-report=json:cov_ws.json --no-cov-on-fail` (full suite, matching how the 50.44% baseline was measured) — **routes/websocket.py: 80.32%** (up from 50.44%), 569 statements, 112 missing (down from 282).
- [x] Full backend suite run: `pytest tests/ -q --no-cov` — `6865 passed, 8 skipped, 1 xfailed, 0 failed` (482.71s) — zero regressions; the previously-noted pre-existing flaky
  `test_two_drivers_accepting_same_ride_one_wins` did not trigger on this run.
- [ ] Manual repro / staging check — not applicable, test-only change with no deployable behavior difference.
- [x] Blast-radius grep performed: see section 4 above, every real caller/dependency enumerated.
- [x] Reviewed against CLAUDE.md conventions: patch target is
  `backend.routes.websocket.<name>` (the module under test's own bindings,
  matching the house style already established in `test_websocket_auth.py`
  / `test_websocket_live_location.py`), not `backend.socket_manager.*` or
  `backend.db_supabase.*` directly.
- [ ] Feature-flagged — not applicable, test-only.

## 10. What was NOT verified

- Not run against real Supabase, real Redis, real Firebase, or a real
  WebSocket transport over the network — mocked throughout via
  `unittest.mock`/`TestClient.websocket_connect`, matching this repo's
  existing WS test convention.
- The remaining 112 uncovered lines are concentrated in: the module-load
  dual-import fallback branches (lines 12-14, 23-24, 30-32 — only reachable
  when imported outside the `backend` package, exercised by neither this
  file nor any other in the suite, same as every other module's dual-import
  except branch), the `get_nearby_drivers` handler's non-kill-switch
  in-radius-drivers path (lines 1265-1291 — requires mocking
  `dispatch_geo_bounds`, `intent_online`, `calculate_distance`, and
  `prematch_driver_list` together; judged lower marginal value than the
  branches already closed, since `/drivers/nearby`'s REST equivalent
  already carries its own dedicated test coverage of that same logic), a
  handful of `logger.debug`/`logger.warning` exception-swallow lines inside
  already-covered `try/except` blocks (e.g. 897, 908-909, 963-968,
  1071-1075 — the success path and the outer exception-handling contract
  are both covered; only the specific inner log-line execution isn't), and
  the `finally` block's own breadcrumb-flush-failure branch (1379-1380 —
  covered path is flush-success; the flush-raises-and-is-logged branch
  is not separately pinned). Not pursued further in this pass.
- No load/concurrency testing of the WS rate limiter or batch throttle
  beyond what `test_websocket_per_user_rate_limit.py` already covers.

## 11. Bugs noted but NOT fixed (per task scope — test-only pass)

- **`location_batch` with an empty `points: []` list never sends a
  `location_batch_ack` response.** The early guard
  (`if not isinstance(points, list): ack 0; continue`) only fires for a
  non-list payload; a well-typed empty list falls through to
  `if driver_id and isinstance(points, list) and points:`, which is False
  for an empty list, skipping the entire block — including the
  `await websocket.send_json({"type": "location_batch_ack", "count":
  inserted})` call, which lives *inside* that same `if`. A client that
  legitimately flushes an empty offline-recovery batch (e.g. after a
  successful earlier flush leaves nothing buffered) gets no
  acknowledgment at all and will sit waiting until the next heartbeat
  ping or its own client-side timeout. Not a regression from this PR —
  pre-existing behavior, first observed while writing
  `test_location_batch_non_list_points_acks_zero` and its neighbors.
  Recommend filing as a small follow-up: either move the ack outside the
  `if` guard (send `count: 0` immediately for an empty-but-valid list) or
  explicitly document the empty-list case as an intentional no-op if
  that's the desired behavior.
