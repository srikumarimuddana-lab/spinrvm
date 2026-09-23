# Corporate auto-top-up lock impact

## Issue/gap identified
Every API replica could initiate the same wallet top-up tick.

## Root cause
The 10-minute loop had Stripe idempotency but no shared ownership gate.

## Fix/remediation
Require strict Redis SET NX EX before each tick. Contention or Redis error skips work; Stripe idempotency and wallet/webhook behavior stay intact.

## Risk & impact on existing functionality
Blast radius is only `corporate_autotopup_loop` in `backend/core/lifespan.py`. Top-ups pause when Redis ownership is unavailable. The 510-second lease expires before the 540-second minimum interval.

## User-experience effect
No UI change. Top-ups may be delayed during Redis outage/contention; daily caps and Stripe idempotency remain in effect.

## Files modified
| File | Change | Reason |
|---|---|---|
| `backend/utils/corporate_autotopup.py` | Strict lease before tick | Gate tick starts across replicas |
| `backend/tests/test_corporate_autotopup.py` | Outage, contention, recovery, expiry coverage | Verify gate/cadence |
| `docs/change-log/2026-09-23-autotopup-lock.md` | Impact record | Record risk/verification |

## Before/after
```python
# Before: await run_autotopup_tick()
got_lock = await redis_set_nx(_LOCK_KEY, loop_pod_id(), 510)
if got_lock:
    await run_autotopup_tick()
```

## Rollback plan
Pause corporate billing with `corporate_billing_enabled=false` if needed; revert and redeploy to remove the gate. Stripe idempotency remains intact.

## Verification performed
`pytest tests/test_corporate_autotopup.py tests/test_replay_safety_payment_loops.py -q --no-cov` — 25 passed.

## What was NOT verified
No live Redis, Supabase, or Stripe calls. A tick exceeding 510 seconds still relies on Stripe idempotency.
