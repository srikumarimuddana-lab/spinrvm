# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude Code (spinr platform) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch, payments, ai, corporate, admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | Scheduled-ride audit (`spinr-dispatch-reviewer`), P1 finding #2 |

## 1. Issue / gap identified

`redis_set_nx` (`backend/utils/redis_client.py`) was the one primitive in that module that caught a real Redis error and silently fell through to the in-process per-replica fallback, logging only a `warning`. Every other function in the same module (`redis_get`/`redis_set`/`redis_incr`/`redis_expire`/`redis_delete`) raises on the same class of error. During a real production Redis blip, every replica's `redis_set_nx` call independently "won" its own local lock, so a leader-election or dedupe guarantee silently became "every replica proceeds independently" — duplicate reminders, duplicate driver nudges, duplicate admin escalations, or a lost mutual-exclusion guarantee — with no signal louder than a warning log.

## 2. Root cause

`redis_set_nx`'s `except Exception as e: logger.warning(...)` block did not `raise` and did not `return`, so execution fell through past the `if r:` block into the in-process-fallback code that follows — code intended only for the "Redis not configured at all" case, not "Redis configured but erroring."

## 3. Fix / remediation

- `redis_set_nx` now raises on a real (Redis-configured-but-unavailable) error, matching every sibling function's contract.
- Every one of the 17 call sites across the codebase was individually checked for how it would behave against the new contract (see §4). Two categories required a code change; the rest were already safe:
  - **6 background loops** called `redis_set_nx` directly inside `while True:` with no surrounding `try/except` at all — an unhandled exception would have killed the loop task permanently (until process restart), not just skipped one tick. Wrapped each in a `try/except` that logs at `error` and proceeds with the tick (these locks are documented throttles, not correctness guards — the atomic DB claim or Stripe idempotency key is the real safety net in every case): `driver_claim_reaper_loop`, `preauth_capture_loop`, `payment_retry_loop`, `orphaned_hold_reconciler_loop`, `offer_expiry_reaper_loop`, `suspension_reactivation_loop`.
  - **1 more background loop** (`check_expiring_subscriptions` in `routes/drivers/subscriptions.py`) had the same unguarded-`while True:` shape — same fix, same "proceed with the tick" reasoning (this lock only prevents duplicate notifications).
  - **1 AI conversation lock** (`ai/orchestrator.py::run_chat_turn`) — wrapped to fail OPEN with a loud log, mirroring the file's own existing documented policy for `_over_daily_cap`: blocking every AI conversation on a Redis blip is worse than occasionally racing two concurrent turns on the same conversation (a data-quality edge case, not a safety one).
  - **2 HTTP-request-handler locks that ARE correctness/anti-abuse guards, not throttles** — wrapped to fail CLOSED with a 503 instead: `routes/rides/payments.py`'s wallet-settlement re-drive exclusivity lock (money path), and `routes/admin/subscriptions.py`'s invoice-resend anti-email-bomb cooldown.
  - **10 other call sites** (the remaining loop-body dedupe keys inside `scheduled_rides.py`, and the other background loops — `surge_engine`, `stripe_reconcile`, `zoho_desk_sync`, `referral_payout`, `distance_reconciliation`, `reconciliation`, `retention_purge`, `t4a_annual_job`, `period1_distance_finalizer`, `ledger_projection`) already wrapped their `redis_set_nx` call in their own `try/except` (either an outer loop-level catch-all or an inline dedupe-specific fail-open) — confirmed safe as-is, no change needed.

## 4. Risk & impact on existing functionality

