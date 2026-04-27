# Runbook — Rate-Limit Response Contract

**Owner:** `backend` + `mobile` · **Cadence:** Always-on; runbook on contract change
**Closes:** B-P1-8 (rate-limit headers + 429 retry semantics)

---

## Why This Matters

Every rate-limited endpoint has two failure modes that look identical
to a user but mean different things:

  - "You're being throttled — wait 60s and retry" → recoverable.
  - "The server is broken" → file a ticket.

Without machine-readable retry-timing on the 429, the mobile clients
collapse both into a generic *"Request failed"*. Users hammer the
button; the rate limit re-trips; the support queue fills with reports
of "the app is broken" when it's the user's own retry storm.

This runbook pins the wire format that lets the mobile UX render
*"Try again in 58s"* with a live countdown instead. Break either side
of the contract and the UX silently regresses.

---

## Wire Format

### Headers (every 429 response)

| Header | Source | Purpose |
|---|---|---|
| `Retry-After: <seconds>` | RFC 9110 §10.2.3 | Integer delta-seconds, the canonical wait time. Always emitted. |
| `RateLimit-Limit: <amount>` | IETF draft-ietf-httpapi-ratelimit-headers | Total quota for the window. |
| `RateLimit-Remaining: 0` | Same | Always 0 on a 429 (we're past the limit). |
| `RateLimit-Reset: <seconds>` | Same | Delta-seconds form (same value as Retry-After). |

`Retry-After` is the only header guaranteed to round-trip through every
proxy and CDN — the IETF `RateLimit-*` set is mid-draft and some
corporate WAFs strip non-standard headers. Clients fall back to the
JSON body when those are missing (see below).

### JSON body (slowapi-handled 429s only)

```json
{
  "error": "rate_limit_exceeded",
  "message": "Too many requests. Please slow down and try again later.",
  "retry_after": 60,
  "limit": 3,
  "documentation_url": "https://spinr.app/docs/rate-limits"
}
```

The OTP lockout path (`routes/auth.py::_check_otp_lockout`) raises
`HTTPException(429)` directly — its body is FastAPI's default
`{"detail": "ERR_OTP_LOCKED"}` shape, but the **headers** match the
slowapi shape so a single client parser handles both.

---

## Backend

### Two 429 sources, one contract

| Source | Trigger | Code |
|---|---|---|
| **slowapi window limit** | `@limiter.limit("5/minute")` decorator hit | `utils/rate_limiter.py::rate_limit_exceeded_handler` |
| **OTP brute-force lockout** | 5 failed OTPs in 1 hour → 24 h Redis lockout | `routes/auth.py::_check_otp_lockout` |

Both emit identical headers. If you add a **third** 429 source (e.g.
account-level lockout, IP block list), mirror the same set or the
client falls back to "Request failed" on that path.

### Where `retry_after` comes from

`exc.limit.limit` is a `slowapi.wrappers.Limit`; the parsed
`RateLimitItem` is `exc.limit.limit.limit` (yes, doubly nested).
`get_expiry()` returns the **window size** in seconds — 60 for
"5/minute", 3600 for "5/hour". That's the worst-case wait: the bucket
will definitely have headroom by then. Computing the exact bucket
reset moment requires probing the storage backend's per-key state,
which slowapi/limits doesn't expose cheaply, and being slightly
conservative on Retry-After is the right failure mode for a rate
limit (waiting *longer* than strictly necessary is safe; waiting
*less* re-trips the limit).

The previous implementation hard-coded `Retry-After: 60` as a
sentinel. The 429 fired correctly, but a 5/hour endpoint told the
client "wait 60s" instead of 3600s, and the client's retry would
re-trip immediately. Fixed in B-P1-8.

### Endpoint inventory

All currently rate-limited:

| Endpoint | Limit | Trigger window |
|---|---|---|
| `POST /auth/send-otp` | 3/minute | 60s |
| `POST /auth/verify-otp` | 5/minute | 60s |
| `POST /auth/firebase` | 10/minute | 60s |
| `POST /auth/refresh` | 20/minute | 60s |
| `POST /auth/logout` | 3/minute | 60s |
| `POST /auth/logout-all` | 5/minute | 60s |
| `POST /admin/auth/login` | 5/minute | 60s |
| `POST /admin/auth/refresh` | 20/minute | 60s |
| `POST /admin/auth/logout` | 10/minute | 60s |
| `POST /admin/auth/logout-all` | 5/minute | 60s |
| `POST /admin/auth/change-password` | 3/minute | 60s |

Plus per-business-flow limits in `utils/rate_limiter.py` (ride
creation, location updates, document upload, promo guards). All
share the same handler and emit the same shape.

---

## Client

### Two parallel implementations

Same contract, different consumers:

| File | Used by | Test |
|---|---|---|
| `shared/api/client.ts` | rider-app, driver-app | `shared/api/__tests__/*` (orphan jest infra — see *Limitations* below) |
| `admin-dashboard/src/lib/api.ts` | admin-dashboard | `admin-dashboard/src/lib/__tests__/api.test.ts` (vitest, runs in CI) |

Both export a `RateLimitError` class with this shape:

```ts
class RateLimitError extends Error {
  status: 429;
  retryAfterSeconds: number;     // RFC 9110 Retry-After, parsed
  limit: number | null;          // RateLimit-Limit (IETF draft)
  remaining: number | null;      // RateLimit-Remaining
  resetSeconds: number | null;   // RateLimit-Reset
  data: any;                     // Full backend body for diagnostics
  requestId?: string;            // shared client only — admin uses fetch directly
}
```

**Important:** these are TWO classes with the same shape, not one
shared class. A `RateLimitError` thrown by the rider app will not
satisfy `instanceof RateLimitError` imported from the admin file.
This is intentional — the admin-dashboard runs in a different
JavaScript runtime (Next.js SSR + browser) than the React Native
apps, and a shared module would drag React Native deps into Next's
bundle. **If you change one, change the other.** The runbook is the
single source of truth on the contract.

### Why we don't auto-retry

A 429 from `/auth/logout-all` has side effects (it tries to bump
token_version + revoke refresh tokens). Auto-retrying would mask
the real failure mode (tight retry loop hammering an already-slow
endpoint) without giving the user feedback. Even safe GETs aren't
auto-retried — the user is staring at a spinner; surfacing
`"Try again in 58s"` UX is strictly better than blocking on a
sleep that's longer than the user's attention span.

### Parsing rules

`Retry-After` per RFC 9110 §10.2.3 — TWO valid forms:

1. **delta-seconds** (preferred): `Retry-After: 60` — integer string.
2. **HTTP-date**: `Retry-After: Fri, 31 Dec 2025 23:59:59 GMT`.

The client parser handles both (date form converted to seconds-from-
now, clamped to 0 for past dates). Malformed values fall back to
`body.retry_after` if present, otherwise 60s as a last resort.

`RateLimit-*` headers are integer strings; missing headers fall back
to `body.limit` for `RateLimit-Limit`, otherwise `null`.

---

## Operating

### Verifying the contract on a deployed environment

```bash
# /auth/logout is rate-limited to 3/minute. Burn the budget then
# inspect the 4th call's headers + body. Replace the URL host as
# appropriate.
for i in 1 2 3 4; do
  curl -sS -i -X POST https://api.spinr.app/auth/logout \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" \
    -d '{"refresh_token":"x"}' | head -20
  echo "---"
done
```

The 4th response must show:
- `HTTP/2 429`
- `Retry-After: 60`
- `RateLimit-Limit: 3`
- `RateLimit-Remaining: 0`
- `RateLimit-Reset: 60`
- Body containing `"error": "rate_limit_exceeded"` and `"retry_after": 60`.

If any of these are missing, the typed `RateLimitError` parser falls
back to defaults and the UX silently regresses to "Request failed".

### Adding a new rate-limited endpoint

1. Decorate with `@limiter.limit("N/window")` per the existing pattern.
2. The slowapi handler picks it up automatically — no per-endpoint
   wiring needed.
3. Verify the 429 shape via the curl probe above before merging.

### Adding a new 429 source (NOT slowapi-mediated)

If you raise `HTTPException(status_code=429)` directly (like
`_check_otp_lockout` does), the slowapi handler does NOT fire — you
must set the headers yourself. Mirror the OTP path:

```python
raise HTTPException(
    status_code=429,
    detail="ERR_<your_code>",
    headers={
        "Retry-After": str(retry_after_seconds),
        "RateLimit-Limit": str(limit_amount),
        "RateLimit-Remaining": "0",
        "RateLimit-Reset": str(retry_after_seconds),
    },
)
```

---

## Limitations & Known Debt

- **`shared/api/__tests__/` is orphan infrastructure.** The files
  `client.refresh.test.ts` and `client.authHeader.test.ts` exist
  but neither rider-app's nor driver-app's jest config picks them
  up (no `roots` / `rootDir` outside the app directory). Adding a
  shared-side jest config that runs them is a P3 item; until then,
  the admin-dashboard vitest tests are the only enforced contract
  for the typed-error shape. See `admin-dashboard/src/lib/__tests__/api.test.ts`
  for the rate-limit cases.
- **`get_expiry()` returns window size, not bucket reset time.** A
  client that retries exactly at `Retry-After` will succeed — but a
  client retrying at `Retry-After / 2` because they trust the value
  to be tight will sometimes re-trip the limit. The contract is
  "wait at least Retry-After seconds", not "Retry-After is the
  exact reset moment". Honour that.

---

## What NOT to Do

- **Do not auto-retry 429s on the client.** Fail fast and let the
  caller decide. POSTs may have side effects; even safe GETs are
  blocked by a sleep that's longer than user attention. The `/auth/refresh`
  on 401 is the only auto-retry the client does, and it's keyed on
  `instanceof RateLimitError === false`.
- **Do not strip `RateLimit-*` headers in middleware "for security".**
  They expose a small amount of capacity info but no PII or
  authentication state. Stripping them silently regresses the typed-
  error UX to a generic message.
- **Do not change `Retry-After` to a milliseconds value to be more
  precise.** RFC 9110 mandates integer seconds. A floating-point or
  millisecond value will confuse every caching proxy and standards-
  compliant client between us and the user.
- **Do not introduce a third `RateLimitError` class without updating
  this runbook.** The two-class duplication (shared + admin) is
  deliberate scope-control for B-P1-8; a third copy should pause for
  consolidation, not just continue the pattern.
