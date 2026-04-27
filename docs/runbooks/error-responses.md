# Runbook — Error Response Contract

**Owner:** `backend` · **Cadence:** Always-on; runbook on contract change
**Closes:** B-P2-1 (sanitised 5xx + request-id correlation)

---

## Why This Matters

A pre-B-P2-1 audit of `backend/routes/` found **32 sites** doing
`raise HTTPException(500, detail=str(e))` or
`detail=f"...{e}"`. The detail string went straight to the client.
Worst leaks:

| Site | Leak content |
|---|---|
| `routes/drivers.py:2981` (Stripe subscription charge) | Stripe charge IDs (`ch_…`), customer IDs (`cus_…`), decline codes |
| `routes/auth.py:398` (OTP login, **unauthenticated**) | Supabase row IDs, Firebase JWT errors |
| `routes/corporate_accounts.py` (admin) | Postgres unique-constraint names, FK names |
| `routes/admin/auth.py:372` (admin token decode) | JWT library error messages |
| `routes/payments.py:332` (rider card add) | Pydantic structure hints |

Plus a parallel correlation problem: every exception handler was
generating its own fresh `request_id`, so the id the client saw in
the response body **did not match** the id loguru stamped into the
request-lifecycle logs. A support ticket quoting "request_id =
abc123" couldn't be cross-referenced against logs.

This runbook pins what the client-facing error response shape is,
what the sanitiser does, and how route authors should signal
"this 5xx detail is safe to expose".

---

## Wire Format

Every error response (4xx and 5xx) emits:

```json
{
  "success": false,
  "detail": "<the user-facing message OR 'Internal server error'>",
  "error": {
    "code": <int>,
    "message": "<same as detail>",
    "request_id": "<correlates to server logs>",
    "timestamp": "<ISO-8601>",
    "sanitised": true   // present ONLY when the 5xx detail was scrubbed
  }
}
```

Headers always include `X-Request-ID: <correlates to body.error.request_id>`.

The `success: false` + top-level `detail` shape is for the mobile
client (`shared/api/client.ts::extractErrorMessage`); the nested
`error.*` shape is for newer clients and structured logging
consumers. Don't drop either — both have callers.

---

## Sanitisation Rules

### 5xx HTTPException — sentinel-or-sanitise

When a route raises `HTTPException(status_code=5xx, detail=...)`,
the handler in `backend/utils/error_handling.py::http_exception_handler`
applies this gate:

| Detail shape | Behaviour |
|---|---|
| Matches regex `^ERR_[A-Z0-9_]+$` | Pass through unchanged. Sentinel — vetted by route author. |
| Anything else | Replace with `"Internal server error"`. Set `error.sanitised: true`. Log the original detail server-side paired with the request_id. |

The rule is deliberately strict: a contributor cannot accidentally
leak by writing a plausible-sounding sentence. Routes that need to
convey 5xx info must either:

