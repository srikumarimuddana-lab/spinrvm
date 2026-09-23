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

When Redis lock acquisition failed, the orphaned-hold loop proceeded to reconcile anyway, allowing multiple replicas to scan and attempt releases without distributed ownership.

## 2. Root cause

The old best-effort helper could raise on Redis failure, and the loop caught the exception by setting `got_lock = True`.

## 3. Fix / remediation

Use the strict Redis helper under the existing `redis_set_nx` patch seam. Lock errors now log only the exception class, increment `spinr_loop_lock_unavailable_total{loop="orphaned_hold_reconciler"}`, and skip that pass.

## 4. Risk & impact on existing functionality

- Blast radius: isolated to the orphaned-hold reconciliation background loop.
- `orphaned_hold_reconciler_loop` is the only changed consumer. `reconcile_tick` retains its compare-and-swap claim on observed `updated_at` and open `auth_status`; card release keeps Stripe idempotency key `ride-cancelauth-{ride}-{pi}`.
- Before: lock failure authorized a scan; after: no scan runs without acquisition, with retry on the next interval. A Redis outage may delay releasing an affected card hold by one loop interval.
- The 0.85× interval lock TTL remains below the minimum jittered sleep; tests retain the cadence/expiry overlap defense. The DB claim and Stripe idempotency remain correctness protections if a lock expires during a slow tick.
- No ride-state or wallet-delta logic changed.

## 5. User-experience effect

No app UI or email copy changes. A rider's orphaned card hold may remain reserved until a later healthy Redis interval; normal reconciliation resumes after lock acquisition.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/orphaned_hold_reconciler.py` | Alias strict lock, fail closed, emit lock-unavailable metric, correct replay-contract docs | Prevent releases without Redis ownership |
| `backend/tests/test_orphaned_hold_reconciler_coverage.py` | Replace fail-open assertion; cover skip, recovery, metric, and redacted error | Pin Redis outage behavior |
| `docs/change-log/2026-09-23-orphaned-hold-lock.md` | Record impact and verification | Required runtime change record |

## 7. Before / after

```python
# Before
logger.error(f"orphaned_hold_reconciler: leader lock unavailable ({lock_err}), proceeding without it")
got_lock = True
```

```python
# After
logger.error("orphaned_hold_reconciler: leader lock unavailable (%s); skipping tick", type(lock_err).__name__)
_metric_inc("spinr_loop_lock_unavailable_total", {"loop": "orphaned_hold_reconciler"})
got_lock = False
```

## 8. Rollback plan

If the outage delay is unacceptable, revert the isolated loop change and redeploy. The lock itself changes no persisted data; existing DB claims and Stripe idempotency remain intact.

## 9. Verification performed

- [x] Automated: `/tmp/pr5725-venv/bin/python -m pytest backend/tests/test_orphaned_hold_reconciler.py backend/tests/test_orphaned_hold_reconciler_coverage.py backend/tests/test_orphaned_hold_reconciler_loop_coverage.py -q --no-cov` (38 passed).
- [ ] Manual repro in staging: not run; no production/staging changes were made.
- [x] Blast-radius grep: `orphaned_hold_reconciler_loop`, `reconcile_tick`, and hold release/idempotency tests.
- [x] Replay checks: existing CAS/Stripe key tests, lock-expiry-before-earliest-wake test, plus reacquisition on next wake.
- [ ] Not verified against live Redis or Stripe; tests patch those boundaries.
- [x] No feature flag needed; this is a backend loop safety gate.

## 10. Sign-off

- [x] Rollback and blast radius are stated.
- [x] DB claim and Stripe idempotency protections remain unchanged.
- [x] Redis outage skip and recovery are covered.
