# Runbook — Auth Token Lifecycle & Reuse Detection

**Owner:** `backend` · **Cadence:** Always-on automatic; manual investigation on alert
**Closes:** B-P1-3 (refresh-token reuse detection), B-P1-11 (WebSocket token revocation); cross-refs B-P1-13 (logout-all)

---

## Why This Matters

Refresh tokens are the only durable proof a client has of "I was already
logged in". A leaked refresh token is the highest-impact credential
exposure in the system: an attacker holding one can mint fresh access
tokens indefinitely until the row is revoked.

The system runs OAuth2's "rotation + reuse detection" pattern (BCP §4.14.2):
every successful `/auth/refresh` call replaces the presented refresh
token with a new one, and the old row is stamped `revoked_at`.
Presenting an already-revoked refresh token is treated as **theft** —
legitimate clients always step forward to the latest token they hold
and never return to an old value.

When reuse is detected, the backend cascades a full session kill for
the user. This is conservative on purpose: at the moment of detection
we cannot tell which device is the attacker, so we revoke every
session and force re-auth on every device.

---

## Token Model

| Token | Format | TTL | Storage |
|---|---|---|---|
| Access token (rider/driver) | JWT (HS256) | 15 min | client only — never persisted server-side |
| Access token (admin) | JWT (HS256) | 12 h | client only |
| Refresh token | opaque, 384 bits entropy | 30 days | sha256-hex stored in `refresh_tokens.token_hash` |

Access tokens carry `token_version`. The auth middleware
(`backend/dependencies.py`) re-reads the user row on every request and
rejects the token if its `token_version` claim is below the current
DB value. Bumping `token_version` is the kill-switch for in-flight
access tokens that have not yet hit their TTL.

Refresh tokens are chained: each row carries `replaced_by` pointing at
the row that succeeded it. The chain is queryable for incident response
(see *Investigating an alert* below).

---

## WebSocket Token Revocation (B-P1-11)

HTTP requests re-read `token_version` on every call (the middleware in
`backend/dependencies.py`). WebSocket connections, by contrast, are
authenticated **once at connect time** — without further work, a socket
that opened before `/auth/logout-all` would keep pushing dispatch
events for the lifetime of the connection. B-P1-11 closes that with
three layers of defence:

1. **Connect-time check** (`backend/routes/websocket.py`). After the
   JWT is decoded and the user row is loaded, the handshake is
   rejected if `payload.token_version < user.token_version`. Stops a
   stale-token reconnect from succeeding in the first place. The
   client receives `{"type": "error", "message": "session_revoked"}`
   and the socket is closed before any real traffic flows.

2. **Periodic heartbeat re-validation** (`heartbeat_task` in the same
   file). Every 30 s — same cadence as the existing keepalive ping —
   the heartbeat re-reads `token_version` from `users` (rider/driver)
   or `admin_staff` (admin) and closes the socket with code 1008
   `token_revoked` if the row's value has advanced past the JWT's
   claim. This is the **safety net**: it catches sockets even when
   the push-based kick (layer 3) fails. Bound on staleness: at most
   30 s after the bump lands.

3. **Push-based kick** (`backend/socket_manager.py::ConnectionManager.kick_user`).
   `/auth/logout-all`, `/admin/auth/logout-all`, and the B-P1-3 reuse
   cascade all call `manager.kick_user(user_id, client_types=[…])`
   after they finish bumping `token_version` and revoking refresh
   tokens. `kick_user` publishes a control message
   (`{"control": {"action": "kick_user", …}}`) on the
   `spinr:ws:dispatch` Redis channel so every replica disconnects its
   own copy of the user's sockets, then runs the local disconnect
   synchronously so the caller's response blocks until at least the
   originating VM has kicked the user. **Best-effort by design** —
   layer 2 is the durable contract; layer 3 just shrinks the window
   from ≤30 s to ≤a few hundred ms.

Scope of the kick is set by `client_types`:

| Trigger | Scope passed to `kick_user` |
|---|---|
| `/auth/logout-all` (rider/driver) | `["rider", "driver"]` |
| `/admin/auth/logout-all` | `["admin"]` |
| B-P1-3 reuse cascade, audience=rider/driver | `["rider", "driver"]` |
| B-P1-3 reuse cascade, audience=admin | `["admin"]` |

The reason string sent on the close frame propagates into the client's
error handler so the UX can surface "you signed out everywhere" /
"session revoked" rather than a generic disconnect. The frame the
client sees right before the close is `{"type": "session_revoked",
"reason": "<logout_all|refresh_token_reuse|token_revoked>"}`.

### Single-machine fallback