1. **Use an `ERR_*` sentinel** the mobile client can branch on.
2. **Raise a `SpinrException`** (which goes through
   `spinr_exception_handler`, has structured fields, and is the
   path for "this is a known business error with a vetted
   message").

### 4xx HTTPException — pass through

`detail` is preserved unchanged. 4xx messages are user-facing UX
("Card declined", "Invalid phone number", "Ride not found") and
have always been the route author's responsibility.

### Unhandled exceptions — full sanitisation

`general_exception_handler` returns:

```json
{
  "success": false,
  "error": {
    "code": 9001,
    "message": "An unexpected error occurred",
    "request_id": "<id>",
    "timestamp": "..."
  }
}
```

In `ENV in {development, local}` only, `exception_type` and a
truncated `detail` (≤500 chars) are added. **Never** in production.
The full traceback always hits the server log.

### `RequestValidationError` — pass through

FastAPI 422s emit field-level error lists. These contain the
field name and a short message ("Invalid email format", "Field
required"). Considered safe to expose — they describe the
request, not the server's internal state.

---

## Request-ID Correlation

### Generation and propagation

1. **`RequestIDMiddleware`** (`core/middleware.py`) runs first.
   It reads `X-Request-ID` from the inbound headers (clients can
   set their own) or generates a UUID.
2. The id is bound to `request.state.request_id` AND to the
   loguru context via `logger.contextualize(request_id=...)` so
   every log line emitted during this request lifecycle carries it.
3. Every exception handler reads `request.state.request_id` via
   `_resolve_request_id(request)` and emits it in:
   - The response body (`error.request_id`)
   - The response header (`X-Request-ID`)

The body and header values are guaranteed to match — the test
`test_request_id_in_body_matches_x_request_id_header` pins this.

### Why this matters operationally

Support workflow:

1. User reports "the app showed me an error".
2. Mobile client surfaces `error.request_id` (the typed
   `RateLimitError` from B-P1-8 already exposes this; the generic
   error path does too via `recordApiError`).
3. Support quotes the id.
4. Ops greps server logs for `[<id>]` and finds every log line
   for that request — including the full sanitised exception
   detail.

Without the consolidation, ops would need to grep two different
ids (the middleware's vs the handler's) and they'd never match
because they were generated independently.

---

## Patterns for Route Authors

### ✅ Correct: external-service failure (Stripe, Twilio, Firebase)

```python
try:
    charge = stripe.PaymentIntent.create(...)
except Exception as e:
    # Log full detail server-side; never interpolate into the
    # client-facing message. logger.exception captures the
    # traceback automatically.
    logger.exception(
        f"Stripe subscription charge failed for driver {driver['id']}"
    )
    raise HTTPException(
        status_code=402,
        detail="Payment failed. Please try another payment method.",
    ) from e
```

The canonical reference site is
`backend/routes/drivers.py::create_subscription` (around line
2981).

### ✅ Correct: known business error with sentinel

```python
if not redis_available:
    raise HTTPException(
        status_code=503,
        detail="ERR_AUTH_UNAVAILABLE",  # sentinel — passes the gate
    )
```

The mobile client branches on `ERR_AUTH_UNAVAILABLE` to show a
"please retry" UX. Free text would be sanitised away.

### ✅ Correct: typed Spinr exception

```python
raise ResourceNotFoundException("Ride", ride_id)
# → 404, error.code=3001, message="Ride not found: <id>",
#   handled by spinr_exception_handler
```

Use this when the error has structured fields the client should
see (`details.resource_type`, `details.resource_id`).

### ❌ Wrong: interpolating exception into detail

```python
# BAD — pre-B-P2-1 leak pattern
except StripeError as e:
    raise HTTPException(500, detail=f"Payment failed: {e}")
```

The sanitiser will catch this and replace `detail` with
`"Internal server error"` (so the leak is bounded), but you lose
the chance to give the user actionable text. Use the pattern
above instead.

### ❌ Wrong: free-text 5xx detail

```python
# Sanitiser will replace detail with "Internal server error"
raise HTTPException(503, detail="Database connection pool exhausted")
```

Use a sentinel (`ERR_DB_POOL`) or a `SpinrException` subclass.

---

## Operating

### Verifying the contract on a deployed environment

```bash
# 1. Sanitisation: trigger a 500 and confirm the body is generic.
#    /api/admin/staff/{id} with a malformed id produces a 500
#    today (Supabase exception interpolation).
curl -i -X PATCH "https://api.spinr.app/api/admin/staff/INVALID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}' | head -30

# Expected:
#   HTTP/2 500
#   X-Request-ID: <id>
#   {"success":false,"detail":"Internal server error",
#    "error":{"code":500,"message":"Internal server error",
#             "request_id":"<id>","sanitised":true,...}}

# 2. Correlation: client-set X-Request-ID round-trips.
curl -i -X GET "https://api.spinr.app/api/admin/rides/nonexistent" \
  -H "X-Request-ID: support-ticket-12345" \
  -H "Authorization: Bearer $TOKEN" | grep -i "x-request-id"

# Expected:  X-Request-ID: support-ticket-12345
```

### Investigating a support ticket

```bash
# User says "I got an error, request_id was abc123def456".
# Pull every log line from that request lifecycle:
grep "abc123def456" /var/log/spinr/app.log | head -50

# The sanitised detail (the actual exception that was scrubbed
# from the client response) appears as:
#   [abc123def456] Sanitised 5xx HTTPException detail at POST /...
#                  status=500 detail=<full original detail>
# Plus, if the exception was unhandled:
#   [abc123def456] Unhandled exception at POST /...:
#                  StripeError: Error 1002: Charge ch_xyz...
```

### Adding a new sentinel

1. Pick a name following the `ERR_<UPPER_SNAKE>` pattern.
2. Use it in route code: `HTTPException(503, detail="ERR_NEW_THING")`.
3. Update the mobile client's error parser if the UX should
   branch on it (otherwise it just renders "ERR_NEW_THING" as a
   string, which is accepted but not great UX).
4. No backend test is required for the sentinel itself — the
   regex `^ERR_[A-Z0-9_]+$` catches anything matching the
   shape. The behaviour is pinned by
   `test_passes_through_err_sentinel`.

---

## Limitations & Known Debt

- **The 32 leak sites are not bulk-rewritten.** The framework-level
  intercept catches them all (5xx detail gets sanitised), but the
  routes still log via `logger.error(f"...{e}")` instead of
  `logger.exception(...)`. The latter captures the full traceback;
  the former only the message. P3 follow-up: bulk-rewrite the
  call sites for log fidelity.
- **4xx detail is fully trusted.** Free-text 4xx like
  `f"Card declined: {stripe_error.user_message}"` could leak
  Stripe IDs if `user_message` happens to contain them.
  Mitigation today: 4xx detail flows are reviewed at PR time.
  Stricter: another audit + selective sanitisation. Tracked as
  P3.
- **The dev-mode bypass exposes exception type + truncated detail
  in the body.** Intentional for local debugging. Verified
  `_is_dev` only reads `settings.ENV` and never trusts headers.

---

## What NOT to Do

- **Do not bypass the sanitiser by raising directly via
  `JSONResponse(status_code=500, content={"detail": str(e)})`.**
  That returns the raw response without going through any
  handler. If you ever need a 5xx that is NOT a sanitised
  generic message, raise a `SpinrException` subclass instead.
- **Do not generate `request_id` inside route code.** The
  middleware already did it; reading it from
  `request.state.request_id` keeps the correlation invariant.
  Generating a new one in a route would mean the response body
  carries a different id from the log lines.
- **Do not log exception details with `logger.warning(f"...{e}")`
  on auth/payment/dispatch paths.** Per CLAUDE.md, those failures
  must surface loudly via `logger.error` or `logger.exception`.
  The runbook for those failure classes
  (`docs/runbooks/auth-tokens.md`, `stripe-reconciliation.md`)
  expects log entries, not warnings.
- **Do not return `success: true` on an error path.** Some legacy
  routes did this with a non-200 status; downstream consumers
  (Sentry alert rules, the mobile client's `extractErrorMessage`)
  use `success` as their primary discriminator. Always `false`
  on error.
