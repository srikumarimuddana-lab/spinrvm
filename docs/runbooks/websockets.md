# Runbook — WebSocket Hardening

**Owner:** `backend` · **Cadence:** Always-on; runbook on contract change
**Closes:** B-P1-12 (per-user message rate-limit); cross-refs B-P1-11 (token revocation, see `auth-tokens.md`)

> **Recovery note (2026-04-27):** B-P1-11 + B-P1-12 were briefly dropped
> from the working tree by merge commit `401bd5d` ("resolve 10-file
> conflict — merge main into review-pending-audits-Pu1aP"). The tests
> for both pieces survived the merge intact and immediately failed
> against the dropped code, surfacing the loss. Restored in a follow-
> up commit by manually re-applying the original diffs from
> `707da6e` (B-P1-11) and `a96c70d` (B-P1-12) on top of the post-
> merge file shapes (which had grown new `broadcast_to_admins` /
> `broadcast_ride_status` / driver-presence handling). All 38 affected
> tests pass post-restore. If you ever see B-P1-11/12 disappear in a
> future merge, this is the playbook: read `git show 707da6e` and
> `git show a96c70d`, apply manually to current shapes, run the
> two test files to verify.

---

## Why This Matters

A single authenticated rider/driver WebSocket carries: ride dispatch
events, live location, chat, ride state transitions. The receive
loop also accepts location updates that hit the database on every
message. Three abuse vectors feed into the same code path:

1. **Single noisy client** — buggy reconnect spamming the server.
2. **Multi-socket abuse** — same authenticated user opens N sockets
   to multiply the per-socket rate cap.
3. **Oversized payload** — single huge frame to OOM the worker.

This runbook pins what the receive loop enforces, what frames the
client sees on each gate, and how to operate it safely.

For the *authentication-side* of WS hardening (token_version
re-validation, kick-on-logout-all, the B-P1-3 reuse cascade), see
`docs/runbooks/auth-tokens.md` → "WebSocket Token Revocation".

---

## Receive-Loop Gates (in order)

The main message loop in `backend/routes/websocket.py` runs every
inbound frame through these checks. Each gate emits a typed JSON
frame to the client and either drops the message (`continue`) or
closes the socket. Order matters — cheaper checks come first so a
malicious payload pays the smallest CPU cost.

| # | Gate | Limit | Frame on hit | Action |
|---|---|---|---|---|
| 1 | Message size | 64 KB (`WS_MAX_MESSAGE_SIZE`) | `{"type":"error","message":"message_too_large"}` | drop, keep socket |
| 2 | JSON validity | parseable | `{"type":"error","message":"invalid_json"}` | drop, keep socket |
| 3 | **Per-user rate** (B-P1-12) | **30 msg/s** across ALL of the user's sockets on this machine | `{"type":"rate_limited","scope":"user","limit":30,"window_seconds":1,"retry_after_seconds":1}` | drop, keep socket |

Token-revocation gates (heartbeat re-validation, connect-time
token_version check, kick-on-logout) sit *outside* the receive loop
and are covered in `auth-tokens.md`.

---

## Per-User Rate Limit (B-P1-12)

### What changed

Before: a closure-scoped `_msg_timestamps` list per socket. An
attacker who opened five sockets got `5 × 30 = 150 msg/s` effective
throughput because each socket had its own bucket.

After: `ConnectionManager.note_user_message(user_id)` keys the
sliding-window bucket on `user_id` and aggregates across every
WebSocket the user has open on this machine. The cap is now a
**per-user** cap, not a per-socket cap.

### Wire format on hit

```json
{
  "type": "rate_limited",
  "scope": "user",
  "limit": 30,
  "window_seconds": 1,
  "retry_after_seconds": 1
}
```

Mirrors the HTTP 429 contract from B-P1-8 (`docs/runbooks/rate-limits.md`)
in spirit — typed frame, retry hint, limit context — so a future
client that wants to display a unified "you're sending too fast"
banner can reuse the same parser.

### What the client should do

- **Drop** the offending message — the server already did.
- **Throttle** subsequent sends. The server keeps the socket alive,
  so a client that respects `retry_after_seconds` will resume
  flowing within ≤1 second.
- **Do NOT reconnect** — a reconnect doesn't reset the cap (see
  *Bucket lifecycle* below) and just costs a JWT verify round-trip.

