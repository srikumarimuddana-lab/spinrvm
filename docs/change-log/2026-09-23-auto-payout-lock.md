# Change Impact & Risk: automatic payout Redis lock

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | Backend / driver payouts |
| Domain (Sentry tag) | payments |
| PR / commit link | PR #5725 |
| Related issue or gap ID | PR 5725 implementation plan, Task 6 |

## 1. Issue / gap identified

The auto-payout loop ran its hourly stale-reserved sweep before acquiring Redis. That sweep can retry Stripe transfers, so Redis outages or contention could let multiple replicas transfer without distributed ownership. The Sunday batch also treated lock exceptions as successful acquisition.

## 2. Root cause

The lock covered only the Sunday batch, not the hourly stale-reserved transfer sweep; its exception handler also explicitly set `got_lock = True`.

## 3. Fix / remediation

The hourly loop now acquires the strict Redis lock before either the stale-reserved transfer sweep or Sunday batch. A lock error logs only the exception class, increments `spinr_loop_lock_unavailable_total{loop="auto_payout"}`, and skips both transfer-producing paths for that iteration. The lease stays at 85% of the 3600-second loop interval (3060 seconds), expiring before the next wake. Stale-running-batch finalization remains outside the lock because it only updates batch status and does not produce transfers.

## 4. Risk & impact on existing functionality

- Blast radius: isolated to transfer-producing paths in `auto_payout_loop`: `sweep_stale_reserved` and Sunday `run_weekly_auto_payout`.
- Stale-running-batch finalization, `auto_payout_batches.week_key` claims, payout reservation rows, attempt-scoped Stripe idempotency keys, and finalization rules remain unchanged.
- Before: the hourly stale sweep ran before any lock, and the Sunday batch proceeded on lock errors. After: neither path runs without acquisition. After a healthy acquisition, the stale sweep still runs hourly and the weekly batch still runs in its existing Sunday window.
- During Redis outage/contention, stranded-reservation retries may wait for the next healthy hourly iteration; scheduled weekly payouts may be delayed while the Sunday window is unhealthy. No payout amount or eligibility rules changed.
- No ride state or wallet-delta logic changed.

## 5. User-experience effect

Drivers may receive a stale reserved-payout retry or scheduled weekly payout later if Redis is unavailable or another replica owns the hourly lock. No app UI or notification copy changed; the hourly sweep resumes with a healthy lock, and the regular weekly batch retains its Sunday schedule.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/auto_payout.py` | Move strict Redis acquisition ahead of the hourly sweep and Sunday batch; fail closed and emit metric | Prevent all transfer-producing loop work without Redis ownership |
| `backend/tests/test_auto_payout.py` | Cover error→recovery, weekday and Sunday contention; assert neither sweep nor batch runs without lock, metric/redaction, and TTL | Prove every transfer path is gated and schedule resumes after acquisition |
| `docs/change-log/2026-09-23-auto-payout-lock.md` | Record impact and verification | Required runtime change record |

## 7. Before / after

```python
# Before
await sweep_stale_reserved(stripe_secret)  # ran before any lock
if _is_batch_window(now_local):
    got_lock = await redis_set_nx(LOCK_KEY, pod_id, int(interval * 0.85))
```

```python
# After
try:
    got_lock = await redis_set_nx(LOCK_KEY, pod_id, int(interval * 0.85))  # every hourly tick
except Exception as lock_err:
    logger.error("... skipping payout work (%s)", type(lock_err).__name__)
    _metric_inc("spinr_loop_lock_unavailable_total", {"loop": "auto_payout"})
    got_lock = False
if got_lock:
    await sweep_stale_reserved(stripe_secret)
    if _is_batch_window(now_local):
        await run_weekly_auto_payout()
```

## 8. Rollback plan

Revert the isolated loop-gate change and redeploy if payout timing becomes unacceptable. The lock does not change payout records; existing database claims and Stripe idempotency remain intact.

## 9. Verification performed

- [x] Automated: `/tmp/pr5725-venv/bin/python -m pytest backend/tests/test_auto_payout.py -q --no-cov` (72 passed).
- [ ] Manual repro in staging: not run; no production/staging changes were made.
- [x] Blast-radius grep: `auto_payout_loop`, weekly batch invocation, and existing payout claim/idempotency tests.
- [x] Money dry-run scenario: simulated Sunday lock outage followed by acquisition; stale sweep and weekly batch run only on the acquired iteration. Contention is covered on a weekday (hourly sweep) and Sunday (sweep plus batch). The tests assert the unchanged 3060-second TTL; existing mocked payout tests cover payout claims and Stripe idempotency contracts.
- [ ] Not verified against live Redis or Stripe; tests mock lock and transfer boundaries.
- [x] No feature flag needed; this is a backend worker safety gate.

## 10. Sign-off

- [x] Rollback and blast radius are stated.
- [x] Existing payout claims, settings, and idempotency remain unchanged.
- [x] Lock errors and contention skip all transfer paths; subsequent recovery is covered.
