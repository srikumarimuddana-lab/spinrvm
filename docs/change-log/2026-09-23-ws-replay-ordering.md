# WebSocket durable replay ordering

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | backend, driver-app |
| Domain (Sentry tag) | dispatch |
| PR / commit link | PR #5725 follow-up |
| Related issue or gap ID | Wave 2 WS replay/resume coverage |

## 1. Issue / gap identified

Concurrent durable WebSocket publishes could expose a higher sequence to a reconnect before an earlier publisher had appended its message, so resuming from the higher cursor could permanently skip the late append.

## 2. Root cause

`publish()` used a standalone `INCR` followed by a separate outbox pipeline. Redis serialized each command, but not the sequence allocation with the append, trim, expiry, and publish group.

## 3. Fix / remediation

Use one Redis Lua operation for sequence allocation, outbox append and retention, and channel publish. Existing message envelopes, 50-message ring, 300-second expiry, and unsequenced local-delivery fallback remain in place. Runtime errors after `INCR` may leave a sequence gap; Redis Lua does not roll back earlier writes.

## 4. Risk & impact on existing functionality

- Blast radius: single-surface backend WebSocket publisher; `backend/socket_manager.py` is the only production caller. `backend/routes/websocket.py` reads the outbox for reconnect replay.
- Durable ride events keep their existing envelope and reconnect behavior; non-durable location fan-out still bypasses sequence/outbox. Redis script execution errors fall back to the existing unwrapped publish path, so the event can be delivered live without advancing the replay cursor.
- No database, ride state, insurance, or wallet writes change. No background loop or migration interaction.

## 5. User-experience effect

Drivers may recover a durable ride event that could previously be skipped during a concurrent publish/reconnect race. The change is backend-only and may affect an active WebSocket session; there is no copy or notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/ws_pubsub.py` | Atomic Redis sequence, outbox, retention, and publish operation | Prevent append order from diverging from sequence order |
| `backend/tests/test_ws_pubsub_coverage.py` | Interleaving, fallback, envelope, and retention assertions | Verify ordering fix and preserved behavior |
| `backend/tests/test_websocket_auth.py` | Route-level replay cursor assertion | Verify reconnect filters and sends only messages newer than `last_seq` |
| `backend/tests/test_ws_pubsub_redis_integration.py` | Real-Redis concurrency, payload, ordering, retention, and TTL test (loopback DB 15 only) | Execute the Lua contract against Redis rather than only a mock |

## 7. Before / after

```python
# Before
seq = await redis.incr(seq_key)
await pipeline.rpush(outbox_key, json.dumps({"seq": seq, "data": message}))
await pipeline.publish(channel, body)
```

```python
# After
await redis.eval(script, 2, seq_key, outbox_key, channel, client_id, payload, 50, 300)
```

## 8. Rollback plan

This changes no persisted application data outside the existing expiring Redis sequence and outbox keys. Revert the backend change and redeploy to restore the previous publisher implementation; no data-level cleanup is required.

## 9. Verification performed

- [x] Automated tests run: `backend/tests/test_ws_pubsub_coverage.py` (64 passed), `backend/tests/test_websocket_auth.py` (16 passed), `backend/tests/test_websocket_live_location.py` (9 passed), and `backend/tests/test_ws_pubsub_redis_integration.py` with `WS_TEST_REDIS_URL=redis://127.0.0.1:6399/15` (1 passed); one pre-existing Starlette warning in each run.
- [x] Real-Redis verification: Redis 6.2.14 on a disposable loopback server with no persistence; the integration test exercised 52 concurrent publishes, preserved JSON payloads, published sequences 1–52 in order, retained sequences 3–52, and set an approximately 300-second TTL. Unique test keys were deleted afterward.
- [ ] Manual staging repro: not run; no staging or production Redis was used.
- [x] Blast-radius grep performed: `pubsub.publish`, `get_outbox`, and WS outbox/sequence keys; callers are named above.
- [x] Reviewed backend `CLAUDE.md` WS fan-out/reconnect conventions.
- [x] Feature flag not used: backend ordering fix with no new user-visible behavior outside reliable replay.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated.
- [x] No notification or copy change.