- **Full blast radius grepped**: 17 call sites across `backend/utils/`, `backend/ai/`, `backend/routes/` — every one individually read and classified (see §3). None left unaddressed.
- **Behavior change is deliberate and scoped to "what happens during a Redis error"** — the happy path (Redis healthy, or Redis unset in dev) is byte-for-byte unchanged for every caller.
- **The 6 fixed background loops**: pre-fix, an unhandled exception here would have SILENTLY killed the loop task (no crash log distinguishing "loop died" from "loop never started"; only `_record_heartbeat` stopping would eventually show up as a stalled-loop alert, if one exists for that loop). Post-fix, the loop survives and logs loudly — a strict improvement, not a new risk.
- **The 2 fail-closed call sites (503)**: a genuine behavior change from "silently proceed as if the lock always succeeds" (the old per-replica-fallback illusion) to "reject the request during a Redis outage." This trades a masked correctness risk (double-settlement re-drive, email-bomb) for a visible, retryable failure — the more conservative choice for a money path and an anti-abuse guard respectively.
- **No change to any RPC/DB-level correctness guard** — every fixed call site's underlying atomic DB claim, Stripe idempotency key, or (for the two fail-closed cases) the guard itself was already documented as the actual safety net; this fix only changes how loudly a Redis outage is surfaced and how each caller degrades.

## 5. User-experience effect

- **Riders/drivers**: no visible change during normal operation. During a genuine Redis outage: a wallet-settlement retry (`/rides/{id}/process-payment` re-drive) now returns "Payment retry temporarily unavailable — please try again in a moment" (503) instead of silently proceeding without its exclusivity guard.
- **Admins**: an invoice-resend during a Redis outage now returns "Please try again in a moment" (503) instead of silently proceeding without the anti-spam cooldown.
- **AI assistant users**: unaffected — the conversation lock fails open exactly as before in spirit, just now with a loud log instead of a silent one.
- Not visible mid-session to anyone; only surfaces during an actual Redis-configured-but-unavailable incident.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/redis_client.py` | `redis_set_nx` now raises on error instead of silently falling back | Match every sibling function's fail-loud contract |
| `backend/utils/driver_claim_reaper.py` | Wrapped lock-acquire call in try/except, proceeds with tick on error | Loop previously had no surrounding guard — would have crashed |
| `backend/utils/preauth_capture.py` | Same | Same |
| `backend/utils/payment_retry.py` | Same | Same |
| `backend/utils/orphaned_hold_reconciler.py` | Same | Same |
| `backend/utils/offer_expiry_reaper.py` | Same | Same |
| `backend/utils/suspension_reactivation.py` | Same | Same |
| `backend/routes/drivers/subscriptions.py` | Same, for `check_expiring_subscriptions`'s expiry-check lock | Same |
| `backend/ai/orchestrator.py` | Wrapped conversation-lock acquire, fails open with a loud log | Match the file's own existing daily-cap fail-open policy |
| `backend/routes/rides/payments.py` | Wrapped wallet re-drive lock acquire, fails closed with 503 | This lock IS the exclusivity guard on a money path |
| `backend/routes/admin/subscriptions.py` | Wrapped invoice-resend cooldown acquire, fails closed with 503 | This lock IS the anti-email-bomb guard |
| `backend/tests/test_redis_client_coverage.py` | Rewrote `test_set_nx_falls_back_to_local_on_redis_error` → `test_set_nx_raises_when_configured_but_erroring` | Old test asserted the exact behavior being fixed |
| `backend/tests/test_suspension_reactivation_coverage.py` | **Removed** `test_redis_lock_error_is_not_swallowed_by_loop_body` (explicitly documented + asserted the old crash-on-error behavior; with the fix, this test spun in an infinite loop instead of raising, since it mocked `asyncio.sleep` as a no-op with no stop condition); added a new test covering the fixed behavior | Old test encoded the bug as "current behavior, not asserting it's desirable" — now superseded |
| `backend/tests/test_driver_claim_reaper_coverage.py`, `test_preauth_capture_coverage.py`, `test_payment_retry_coverage.py`, `test_orphaned_hold_reconciler_coverage.py`, `test_offer_expiry_reaper_coverage.py`, `test_subscriptions_coverage.py`, `test_ai_orchestrator.py`, `test_rides_payments_coverage.py`, `test_admin_subscription_invoice.py` | Added a "survives/fails correctly on Redis error" test per fixed call site | Cover each of the 10 changed call sites |

## 7. Before / after

```python
# Before (utils/redis_client.py)
async def redis_set_nx(key: str, value: str, ttl: int) -> bool:
    r = await _get_redis()
    if r:
        try:
            ok = await r.set(key, value, nx=True, ex=ttl)
            return bool(ok)
        except Exception as e:
            logger.warning(f"redis_set_nx error: {e}")
    if _local_get(key) is not None:
        return False
    _local_set(key, value, ttl)
    return True