When `WS_REDIS_URL` / `RATE_LIMIT_REDIS_URL` are unset (dev),
`pubsub.publish_kick_user` returns False and only the caller's local
sockets get kicked. That's correct: in single-machine mode there are
no other replicas to fan out to. The fallback is intentional, not a
degraded path that needs alerting.

### Why we still need the heartbeat layer

On the day Redis is degraded, the publish path returns False, and
**other replicas don't get the kick**. Without the heartbeat,
sockets on those replicas would survive until their next reconnect.
The heartbeat re-read is what makes B-P1-11 robust against partial
Redis outages — never remove it as "redundant with kick_user". The
contract is "≤30s to disconnect", and only the heartbeat enforces it
under partial-failure modes.

---

## Reuse Detection (B-P1-3)

### Trigger

`utils/refresh_tokens.py::lookup_refresh_token` is called by every
`/auth/refresh` request. If the looked-up row's `revoked_at` is
non-NULL, `_handle_refresh_token_reuse` fires. The function:

1. **Logs at ERROR level** with full context: `row_id`, `user_id`,
   `audience`, `original_revoked_at`, `replaced_by`. Sentry/logs
   alerting should be configured to page on `REFRESH TOKEN REUSE
   DETECTED`.
2. **Bumps `token_version`** on the right table (`users` for
   rider/driver audiences; `admin_staff` for admin audiences).
   `admin-001` is skipped — the super-admin's creds live in env vars
   and have no DB row to bump (rotate `ADMIN_PASSWORD` instead).
3. **Revokes every refresh token** for the user via
   `revoke_all_for_user(user_id)`. All devices are logged out at the
   refresh-token boundary.
4. **Kicks every live WebSocket** for the user (B-P1-11) via
   `manager.kick_user(...)` — scoped to the right client_types based
   on the audience (rider/driver vs admin). Closes the socket with
   `code=1008 token_revoked` so the client surfaces session-expired
   UX. Best-effort: a kick failure does not skip step 5.
5. **Inserts an `audit_logs` row** tagged
   `action='refresh_token_reuse_detected'` with the full cascade
   detail in `details` (TEXT JSON per migration 06 schema).

Each step is best-effort: a failure in one does not skip the others.
The cascade never raises — `lookup_refresh_token` always returns
`None` either way, so the client sees a generic 401 with no oracle
leakage about which step succeeded.

### What the user sees

A single 401 from `/auth/refresh`. The mobile client's Axios
interceptor will surface it as a session-expired UX prompt, and the
user re-authenticates via OTP. From the user's perspective this is
indistinguishable from any other refresh failure (intentional — the
attacker watching the network must not learn that detection fired).

---

## Operating

### Confirm reuse-detection is healthy

```sql
-- Recent detection events (last 30 days):
SELECT created_at, entity_id AS user_id, details
FROM audit_logs
WHERE action = 'refresh_token_reuse_detected'
ORDER BY created_at DESC
LIMIT 50;
```

Baseline rate is expected to be near-zero. A non-zero count is not
necessarily a breach — it can also be triggered by:
- A buggy mobile client retrying a failed refresh with the old token
- Aggressive network middleware caching an old refresh response
- A real attack

Investigate every event individually (see below) before assuming
either category.

### Investigating an alert

```sql
-- 1. Pull the audit_log entry.
SELECT details FROM audit_logs
WHERE id = '<audit_log_id>';
-- The details JSON contains: replayed_row_id, audience, replayed_user_agent,
-- replayed_ip, original_revoked_at, replaced_by, cascade_token_version,
-- cascade_refresh_revoked.

-- 2. Walk the chain forward from the replayed token.
WITH RECURSIVE chain AS (
  SELECT id, replaced_by, revoked_at, user_agent, ip, issued_at
  FROM refresh_tokens
  WHERE id = '<replayed_row_id>'
  UNION ALL
  SELECT rt.id, rt.replaced_by, rt.revoked_at, rt.user_agent, rt.ip, rt.issued_at
  FROM refresh_tokens rt
  JOIN chain ON rt.id = chain.replaced_by
)
SELECT * FROM chain ORDER BY issued_at;
```

Compare `user_agent` and `ip` between the replayed row and its
descendants. If they diverge sharply — different OS, different country
— treat as theft and notify the user out-of-band.

### Forcing a manual session kill

For incident response (e.g. compromised device reported by a user),
prefer the existing endpoints over a manual SQL run. B-P1-13 wired
these into every client surface, so the operator's first move is
almost always to walk the user through the in-app button rather than
touching the DB:

- **Rider:** Account tab → **Safety & Privacy → Sign out of all
  devices** (`rider-app/app/(tabs)/account.tsx`). Calls
  `authStore.logoutAll()` → `POST /auth/logout-all`.
