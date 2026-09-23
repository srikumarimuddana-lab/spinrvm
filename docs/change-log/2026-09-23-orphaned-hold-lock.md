# Change Impact & Risk: orphaned hold reconciliation lock

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | Backend |
| Domain (Sentry tag) | payments |
| PR / commit link | PR #5725 |
| Related issue or gap ID | PR 5725 implementation plan, Task 5 |

## 1. Issue / gap identified

When Redis lock acquisition failed, the orphaned-hold loop safely skipped reconciliation but then recorded a healthy heartbeat. Persistent Redis outages were therefore invisible to `/health` and loop watchdogs.

## 2. Root cause

The exception branch and ordinary `SET NX` contention both set `got_lock = False`, then shared a branch that emitted a successful heartbeat. Runtime health could not distinguish Redis outage from another replica holding the lock.

## 3. Fix / remediation

Keep the strict Redis helper under the existing `redis_set_nx` patch seam. Lock errors log only the exception class, increment `spinr_loop_lock_unavailable_total{loop="orphaned_hold_reconciler"}`, record a loop dependency failure, skip heartbeat and reconciliation, then retry next interval. Ordinary contention remains healthy; a later heartbeat clears the failure.

## 4. Risk & impact on existing functionality

- Blast radius: isolated to the orphaned-hold reconciliation background loop.
- `orphaned_hold_reconciler_loop` is the only changed consumer. `reconcile_tick` retains its compare-and-swap claim on observed `updated_at` and open `auth_status`; card release keeps Stripe idempotency key `ride-cancelauth-{ride}-{pi}`.
- Before this health fix: lock errors and ordinary contention both skipped reconciliation and recorded a healthy heartbeat. After: contention remains healthy, Redis errors immediately degrade runtime loop health, and later heartbeat clears the degraded status. No scan runs without acquisition, so a Redis outage may delay releasing an affected card hold by one loop interval.
- The 0.85× interval lock TTL remains below the minimum jittered sleep; tests retain the cadence/expiry overlap defense. The DB claim and Stripe idempotency remain correctness protections if a lock expires during a slow tick.
- No ride-state or wallet-delta logic changed.

## 5. User-experience effect

No app UI or email copy changes. A rider's orphaned card hold may remain reserved until a later healthy Redis interval; normal reconciliation resumes after lock acquisition.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/orphaned_hold_reconciler.py` | Report strict-lock exceptions to loop health; preserve fail-closed reconciliation gate and heartbeat on legitimate contention | Surface outage without authorizing a hold-release scan |
| `backend/utils/loop_monitor.py` | Add a per-loop dependency-failure state that clears on heartbeat and marks health unhealthy | Make dependency outage visible immediately, including before first heartbeat |
| `backend/tests/test_orphaned_hold_reconciler_coverage.py` | Assert no reconcile/heartbeat on lock exception, healthy contention, recovery, metric, and redacted error | Pin outage behavior |
| `backend/tests/test_loop_monitor_dependency_failure.py` | Assert dependency failure is unhealthy and heartbeat restores health | Pin runtime health contract |
| `docs/change-log/2026-09-23-orphaned-hold-lock.md` | Record impact and verification | Required runtime change record |

## 7. Before / after

```python
# Before
_record_heartbeat(_LOOP_NAME)  # lock error and contention look identical
```

```python
# After
logger.error("orphaned_hold_reconciler: leader lock unavailable (%s); skipping tick", type(lock_err).__name__)
_metric_inc("spinr_loop_lock_unavailable_total", {"loop": "orphaned_hold_reconciler"})
_record_dependency_failure(_LOOP_NAME)
got_lock = False
# Skip heartbeat only on exception; ordinary contention still heartbeats.
```

## 8. Rollback plan

If the outage delay is unacceptable, revert the isolated loop change and redeploy. The lock itself changes no persisted data; existing DB claims and Stripe idempotency remain intact.

## 9. Verification performed

- [x] Automated: `/tmp/pr5725-venv/bin/python -m pytest backend/tests/test_orphaned_hold_reconciler_coverage.py -k 'initial_stagger_sleep_then_skips_when_lock_not_acquired or survives_a_redis_lock_error or recovers_after_lock_error' -o addopts='' -q` (3 passed); loop-monitor failure/recovery test (1 passed).
- [ ] Manual repro in staging: not run; no production/staging changes were made.
- [x] Blast-radius grep: `orphaned_hold_reconciler_loop`, `reconcile_tick`, and hold release/idempotency tests.
- [x] Replay checks: existing CAS/Stripe key tests, lock-expiry-before-earliest-wake test, plus reacquisition on next wake; Redis errors still skip reconciliation and contention still reports healthy.
- [ ] Not verified against live Redis or Stripe; tests patch those boundaries.
- [x] No feature flag needed; this is a backend loop safety gate.

## 10. Sign-off

- [x] Rollback and blast radius are stated.
- [x] DB claim and Stripe idempotency protections remain unchanged.
- [x] Redis outage skip and recovery are covered.
