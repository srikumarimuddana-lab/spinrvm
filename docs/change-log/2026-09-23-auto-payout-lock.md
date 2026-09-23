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

The auto-payout loop already skips transfer-producing work unless its strict Redis lock is acquired, but it emitted a healthy heartbeat after a lock exception. Persistent Redis outages were therefore invisible to `/health` and loop watchdogs. The hourly stale-running-batch finalizer remains outside the lock because it only updates batch status.

## 2. Root cause

The lock exception was converted to `got_lock = False`, the same branch as ordinary contention, then the common loop tail recorded a heartbeat. This erased any signal that the Redis dependency had failed.

## 3. Fix / remediation

On lock error, the loop logs only the exception class, increments `spinr_loop_lock_unavailable_total{loop="auto_payout"}`, marks the dependency failure in loop health, and skips its common heartbeat so the failure persists. Ordinary contention still heartbeats; a later successful lock acquisition clears the failure. The strict 3060-second lease, existing transfer gates, stale-running-batch finalization, and schedule remain unchanged.

## 4. Risk & impact on existing functionality

- Blast radius: `auto_payout_loop`'s health status, plus the existing transfer-producing paths `sweep_stale_reserved` and Sunday `run_weekly_auto_payout`. `/health` and worker health consume the shared `loop_monitor` contract; other loop consumers retain existing behavior unless they report a dependency failure.
- Stale-running-batch finalization, `auto_payout_batches.week_key` claims, payout reservation rows, attempt-scoped Stripe idempotency keys, and finalization rules remain unchanged.
- Before this health fix: lock exceptions and ordinary contention both skipped transfer work and emitted a healthy heartbeat. After: contention remains healthy, Redis errors immediately degrade runtime loop health, and later heartbeat clears the degraded status. After a healthy acquisition, the stale sweep still runs hourly and the weekly batch still runs in its existing Sunday window.
- During a Redis outage, stranded-reservation retries may wait for the next healthy hourly iteration; scheduled weekly payouts may be delayed while the Sunday window is unhealthy. No payout amount or eligibility rules changed.
- No ride state or wallet-delta logic changed.

## 5. User-experience effect

Drivers may receive a stale reserved-payout retry or scheduled weekly payout later if Redis is unavailable or another replica owns the hourly lock. No app UI or notification copy changed; the hourly sweep resumes with a healthy lock, and the regular weekly batch retains its Sunday schedule.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/auto_payout.py` | Report strict-lock exceptions to loop health and skip the iteration heartbeat; preserve existing acquired-lock transfer gates | Surface Redis outage while keeping financial decisions unchanged |
| `backend/utils/loop_monitor.py` | Add a per-loop dependency-failure state that clears on heartbeat and marks health unhealthy | Make dependency outage visible immediately, including before first heartbeat |
| `backend/tests/test_auto_payout.py` | Assert lock error marks failure and skips heartbeat, contention remains healthy on weekdays and Sundays, and recovery heartbeats | Pin outage/contention/recovery without changing payout schedule |
| `backend/tests/test_loop_monitor_dependency_failure.py` | Assert dependency failure is unhealthy and heartbeat restores health | Pin runtime health contract |
| `docs/change-log/2026-09-23-auto-payout-lock.md` | Record impact and verification | Required runtime change record |

## 7. Before / after

```python
# Before
except Exception as lock_err:
    got_lock = False  # indistinguishable from ordinary contention
```

```python
# After
except Exception as lock_err:
    _record_dependency_failure("auto_payout (1h, Sundays)")
    lock_failed = True
# ... existing payout work remains gated by got_lock
if not lock_failed:
    _record_heartbeat("auto_payout (1h, Sundays)")
```

## 8. Rollback plan

Revert the isolated health-reporting change and redeploy if its health status or alert behavior is incorrect. This change does not alter payout records; existing database claims and Stripe idempotency remain intact.

## 9. Verification performed

- [x] Automated: `/tmp/pr5725-venv/bin/python -m pytest backend/tests/test_auto_payout.py -k 'sunday_batch_skips_lock_error or contended_hourly_lock' -o addopts='' -q` (3 passed); loop-monitor failure/recovery test (1 passed).
- [ ] Manual repro in staging: not run; no production/staging changes were made.
- [x] Blast-radius grep: `auto_payout_loop`, weekly batch invocation, and existing payout claim/idempotency tests.
- [x] Money dry-run scenario: simulated Sunday lock outage followed by acquisition; stale sweep and weekly batch run only on the acquired iteration. Contention is covered on a weekday (hourly sweep) and Sunday (sweep plus batch). The tests assert the unchanged 3060-second TTL; existing mocked payout tests cover payout claims and Stripe idempotency contracts.
- [ ] Not verified against live Redis or Stripe; tests mock lock and transfer boundaries.
- [x] No feature flag needed; this is a backend worker safety gate.

## 10. Sign-off

- [x] Rollback and blast radius are stated.
- [x] Existing payout claims, settings, and idempotency remain unchanged.
- [x] Lock errors and contention skip all transfer paths; subsequent recovery is covered.
