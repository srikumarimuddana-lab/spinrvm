# Dimension 06 — Real-Time Features (WebSocket & GPS)

**Question:** Does live location tracking and real-time messaging work reliably when the network is bad, the phone is locked, or the server restarts?

---

## Checklist

### WebSocket Connection
- [ ] Authentication enforced as first message — no data accepted before auth
- [ ] Connection key = `client_type + user_id` — prevents impersonation
- [ ] 30-second ping-pong heartbeat detects silent disconnections
- [ ] Auto-reconnect with exponential backoff on disconnect
- [ ] Connection cleaned up from server dict on disconnect — no memory leak
- [ ] Rate limit per connection (Spinr standard: 30 msg/sec, 64KB max)
- [ ] Unknown message types logged and ignored — not silently dropped
- [ ] WS closed gracefully on app unmount / logout

### GPS Tracking
- [ ] Foreground location permission requested before background
- [ ] Background permission with rationale screen before requesting
- [ ] Background location subscription re-established when app returns to foreground
- [ ] GPS accuracy: `ACCURACY.BestForNavigation` for active rides, lower for idle
- [ ] Battery consideration: accuracy reduced when driver is stationary
- [ ] Location buffer capped (Spinr standard: 500 entries max)
- [ ] Location batch uploaded on interval (30s) — not every GPS event
- [ ] Failed batch upload has retry with cleanup (not unbounded accumulation)
- [ ] Location subscription cleaned up on unmount

### Redis Pub/Sub (Multi-Server)
- [ ] Messages delivered across server instances via Redis pub/sub
- [ ] Fallback to in-process delivery when Redis is unavailable
- [ ] Fallback is clearly logged — not silently switched
- [ ] Message ordering: document whether ordering is guaranteed

### Concurrency Safety
- [ ] `broadcast()` takes a snapshot of connections before iterating (dict can change during loop)
- [ ] `send_personal_message()` handles connection-not-found gracefully
- [ ] Concurrent disconnect doesn't crash the message send

---

## Severity Guide

| Finding | Severity |
|---|---|
| No auth check before data sent over WebSocket | CRITICAL |
| Background GPS never requested — tracking stops on phone lock | HIGH |
| No reconnect logic — driver disappears from map until manual restart | HIGH |
| broadcast() race condition — server crashes on concurrent disconnect | MEDIUM |
| Location buffer unbounded — OOM on long shift | MEDIUM |
| No heartbeat — silent disconnections undetected for minutes | MEDIUM |
| Battery: constant high-accuracy GPS during idle | MEDIUM |
| Failed batch upload silently dropped | MEDIUM |
| WS not closed on unmount — connection leak | MEDIUM |
