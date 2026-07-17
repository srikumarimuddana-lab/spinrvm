# Event-Loop and Backpressure Hardening Design

**Date:** 2026-07-16
**Status:** Approved
**Branch:** `codex/event-loop-backpressure-hardening`

## Objective

Prevent synchronous Stripe, rate-limit Redis, and saturated database work from
blocking FastAPI's event loop or consuming request time without a bounded
deadline. Preserve current API contracts, payment idempotency, rate-limit
semantics, and Spinr's modular FastAPI/Postgres/Redis architecture.

This work is local only. It does not access providers, production systems,
secrets, or customer data and does not deploy or push.

## Verified Findings

### Stripe SDK

The finding is confirmed and broader than the originally cited lines.
Synchronous Stripe SDK calls run directly in async route handlers in:

- `backend/routes/payments.py`: customer creation, setup intents, payment-method
  listing/attachment/detachment, and customer default-method changes.
- `backend/routes/wallet.py`: customer creation.
- `backend/routes/corporate_accounts.py`: customer creation.
- `backend/routes/disputes.py`: refund creation.

`backend/utils/stripe_charge.py` provides the established pattern:
`await asyncio.to_thread(lambda: stripe.<resource>.<operation>(...))`.

### Rate limiting

The finding is confirmed. SlowAPI 0.1.9 invokes its synchronous limit strategy
inside its async decorator before awaiting the endpoint. With Redis storage,
every decorated async request can therefore perform a blocking network
round-trip on the event-loop thread.

### Database executor

The deadline finding is confirmed. `repositories/_base.py::run_sync` submits
work to the 64-thread executor and awaits it without a timeout. The existing
deadline context is consulted only after an exception when deciding whether to
retry.

The queue-depth finding is partially outdated: the admin monitoring route
already reads `_DB_EXECUTOR._work_queue.qsize()`. There is no continuous queue
metric at submission/completion, so saturation can still be invisible between
admin polls.

## Design

### 1. Stripe calls

Every direct synchronous Stripe SDK call in the four in-scope route modules
will run through `asyncio.to_thread`. The SDK call and all of its keyword
arguments remain inside the callable passed to the thread.

The change will not:

- alter Stripe idempotency keys;
- change payment-intent, customer, or refund metadata;
- catch or soften new exception classes;
- move database writes or response construction into worker threads; or
- introduce a new executor or service boundary.

Tests will use a deliberately blocking fake Stripe method and an event-loop
sentinel. They must demonstrate that the sentinel advances while the fake SDK
call is blocked, then verify the existing response and exception behavior.

### 2. Async rate-limit compatibility layer

A small compatibility limiter will preserve Spinr's existing
`default_limiter.limit(...)` decorator surface while using:

- `limits.aio.storage` for asynchronous Redis or memory storage;
- `limits.aio.strategies.FixedWindowRateLimiter` for awaited limit checks; and
- SlowAPI's `RateLimitExceeded`/`Limit` objects so the existing 429 exception
  handler and response shape remain compatible.

The compatibility layer will preserve the options actually used by this
repository: static or callable limit values, custom key functions, scopes,
method filtering, per-method scopes, exemption callbacks, costs, enabled
state, and `request.state.view_rate_limit`.

Storage behavior follows the repository's documented degradation policy:

- OTP/auth abuse controls fail closed with a generic 503 if Redis is
  unavailable.
- Non-security endpoint limits may use the asynchronous in-memory fallback,
  with an error log and degradation metric.
- No raw Redis/provider exception is returned to a client.

The current synchronous Redis startup probe will be removed from the
request-limiter construction path. Async storage will connect lazily during an
awaited check. Test reset fixtures will await async storage reset rather than
leaving an un-awaited coroutine.

Compatibility tests will cover successful requests, 429 behavior, dynamic
limits, request state, disabled limiting, method/scope behavior, OTP
fail-closed behavior, and general-limit memory degradation. An event-loop
sentinel test will demonstrate that a delayed async storage operation does not
block unrelated coroutines.

### 3. Database deadlines and queue telemetry

`run_sync` will read `remaining_seconds()` immediately before submission:

1. With no request deadline, existing behavior is preserved.
2. With an exhausted deadline, no executor work is submitted.
3. With remaining time, the executor future is awaited through
   `asyncio.wait_for` using that remaining budget.
4. On timeout, queued work is cancelled when possible and the request receives
   the existing generic database-unavailable 503 contract.

A running Python thread cannot be forcefully stopped. If the executor has
already started a timed-out call, it may finish in the background, but the
request coroutine and event loop are released. Payment correctness continues
to rely on existing database/Stripe idempotency rather than thread
cancellation.

Deadline expiry will not record a database circuit-breaker failure. The
`X-Deadline-Ms` value is client controlled; allowing it to trip the shared
breaker would permit a client to shed unrelated traffic by sending
artificially tiny deadlines. Genuine executor/database exceptions continue to
feed the breaker unchanged.

The executor queue size will be recorded as a gauge immediately around
submission and completion/timeout. The existing admin monitoring snapshot is
retained.

Tests will prove:

- expired work is not submitted;
- a slow future is bounded by the remaining deadline;
- timeout returns a sanitized 503 without changing breaker state;
- no-deadline calls preserve current behavior; and
- queue telemetry is emitted around executor use.

## Delivery and Verification

Implementation is split into independently testable subtasks of no more than
three files, one logical change per commit, and approximately 200 changed lines
or fewer per commit. Each behavior change follows red-green-refactor TDD.

Focused backend tests will run using the dependencies already available in the
workspace. No global backend dependency installation will be attempted. If a
required package is unavailable, the exact unexecuted verification will be
reported rather than represented as passing.

After code changes, the focused regression suites will run and Graphify will be
rebuilt. No production access, deployment, push, or pull request is included.
