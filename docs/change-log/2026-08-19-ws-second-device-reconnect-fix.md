# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch (also touches rides/drivers realtime paths generally) |
| PR / commit link | local worktree commit only — not pushed/opened as a PR (see task instructions) |
| Related issue or gap ID | `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` ranked blocker #15 ("Second device silently steals WS connection") |

## 1. Issue / gap identified

When a second device/tab for the same rider or driver opened a new WebSocket connection, `ConnectionManager.connect()` in `backend/socket_manager.py` overwrote the `active_connections["{driver|rider}_{user_id}"]` dict slot with the new socket and sent **no signal at all** to the old one. The old (now-orphaned) socket stayed open on the client, believing it was still receiving live ride/dispatch updates, with no server-initiated close and no error frame. Independently confirmed by 4 separate audit agents (realtime, edge-case, security, dispatch) as reproducible, not theoretical.

## 2. Root cause

`connect()` was a pure `dict[client_id] = websocket` overwrite with no bookkeeping for whatever object it replaced:

```python
async def connect(self, websocket: WebSocket, client_id: str):
    # WebSocket is already accepted in the endpoint handler
    self.active_connections[client_id] = websocket
    ...
```

There was no reference kept to the previous socket, so it was simply garbage — never explicitly closed. The old connection's own receive loop (`routes/websocket.py`) would eventually notice the TCP connection is idle/dead only via the 30s heartbeat/kernel keepalive, or never, if the client-side process was still alive and just not the active tab.

## 3. Fix / remediation

`connect()` now captures the previous socket registered under `client_id` (if any) before overwriting the dict, and — once the new socket has taken the slot — explicitly closes the old one with a new dedicated close code, `WS_CLOSE_REPLACED_BY_NEW_CONNECTION = 4409` (RFC 6455 private-use range 4000-4999; chosen to echo HTTP 409 Conflict, since two connections are conflicting for one registry slot). The close is wrapped in a narrow `try/except Exception` that logs at `debug` (not swallowed silently, not `except: pass`) — the old socket being already dead is an expected, common case (network drop, backgrounded app), not an error worth surfacing louder.

Ordering matters and was verified against the existing disconnect-safety pattern already used in `routes/websocket.py`: the dict is overwritten with the new socket **first**, then the old socket is closed. Both `routes/websocket.py`'s `WebSocketDisconnect`/`Exception` handlers guard their own cleanup with `manager.active_connections.get(connection_key) is websocket` before calling `manager.disconnect()` — so when closing the old socket triggers its own receive loop's disconnect handling, that guard sees the registered object is no longer itself and skips cleanup, meaning the old connection's teardown can never evict the new connection's registry entry.

This registration path (`connect()`/`active_connections`) is purely per-replica/in-process — Redis pub/sub delegation (`utils/ws_pubsub.py`) only affects **message fan-out** (`send_personal_message`, `broadcast_to_admins`), not connection registration — so the fix behaves identically whether or not `WS_REDIS_URL` is configured. No pub/sub involvement was needed or added.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `ConnectionManager.connect()` has exactly one caller in the whole backend: `routes/websocket.py:618` (`await manager.connect(websocket, connection_key)`), confirmed via `grep -rn "manager\.connect("` across `backend/`. No other route, service, or background loop calls it.
- `disconnect()` (a different method, unchanged) has additional callers (`routes/drivers/subscriptions.py`, `utils/document_expiry.py`) but this fix does not touch `disconnect()`'s signature or behavior at all.
- The auth handshake, 30s ping heartbeat, and 30 msg/s rate-limit contract (CLAUDE.md "WebSocket auth") are untouched — the change is scoped entirely to `connect()`.
- Interaction with the 16 background loops: none. This is a request-time WS registration path, not a background loop.
- Interaction with ride state machine / money: none directly. Indirectly, closing a stale connection could in principle race with an in-flight `send_personal_message` to that same stale socket if one was queued at the exact moment of replacement — `_deliver_local`/`broadcast` already wrap every `send_json` in `asyncio.wait_for(..., timeout=_BROADCAST_SEND_TIMEOUT)` and catch `Exception`, so a send racing a close on the same object degrades to a logged warning, not a crash or a stuck task. No new failure mode introduced there.
- Mobile client-side WS handling (rider-app `hooks/useRiderSocket.ts`, driver-app `hooks/useDriverDashboard.ts`): both apps' `ws.onclose` handlers were checked. Neither branches on `event.code` today for anything except logging in the driver app's exhausted-reconnect Sentry breadcrumb (`close_code: String(event.code)`); reconnection logic runs the same regardless of close code. This means the new 4409 code will not break either client's existing reconnect flow — it is treated the same as any other close. **This client-side behavior was NOT changed and NOT further verified beyond static code reading** — no client-side test exercised receiving a 4409 close specifically (out of scope per task: "client-side reconnect/multi-tab handling is out of scope for this backend fix").

## 5. User-experience effect

