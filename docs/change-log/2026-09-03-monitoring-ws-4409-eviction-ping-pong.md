# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude, at user request — "check the settings page and monitoring for other bugs too" |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-portal-heatmaps-audit-gm8fbn` |
| Related issue or gap ID | Last finding from the same monitoring audit as the other fixes in this batch |

## 1. Issue / gap identified

The backend keys admin WebSocket connections by `admin_{user_id}`, not per browser tab (`routes/websocket.py`), and explicitly closes an older connection with close code `4409` (`socket_manager.py`'s `WS_CLOSE_REPLACED_BY_NEW_CONNECTION`) whenever a new one registers under the same key. `use-monitoring-socket.ts` never special-cased this code — it scheduled a normal reconnect regardless of close reason. If the same admin opens `/dashboard/monitoring` in two tabs/windows (a plausible pattern for an ops-wall display plus a laptop), each tab's reconnect evicts the other's connection, which reconnects and evicts this one back — an unbounded eviction ping-pong, with both tabs showing a flickering "reconnecting…" banner indefinitely.

## 2. Root cause

`FATAL_ERROR_MESSAGES` already exists in this hook to classify auth-related WS `{type: "error"}` *messages* that should stop reconnection, but nothing analogous existed for WS *close codes* — `4409` was never checked in `ws.onclose`.

## 3. Fix / remediation

`ws.onclose` now checks `event.code === 4409` before the normal reconnect path: on that code, it stops reconnecting (`shouldReconnectRef.current = false`) and sets a clear, specific message ("This session was opened in another tab or window. Close that one, or reload this page to take over here.") instead of scheduling another connection attempt.

## 4. Risk & impact on existing functionality

- **Blast radius**: isolated to one `if` branch in `ws.onclose`, added before the existing reconnect logic (which is otherwise completely unchanged — every other close code still reconnects exactly as before).
- No backend change — the WS wire protocol (the close code itself) is pre-existing and unmodified; this is purely a frontend interpretation fix.
- Does not change any auth check or access-control decision — a tab that stops reconnecting here can always get a live connection back by reloading the page (which starts a fresh connection, evicting the other tab in turn — the same underlying single-connection-per-admin model, just no longer fighting silently in an infinite loop).

## 5. User-experience effect

Admin-facing only. Opening the monitoring page in two tabs of the same admin account no longer produces an indefinite flickering "reconnecting…" banner on both — the losing tab now settles into a clear, actionable message instead of silently retrying forever.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/hooks/use-monitoring-socket.ts` | `ws.onclose`: added a check for close code `4409` before the reconnect logic | Stop the eviction ping-pong between two tabs of the same admin |
| `admin-dashboard/src/hooks/use-monitoring-socket.test.tsx` | Extended `MockWebSocket.close()`/`onclose` to carry a close code (matching real `CloseEvent` semantics); added a regression test for the 4409 case | Cover the new behavior; the mock previously couldn't express a close code at all |

## 7. Before / after

```ts
// Before — every close code schedules a normal reconnect
ws.onclose = (event) => {
    ...
    if (!shouldReconnectRef.current || !token) { setStatus("error"); return; }
    setStatus("disconnected");
    // ...schedules reconnect regardless of event.code
};

// After — 4409 (evicted by a newer connection) stops trying instead
ws.onclose = (event) => {
    ...
    if (!shouldReconnectRef.current || !token) { setStatus("error"); return; }
    if (event?.code === 4409) {
        shouldReconnectRef.current = false;
        setStatus("error");
        setLastError("This session was opened in another tab or window. Close that one, or reload this page to take over here.");
        return;
    }
    setStatus("disconnected");
    // ...unchanged reconnect logic for every other close code
};
```

## 8. Rollback plan

Plain `git revert` — no data, no migration, no backend/API change.

## 9. Verification performed

- [x] Confirmed the exact close code and its backend origin by reading `socket_manager.py`'s `WS_CLOSE_REPLACED_BY_NEW_CONNECTION` constant and its one call site directly.
- [x] `tsc --noEmit` — clean.
- [x] `eslint` on both changed files — clean, no warnings or errors.
- [x] Extended the existing mock WebSocket test harness (which could not previously express a close code at all) and added a dedicated regression test for this exact scenario — verifies `status`, the specific error message, and that no reconnect timer is scheduled.
- [x] Ran the full `use-monitoring-socket.test.tsx` suite (6 tests, including the 4 pre-existing ones) — all pass, confirming the new branch doesn't affect any other close-code path.
- [x] Real production build (`npm run build`) — exit code 0, confirmed via full-log grep for "error".

## What was NOT verified

- **No live two-tab reproduction** — this sandbox cannot run two live browser tabs against a real backend WebSocket. Verified by direct source reading of the backend's eviction mechanism and a unit test simulating the exact close code/reason the backend sends, not a live repro.
