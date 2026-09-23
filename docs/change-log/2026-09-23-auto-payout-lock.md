# Change Impact & Risk: weekly automatic payout lock

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | Backend / driver payouts |
| Domain (Sentry tag) | payments |
| PR / commit link | PR #5725 |
| Related issue or gap ID | PR 5725 implementation plan, Task 6 |

## 1. Issue / gap identified

The Sunday payout loop treated Redis lock exceptions as successful acquisition and ran the weekly batch anyway, allowing simultaneous payout workers during Redis outages.

## 2. Root cause

The exception handler explicitly set `got_lock = True` so the batch proceeded without distributed ownership.

## 3. Fix / remediation

The Sunday batch now uses the strict Redis lock primitive. A lock error logs only the exception class, increments `spinr_loop_lock_unavailable_total{loop="auto_payout"}`, and skips the weekly batch for that iteration.

## 4. Risk & impact on existing functionality

- Blast radius: isolated to the Sunday `run_weekly_auto_payout` gate in `auto_payout_loop`.
- Existing hourly stale-reserved and stale-running-batch maintenance continues unchanged. `auto_payout_batches.week_key` claims, payout reservation rows, attempt-scoped Stripe idempotency keys, and finalization rules are unchanged.
- Before: lock failure invoked `run_weekly_auto_payout`; after: it skips that invocation. Once the next Sunday-window iteration acquires Redis, the existing batch claim/resume and idempotency rules continue the run.
- If Redis is down during the Sunday batch window, automatic driver payouts may be delayed until a healthy hourly iteration. No payout amount or eligibility rules changed.
- No ride state or wallet-delta logic changed.

## 5. User-experience effect

Drivers may receive a scheduled weekly payout later if Redis is unavailable during the batch window. No app UI or notification copy changed; the regular batch continues after healthy lock acquisition.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/auto_payout.py` | Alias strict lock, fail closed, sanitize lock diagnostic, emit metric | Prevent payout batch execution without Redis ownership |
| `backend/tests/test_auto_payout.py` | Drive a Sunday-window lock failure then recovery; assert single batch execution, metric, and redaction | Prove no batch on error and resume after acquisition |
| `docs/change-log/2026-09-23-auto-payout-lock.md` | Record impact and verification | Required runtime change record |

## 7. Before / after

```python
# Before
logger.error("[AUTO-PAYOUT] leader lock unavailable (%s), proceeding", lock_err)
got_lock = True
```

```python
# After
logger.error("[AUTO-PAYOUT] leader lock unavailable (%s), skipping Sunday batch", type(lock_err).__name__)
_metric_inc("spinr_loop_lock_unavailable_total", {"loop": "auto_payout"})
got_lock = False
```

## 8. Rollback plan

Revert the isolated loop-gate change and redeploy if payout timing becomes unacceptable. The lock does not change payout records; existing database claims and Stripe idempotency remain intact.

## 9. Verification performed

- [x] Automated: `/tmp/pr5725-venv/bin/python -m pytest backend/tests/test_auto_payout.py -q --no-cov` (70 passed).
- [ ] Manual repro in staging: not run; no production/staging changes were made.
- [x] Blast-radius grep: `auto_payout_loop`, weekly batch invocation, and existing payout claim/idempotency tests.
- [x] Money dry-run scenario: simulated Sunday window, lock outage then successful acquisition; only the healthy iteration invokes the batch. Existing mocked payout suite exercises payout claims and transfer idempotency contracts.
- [ ] Not verified against live Redis or Stripe; tests mock lock and transfer boundaries.
- [x] No feature flag needed; this is a backend worker safety gate.

## 10. Sign-off

- [x] Rollback and blast radius are stated.
- [x] Existing payout claims, settings, and idempotency remain unchanged.
- [x] Lock error skips and subsequent recovery are covered.