- **Driver:** Profile screen → **Sign out of all devices** row, just
  above the regular Sign Out (`driver-app/app/driver/profile.tsx`).
  Same `authStore.logoutAll()` path.
- **Admin staff:** Sidebar footer → **Sign out everywhere** (small
  action below the regular Sign Out;
  `admin-dashboard/src/components/sidebar.tsx` →
  `lib/api.ts::logoutAllAdmin`). Calls `POST /admin/auth/logout-all`.
- **`admin-001` super admin:** the admin endpoint refuses with HTTP
  400 (no DB row to bump `token_version` against). The only kill is
  rotating `ADMIN_PASSWORD` in the environment and redeploying — see
  the env-var rotation steps in the deploy runbook.

All three client paths share the same best-effort contract: if the
backend call fails, the client still tears down the local session and
redirects to login. The worst failure mode is an operator believing
they're signed out everywhere when access tokens are still live, so
the backend handler refuses to claim success unless the
`token_version` bump landed (see `test_logout_all.py`).

After B-P1-11 lands, the same handlers also invoke
`manager.kick_user(...)` so any live WebSocket sockets close
synchronously — without that, a rider who panics and hits "Sign out
everywhere" would still receive ride/dispatch events on the open
socket until the next heartbeat tick (≤30 s). The kick is layered on
**top** of the durable token_version+refresh-revoke contract, not as
a replacement: a kick failure logs and proceeds, because the
heartbeat re-read is the safety net that closes the socket within
30 s regardless.

Manual SQL escape hatch (use `psql` with service-role connection):

```sql
-- Rider/driver:
UPDATE users
SET token_version = COALESCE(token_version, 0) + 1
WHERE id = '<user_id>';

UPDATE refresh_tokens
SET revoked_at = NOW()
WHERE user_id = '<user_id>' AND revoked_at IS NULL;
```

Always pair with an `audit_logs` row recording who ran the kill and
why — manual session kills must be traceable.

---

## What NOT to Do

- **Do not surface the detection event to the client.** Returning a
  distinct error code or message would give an attacker a binary
  oracle for whether their stolen token's siblings are still live.
- **Do not bypass the cascade for "just this one user"** because the
  alert looks like a client bug. Ship the client fix first, then
  investigate the alert. The cascade is cheap (a few row updates) and
  always recoverable via the user re-authenticating.
- **Do not edit `_handle_refresh_token_reuse` to swallow the
  `logger.error` line.** That line is the only signal Sentry/PagerDuty
  has for the reuse event; the audit_logs row alone is not real-time.
- **Do not store the raw refresh token anywhere** — server-side, only
  the sha256 hash lives in `refresh_tokens.token_hash`. A DB dump must
  not yield usable tokens. The raw bytes leave the backend exactly
  once (in the `/auth/refresh` and `/auth/login` response body).
- **Do not delete the heartbeat token_version re-read as "redundant
  with `kick_user`".** The kick is the fast path; the heartbeat is
  the only layer that holds when Redis is degraded. Removing it
  means partial-Redis outages silently regress B-P1-11 to its pre-
  fix behaviour (sockets survive `logout-all` until reconnect).
- **Do not widen `kick_user` scope across identity boundaries.** The
  rider/driver `logout-all` passes `["rider", "driver"]`; the admin
  variant passes `["admin"]`. An admin who hits "Sign out everywhere"
  must not also disconnect their personal rider socket if they
  happen to share a `user_id` (they don't, in our model — but the
  scope guard keeps it that way).

---

## Recovery

If the cascade itself misfires (e.g. mass false-positive due to a
client bug shipped to production):

1. **Patch the client first.** A backend-side rollback that disables
   reuse detection would re-open the original P1 finding.
2. Affected users self-recover by re-authenticating (OTP or password).
   Their data is intact; only their sessions are gone.
3. Audit the spike via the SQL above and confirm the events all share
   the same offending `user_agent` (proves it was a client regression,
   not a real attack).

---

## Notes

- The cascade's `audit_logs` insert uses
  `gen_random_uuid()` for the PK — production schema (migration 06)
  has no DB-side default for `audit_logs.id`. The audit row is
  therefore append-only by both convention (B-P1-7 trigger) and
  contract.
- `_USERS_TABLE_AUDIENCES = {"rider", "driver"}` and
  `_ADMIN_STAFF_AUDIENCES = {"admin"}` are defined at module top in
  `utils/refresh_tokens.py`. If a new audience is introduced (e.g.
  `corporate_admin` on its own table), add it to the right set so the
  token_version bump targets the correct table.
- Reuse detection does not need its own migration — the `replaced_by`
  column was added in migration 25 and back-filled in migration 35.
  All B-P1-3 work is application-side.
