# ADR-004: Transparent in-process Redis fallback for dev/test

**Date:** 2026-02-14
**Status:** Accepted

---

## Context

Redis is used for three distinct purposes in the Spinr backend:

1. **Rate limiting** — SlowAPI per-endpoint counters (`RATE_LIMIT_REDIS_URL`)
2. **OTP lockout and session state** — `otp_fail_count:{phone}`, `otp_lock:{phone}`, `session:{user_id}` (`REDIS_URL`)
3. **WebSocket pub/sub** — cross-replica `spinr:ws:dispatch` fan-out (`WS_REDIS_URL`)

Running a local Redis instance is a friction point for contributors who only need to work on backend logic. Requiring Redis for `pytest` runs would add a service dependency to CI that can be avoided.

The question was: how to make Redis optional in dev/test without a flag that diverges the code paths?

---

## Decision

`backend/utils/redis_client.py` wraps all Redis operations and falls back to an **in-process dict** when `REDIS_URL` is unset (or when the Redis connection fails on startup). The same `redis_get`, `redis_set`, `redis_delete`, `redis_incr`, `redis_expire` interface is exported regardless of the backing store.

Key implementation details:
- On import, the module attempts to connect to Redis using the configured URL. If the connection fails or the URL is absent, it sets a module-level `_USE_FALLBACK = True` flag.
- All helper functions check `_USE_FALLBACK` and route to either the real Redis client or a `dict`-backed in-process store.
- The in-process fallback has **no TTL enforcement** — `redis_set(..., ttl=...)` stores the value but the TTL is ignored. This is acceptable for dev/test; OTP lockout state simply persists for the process lifetime.
- The same fallback pattern applies to `WS_REDIS_URL` (pub/sub): when absent, `ws_pubsub.py` broadcasts only to the in-process `ConnectionManager`, which is correct for single-replica dev.
- Rate limiting via SlowAPI falls back to an in-memory limiter when `RATE_LIMIT_REDIS_URL` is unset; this is SlowAPI's own built-in fallback.

---

## Consequences

**Positive:**
- `pytest` runs with zero external dependencies — no `docker-compose up redis` required.
- New contributors can run the backend with only `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` set.
- The fallback is transparent to all callers; no `if redis_available:` branches in route handlers or services.

**Negative / trade-offs:**
- TTL-less fallback means OTP lockout state, session tokens, and rate-limit counters are never evicted in dev. This can cause unexpected "locked out" states after a long dev session. Workaround: restart the backend process to clear in-process state.
- The fallback is **not** suitable for production: OTP lockouts are lost on restart, and rate-limiting state is per-process (not shared across `--workers 4`). The production Railway environment always has `REDIS_URL` set; this is enforced by a startup assertion in `core/config.py` when `ENV=production`.
- Silent fallback means a misconfigured `REDIS_URL` in staging would go unnoticed. Mitigation: `Settings` logs a `WARNING` when falling back to the in-process store, which is visible in Railway deploy logs.