- Backend-only change to server behavior on an existing, already-shipped connection path — no new client-facing API, no copy/notification change.
- **Visible mid-session**: yes, to the specific narrow case this fix targets — a rider or driver who has the app open in two tabs/devices simultaneously. Before: the first (stale) tab silently stopped receiving live updates with no indication anything was wrong (looked "connected" forever). After: the first tab's socket is closed by the server; its existing `onclose` handler runs (same code path already exercised on any disconnect today), so at worst it now visibly attempts to reconnect/shows a brief "reconnecting" state instead of silently going stale forever. This is a strict improvement for that edge case and does not change behavior for the single-connection-per-user case, which is the overwhelming majority of sessions.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/socket_manager.py` | Added `WS_CLOSE_REPLACED_BY_NEW_CONNECTION = 4409` module constant; `ConnectionManager.connect()` now captures the previous socket for `client_id` (if any), registers the new one, then explicitly closes the old one with the new code, catching/logging (at `debug`) any exception from an already-dead socket | Ranked blocker #15 — stop silently orphaning the old connection on second-device/tab reconnect |
| `backend/tests/test_p1_ws_reconnect.py` | Extended `TestConnectionManagerReconnect` with 3 new tests: stale connection gets explicitly closed with the new code on replacement; an already-dead old connection's `close()` raising does not propagate or block registration of the new connection; the very first `connect()` for a `client_id` does not attempt to close anything | Regression coverage for the fix, per CLAUDE.md "every new state transition/behavior needs a test" |

## 7. Before / after

```python
# Before
async def connect(self, websocket: WebSocket, client_id: str):
    # WebSocket is already accepted in the endpoint handler
    self.active_connections[client_id] = websocket
    logger.info(f"WebSocket connected: {client_id}")
    diag_logger.info(...)
```

```python
# After
async def connect(self, websocket: WebSocket, client_id: str):
    old_websocket = self.active_connections.get(client_id)
    self.active_connections[client_id] = websocket
    logger.info(f"WebSocket connected: {client_id}")
    diag_logger.info(...)
    if old_websocket is not None and old_websocket is not websocket:
        try:
            await old_websocket.close(
                code=WS_CLOSE_REPLACED_BY_NEW_CONNECTION,
                reason="replaced_by_new_connection",
            )
            logger.info(f"WebSocket replaced: closed stale connection for {client_id} (second device/tab connected)")
        except Exception as e:
            logger.debug(f"WebSocket replace: closing stale connection for {client_id} failed (already closed?): {e}")
```

## 8. Rollback plan

No feature flag was added — this is a small, additive, backend-only behavior change with a single caller and no data/schema/migration involvement (no DB writes, no Stripe charges, no wallet deltas, no ride-state changes). Rollback is a plain code revert of the one commit (`git revert <sha>`) followed by a normal redeploy; there is no live data to reconcile because the change only affects in-memory WebSocket objects, which hold no durable state. This is explicitly one of the "genuinely isolated, low-risk changes" the template allows a redeploy-only rollback for.

## 9. Verification performed

- [x] Automated tests run (unit): `pytest backend/tests/test_p1_ws_reconnect.py -v` — **12 passed**, including the 3 new tests added for this fix, via `/tmp/spinr-venv/bin/pytest`.
- [x] Automated tests run (broader regression sweep): `pytest backend/tests/test_p1_ws_reconnect.py backend/tests/test_websocket_coverage.py backend/tests/test_websocket_auth.py backend/tests/test_websocket_auth_ack.py backend/tests/test_websocket_token_revocation.py backend/tests/test_websocket_per_user_rate_limit.py backend/tests/test_p3_ws_broadcast.py backend/tests/test_broadcast_timeout.py backend/tests/test_logout_all.py backend/tests/test_ws_pubsub_coverage.py backend/tests/test_ws_health.py backend/tests/test_ws_fanout_metrics.py backend/tests/test_admin_location_throttle.py` — **201 passed**, 0 failed.
- [x] `ruff check backend/socket_manager.py backend/tests/test_p1_ws_reconnect.py` — all checks passed.
- [ ] Manual repro in staging — **NOT performed** (no staging environment access from this session; see "What was NOT verified" below).
- [x] Blast-radius grep performed: `grep -rn "manager\.connect("` across `backend/` → single caller (`routes/websocket.py:618`). `grep -rn "manager\.disconnect("` across `backend/` → confirmed `disconnect()` itself (a different, unmodified method) is unaffected by this change.
- [x] Reviewed against relevant CLAUDE.md conventions: WebSocket auth contract (unchanged — handshake/heartbeat/rate-limit untouched), "do not silently swallow errors" (the old-socket-close exception is caught narrowly and logged, not passed silently).
- [ ] Feature-flagged — not applicable; see rollback plan above for why an additive, single-caller, no-durable-state change was not flagged.

**Whether a real production build was run**: not applicable — this is a Python backend change; there is no `admin-dashboard`/`rider-app`/`driver-app` build affected by this commit.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert` + redeploy; no data-level remediation needed)
- [x] Blast radius is stated, not assumed (single caller confirmed via grep)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (section 5 above)

## What was NOT verified

- **Not tested end-to-end against a real second-device scenario** (two real client connections racing against the live FastAPI WS endpoint) — only the `ConnectionManager` unit in isolation, with mocked `WebSocket` objects. The full `routes/websocket.py` handshake → `manager.connect()` → close-triggers-old-receive-loop chain was reasoned through by reading the existing `WebSocketDisconnect`/`Exception` handlers' identity guard (`manager.active_connections.get(connection_key) is websocket`), not exercised live.
- **Not tested against a real Supabase/Redis-backed staging deployment** — Redis pub/sub delegation not involved in this code path per the investigation (registration is local-only), but that conclusion was reached by code reading, not by running a multi-replica setup with `WS_REDIS_URL` set and watching cross-replica behavior.
- **Client-side (rider-app/driver-app) handling of the new 4409 close code was read but not modified or tested** — both apps' `onclose` handlers were confirmed to not branch on close code today (see section 4), so no regression is expected, but no test was added on the client side and the task explicitly scoped that out ("client-side reconnect/multi-tab handling is out of scope for this backend fix").
- No visual/UI verification — this is a pure backend/protocol-level change with no UI surface of its own.