```

```python
# After
async def redis_set_nx(key: str, value: str, ttl: int) -> bool:
    r = await _get_redis()
    if r is not None:
        try:
            ok = await r.set(key, value, nx=True, ex=ttl)
            return bool(ok)
        except Exception as e:
            logger.error(f"[REDIS] redis_set_nx({key!r}) failed — Redis configured but unavailable: {e}")
            raise
    if _local_get(key) is not None:
        return False
    _local_set(key, value, ttl)
    return True
```

```python
# Before (each of the 6 unguarded loops, e.g. driver_claim_reaper_loop)
if not await redis_set_nx("spinr:driver:claim_reaper:lock", _pod_id(), lock_ttl):
    _record_heartbeat(_LOOP_NAME)
    await asyncio.sleep(REAP_INTERVAL_SECONDS)
    continue
```

```python
# After
try:
    got_lock = await redis_set_nx("spinr:driver:claim_reaper:lock", _pod_id(), lock_ttl)
except Exception as lock_err:
    logger.error(f"driver_claim_reaper: leader lock unavailable ({lock_err}), proceeding without it")
    got_lock = True
if not got_lock:
    _record_heartbeat(_LOOP_NAME)
    await asyncio.sleep(REAP_INTERVAL_SECONDS)
    continue
```

## 8. Rollback plan

- Pure application-logic change across 11 files, no migration, no data touched. `git revert` is safe.
- If the fail-closed (503) behavior on the two request-handler paths proves too disruptive in practice (e.g. Redis flakiness causing frequent 503s where the old silent-degrade was actually preferred), that specific call site can be flipped to fail-open independently of reverting the core `redis_set_nx` fix — the two decisions are separable.

## 9. Verification performed

- [x] Automated tests: `pytest backend/tests/test_driver_claim_reaper_coverage.py backend/tests/test_preauth_capture_coverage.py backend/tests/test_payment_retry_coverage.py backend/tests/test_orphaned_hold_reconciler_coverage.py backend/tests/test_orphaned_hold_reconciler_loop_coverage.py backend/tests/test_offer_expiry_reaper_coverage.py backend/tests/test_suspension_reactivation_coverage.py backend/tests/test_redis_client_coverage.py backend/tests/test_ai_orchestrator.py backend/tests/test_rides_payments_coverage.py backend/tests/test_admin_subscription_invoice.py backend/tests/test_subscriptions_coverage.py backend/tests/test_scheduled_rides_coverage.py backend/tests/test_scheduled_dispatch_cr.py -q --no-cov` — **364 passed, 0 failed**.
- [x] Blast-radius grep performed and every one of the 17 call sites individually read and classified (§3) — not just the ones that needed a fix.
- [x] Found and fixed a pre-existing test (`test_redis_lock_error_is_not_swallowed_by_loop_body`) that explicitly encoded the old buggy behavior and would have hung indefinitely under the new fix rather than failing cleanly — caught by running the full test file, not just the new test in isolation.
- [ ] Manual repro against a real Redis instance under a simulated outage — not performed, no Redis/staging access in this environment.

## What was NOT verified

- Not tested against a real multi-replica deployment during an actual Redis outage — all coverage is unit-level with mocked Redis errors.
- The fail-open vs. fail-closed classification for each of the 10 changed call sites was reasoned through from each lock's documented purpose (throttle vs. correctness guard), not empirically validated against production incident data.
