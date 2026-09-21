# WebSocket disconnect diagnostics

| Field | Detail |
|---|---|
| Issue | Repeated driver disconnects in the sample rides all have the generic ws_disconnect reason, preventing attribution. |
| Root cause | The receive loop does not pass close-frame or heartbeat state to the existing activity-log hook. The actual cause of the sample disconnects remains unconfirmed. |
| Fix | Enrich the existing connection_lost row with observed close code, allowlisted peer reason, connection age, pong age, and a separate server reason when known. The server heartbeat timeout now sends its reason in the close frame. |
| Alternative | Widening heartbeat timeouts without evidence could mask a lifecycle/network defect and delay failure detection. Diagnostics preserve existing timing and reconnect contracts. |
| Impact | websocket_endpoint clean/error cleanup and heartbeat_task; driver_activity_log readers in admin activity/support tools see additive metadata. Driver/rider socket clients receive the same timeout close code with an added reason. Existing driver-presence grace, ownership guards, admin broadcasts and auth revocation behavior remain intact. No known-forks entry applies. |
| UX | No map or mobile UI change. More precise admin/support evidence for future incidents. This does not establish a phone manufacturer or network cause for historical disconnects. |
| Privacy | Peer-controlled free text becomes other or unspecified; only known categories are retained. No coordinates, tokens, device identifiers or names are added. Peer claims remain separate from a local server decision. |
| Performance | Uses the existing once-per-disconnect audit write, with small in-memory metadata. No new per-fix writes or timers. No production P95 benchmark performed. |
| Rollback | Backend code revert removes future metadata enrichment; existing additive rows can remain. No migration, driver-state or money mutation is introduced. There is no runtime diagnostic switch. |
| Verification | New tests first failed on missing audit fields and heartbeat reason. Endpoint tests cover intentional background close and arbitrary-text redaction; existing tests cover handler errors, send failure, token revocation and presence ownership/grace. Ruff and the targeted socket suites pass. |
| Not verified | Actual Android/iOS builds, native marker animation, multi-replica staging, real network interruption, production audit writes. Rider/driver apps have no automated visual regression tooling; no mobile code was changed. |

| File | Change | Why |
|---|---|---|
| backend/routes/websocket.py | Add close diagnostics to existing audit and allowlist log reason | Identify future failure mode without collecting sensitive payloads |
| backend/tests/test_websocket_coverage.py | Endpoint, heartbeat, error and metadata regressions | Verify real handler flow and preserve connection behavior |
| docs/change-log/2026-09-15-websocket-disconnect-diagnostics.md | Impact and verification record | Bound rollout claims |

Before: `metadata={"reason": "ws_disconnect", "source": "websocket"}`.

After: those keys remain, plus `close_code`, `close_reason`, available monotonic `connection_age_ms`/`last_pong_age_ms`, and optional `server_close_reason`. A missing pong age before authentication is omitted; an unknown close reason is never interpreted as proof of network failure. Existing logging guards remain: only intent-online drivers are audited, and superseded sockets do not generate another connection-lost row.

## Device verification after a staging deployment

1. Record driver/rider Android app versions and OTA update IDs; test the existing native marker fix on an actual release build.
2. Take a ride with the driver app foregrounded, then switch to navigation and lock/unlock the screen. Correlate raw capture gaps and audit rows with those actions.
3. Interrupt data and restore it; verify the durable queue drains, fresh live positions resume, and old captures do not move the live marker backwards.
4. Check intentionally reported `app_backgrounded` separately from `server_close_reason=heartbeat_timeout`, token revocation and unexplained closes. A missing clean close frame can still leave the reason unknown.
5. Enable the route-ordering flag only in staging first and compare raw evidence, rejected counts, phase boundaries and distance revisions. Verify driver and rider marker movement separately: repaired completed-route evidence alone is not proof of a live-marker fix.