### Multi-replica caveat (resolved — B4)

The cap is enforced **fleet-wide** via a Redis fixed-window counter
(`INCR` + `EXPIRE 1`) keyed on `user_id`, shared across every replica —
see `ConnectionManager.note_user_message`. `utils/redis_client.py`
transparently falls back to an in-process dict when `REDIS_URL` is
unset, so local/dev/test behave the same without branching.

If Redis is configured but a call raises (network blip, Redis down),
the limiter fails **open** to `_note_user_message_local` — the
original per-machine sliding-window bucket described below — rather
than blocking every WS message fleet-wide on a transient Redis hiccup.
That per-machine fallback bounds the fleet-wide attack surface to
`replica_count × cap` only during a Redis outage, not as steady-state
behaviour.

### Bucket lifecycle

The per-user bucket is evicted in two cases:

1. **Last socket disconnects** (`ConnectionManager.disconnect`).
   `_user_id_from_key` parses the `{rider,driver,admin}_{user_id}`
   key shape; if no other key for the same user remains in
   `active_connections`, the bucket is dropped. Memory bound: at
   most one entry per currently-connected user.
2. **Forced kick** (`ConnectionManager.disconnect_user`, B-P1-11).
   The bucket is cleared so a kicked user starts with a fresh budget
   if they immediately re-auth — the pre-kick entries are stale
   signal at that point.

A user with both a rider AND a driver socket retains the bucket
until BOTH disconnect. Otherwise toggling either client would let
them silently reset their budget.

---

## Operating

### Confirm the cap is firing

When investigating a "I'm getting rate-limited" support ticket, look
for the typed frame in the diag log. The receive loop logs the
client_id on every accepted message via `diag_logger`, so a sudden
gap in those entries paired with a `rate_limited` frame to the
client confirms the gate fired.

```bash
# Tail the diag log for a specific user. Replace user_id.
grep "rider_<user_id>\|driver_<user_id>\|admin_<user_id>" /var/log/spinr/diag.log | tail -100
```

If the user has multiple sockets and the cap fires when their
*aggregate* rate hits 30/s, that's the intended behaviour — not a
bug. If it fires while a single socket is sending <30/s, check
whether another socket for the same user is also active.

### Tune the cap

`WS_MAX_MESSAGES_PER_SECOND_PER_USER` in `socket_manager.py` is the
single source of truth. The receive loop in `routes/websocket.py`
passes this value via the `max_per_second` kwarg, so changing it
in one place cascades automatically.

Steady-state legitimate traffic at our scale:

- **Driver in-trip:** 1 location_update/s + occasional ride state
  events ≈ 2 msg/s.
- **Rider in-trip:** chat messages on demand ≈ <1 msg/s.
- **Admin live monitor:** receives a lot, sends almost nothing.

30 msg/s is comfortably above any legitimate traffic and well
under what a malicious burst can sustain.

---

## What NOT to Do

- **Do not raise the cap to "fix" a buggy client that's hitting it.**
  The cap is a backstop against abuse; a client legitimately hitting
  30 msg/s sustained is doing something wrong (most likely an
  unthrottled reconnect or a stuck retry loop). Fix the client.
- **Do not switch from `time.monotonic()` to wall-clock time
  (`datetime.now`).** Wall-clock can jump backwards on NTP
  adjustments, which would either spuriously reject (if it jumps
  forward in the cutoff calc) or admit a window's worth of stale
  timestamps (if it jumps backward). Monotonic is the only correct
  choice for sliding-window rate limiting.
- **Do not collapse the per-user cap back into a per-connection
  cap.** That's the bug B-P1-12 closed. If you need a *tighter*
  per-connection cap as belt-and-suspenders, add a second gate;
  do not replace the per-user one.
- **Do not silently raise the cap for `admin` clients to "let them
  monitor faster".** Admins are sinks not sources — they should
  almost never be sending at the cap. If a real admin tool needs
  more inbound throughput, add a typed message and gate it
  separately.
