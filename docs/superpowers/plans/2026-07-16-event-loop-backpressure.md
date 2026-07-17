# Event-Loop and Backpressure Hardening Implementation Plan

> Execute inline in the isolated worktree. Follow red-green-refactor TDD and
> commit each task before starting the next.

**Goal:** Remove blocking provider/rate-limit calls from FastAPI's event loop
and bound database executor waits by the request deadline.

**Architecture:** Keep the existing route modules and DB executor. Stripe work
moves to the standard asyncio worker pool, rate-limit checks use an async
compatibility facade backed by `limits.aio`, and `run_sync` enforces the
remaining deadline while emitting queue telemetry.

**Constraints:** Local only; no provider access, deploy, push, PR, migration, or
dependency installation. Each task changes at most three files.

---

## Task 1: Offload Stripe calls in payments routes

**Files:**

- Create: `backend/tests/test_stripe_event_loop_offload.py`
- Modify: `backend/routes/payments.py`

1. Add tests that patch representative customer/setup/payment-method SDK calls
   with a blocking synchronous fake.
2. Start the handler and an event-loop sentinel concurrently; assert the
   sentinel advances before releasing the fake.
3. Run the new test and confirm it fails because the handler blocks.
4. Wrap all direct Stripe SDK calls in `payments.py` with
   `await asyncio.to_thread(...)`, preserving arguments and exceptions.
5. Run the new test and focused payment tests.
6. Commit: `fix(payments): offload Stripe SDK calls`

## Task 2: Offload wallet and corporate Stripe calls

**Files:**

- Modify: `backend/tests/test_stripe_event_loop_offload.py`
- Modify: `backend/routes/wallet.py`
- Modify: `backend/routes/corporate_accounts.py`

1. Add blocking-fake tests for wallet and corporate customer creation.
2. Run the new cases and confirm event-loop blocking.
3. Wrap both customer-creation calls with `asyncio.to_thread`.
4. Run the new cases plus focused wallet/corporate tests.
5. Commit: `fix(payments): offload customer creation calls`

## Task 3: Offload dispute refunds

**Files:**

- Modify: `backend/tests/test_stripe_event_loop_offload.py`
- Modify: `backend/routes/disputes.py`

1. Add a blocking-fake refund test that verifies the event loop remains
   responsive and the existing idempotency key reaches Stripe unchanged.
2. Run it and confirm failure.
3. Wrap refund creation with `asyncio.to_thread`.
4. Run the new case and `test_dispute_refund_cents.py`.
5. Commit: `fix(payments): offload dispute refunds`

## Task 4: Build the async limiter facade

**Files:**

- Create: `backend/utils/async_limiter.py`
- Create: `backend/tests/test_async_limiter.py`

1. Add unit tests for static/callable limits, enabled state, key/scope
   construction, method filters, exemption, costs, request state, and 429
   compatibility.
2. Add a delayed async fake-storage test proving an unrelated coroutine
   advances during the awaited check.
3. Run the tests and confirm import/behavior failures.
4. Implement the smallest decorator-compatible facade using `limits.aio`
   storage/strategy and SlowAPI `Limit`/`RateLimitExceeded`.
5. Run the unit tests.
6. Commit: `feat(rate-limit): add async limiter facade`

## Task 5: Integrate async rate limiting and failure policy

**Files:**

- Modify: `backend/utils/rate_limiter.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_rate_limit_response_shape.py`

1. Add integration tests for current 429 response shape, async fixture reset,
   OTP/auth Redis failure returning sanitized 503, and non-security fallback to
   async memory storage.
2. Run them and confirm the synchronous limiter fails the async requirements.
3. Construct `default_limiter` with the async facade and remove the
   synchronous request-limiter Redis probe.
4. Preserve existing exported decorators, handler, and app-state setup.
5. Update the shared test fixture to await async storage reset.
6. Run async limiter, response-shape, promo-rate-limit, and auth hardening
   tests.
7. Commit: `fix(rate-limit): await Redis checks off event loop`

## Task 6: Enforce DB deadlines and emit queue telemetry

**Files:**

- Modify: `backend/tests/test_db_executor.py`
- Modify: `backend/repositories/_base.py`

1. Add tests proving expired deadlines do not call `run_in_executor`, slow
   futures time out, breaker state is unchanged by a client deadline, no
   deadline preserves behavior, and queue gauges are emitted.
2. Run the executor tests and confirm failures.
3. Import `remaining_seconds`, reject exhausted budgets before submission, and
   await submitted futures with `asyncio.wait_for` when a deadline exists.
4. Cancel timed-out queued futures when possible and raise the existing
   sanitized database-unavailable exception.
5. Emit queue-depth gauges around submission and in `finally`.
6. Run executor, deadline, circuit-breaker, and DB error tests.
7. Commit: `fix(db): bound executor waits by request deadline`

## Task 7: Focused regression and static verification

**Files:** None unless a discovered regression requires returning to the
relevant task.

1. Run:

   ```powershell
   python -m pytest backend/tests/test_stripe_event_loop_offload.py backend/tests/test_payment_sheet.py backend/tests/test_wallet.py backend/tests/test_corporate_stripe_customer.py backend/tests/test_dispute_refund_cents.py
   python -m pytest backend/tests/test_async_limiter.py backend/tests/test_rate_limit_response_shape.py backend/tests/test_promo_rate_limit.py backend/tests/test_p1_auth_hardening.py
   python -m pytest backend/tests/test_db_executor.py backend/tests/test_db_circuit_breaker.py backend/tests/test_error_handling.py
   python -m ruff check backend/routes/payments.py backend/routes/wallet.py backend/routes/corporate_accounts.py backend/routes/disputes.py backend/utils/async_limiter.py backend/utils/rate_limiter.py backend/repositories/_base.py
   ```

2. If dependencies are missing, record the exact command and error. Do not
   install packages globally.
3. Verify every in-scope direct Stripe SDK call is inside `asyncio.to_thread`
   and no synchronous SlowAPI limiter remains on async endpoints.
4. Confirm `git diff --check` and review every commit/file count.

## Task 8: Rebuild Graphify

**Files:** Generated Graphify outputs only.

1. Run:

   ```powershell
   python -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"
   ```

2. Confirm the manifest HEAD/current source state and inspect the report for
   rebuild errors.
3. Commit only changed tracked Graphify outputs:
   `chore: rebuild graphify after backpressure hardening`
4. Re-run `git status --short` and report the final commit list and all
   verification evidence.
