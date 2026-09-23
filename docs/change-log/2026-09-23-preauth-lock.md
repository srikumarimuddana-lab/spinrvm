# Change Impact & Risk: preauthorization capture lock

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | Backend |
| Domain (Sentry tag) | payments |
| PR / commit link | PR #5725 |
| Related issue or gap ID | PR 5725 implementation plan, Task 4 |

## 1. Issue / gap identified

If the preauthorization capture leader-lock command failed, the loop safely skipped money work but then recorded a healthy heartbeat. Persistent Redis outages were therefore invisible to `/health` and loop watchdogs.

## 2. Root cause

The strict Redis helper raises on outages, but the exception branch collapsed failure and ordinary `SET NX` contention into the same `got_lock = False` path. That path emitted a successful heartbeat, so liveness tracking could not distinguish Redis outage from another replica holding the lock.

## 3. Fix / remediation

The loop retains the strict Redis lock primitive. An unavailable lock logs only the exception class, increments `spinr_loop_lock_unavailable_total{loop="preauth_capture"}`, records a loop dependency failure, skips heartbeat and money work, and tries again next interval. Ordinary contention still records heartbeat; the next successful heartbeat clears the failure so health recovers immediately.

## 4. Risk & impact on existing functionality

- Blast radius: isolated to the preauthorization capture background loop.
- `preauth_capture_loop` is the only consumer changed. `_capture_tick` still uses the existing conditional DB claim (`payment_status=pending` and unchanged `auth_status`) before calling settlement; `settle_card` keeps its Stripe idempotency behavior.
- Before this health fix: lock errors and ordinary contention both skipped this pass and recorded a healthy heartbeat. After: contention remains healthy, Redis errors immediately degrade runtime loop health, and later heartbeat clears the degraded status.
- If Redis is unavailable, eligible captures wait at most for the next loop interval; DB claim and Stripe protection remain in place for overlapping/expired locks.
- No ride state or wallet delta logic changed.

## 5. User-experience effect

No direct app copy or UI change. A driver may receive an eligible fare capture later if Redis is down during a tick; normal settlement and retry flows remain unchanged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/preauth_capture.py` | Report strict-lock exceptions to loop health; keep the existing fail-closed gate and heartbeat on legitimate contention | Surface outage without authorizing a money tick |
| `backend/utils/loop_monitor.py` | Add a per-loop dependency-failure state that clears on heartbeat and marks health unhealthy | Make dependency outage visible immediately, including before first heartbeat |
| `backend/tests/test_preauth_capture_coverage.py` | Assert no capture/heartbeat on lock exception, healthy contention, recovery, metric, redacted log, and cancellation | Pin outage behavior while preserving loop liveness |
| `backend/tests/test_loop_monitor_dependency_failure.py` | Assert dependency failure is unhealthy and heartbeat restores health | Pin runtime health contract |
| `docs/change-log/2026-09-23-preauth-lock.md` | Record impact and verification | Required runtime change record |

## 7. Before / after

```python
# Before
_record_heartbeat(_LOOP_NAME)  # lock error and contention look identical
```

```python
# After
logger.error("preauth_capture: leader lock unavailable (%s); skipping tick", type(lock_err).__name__)
_metric_inc("spinr_loop_lock_unavailable_total", {"loop": "preauth_capture"})
_record_dependency_failure(_LOOP_NAME)
got_lock = False
# Skip heartbeat only on exception; ordinary contention still heartbeats.
```

## 8. Rollback plan

Revert this isolated loop change and redeploy if a Redis outage causes unacceptable capture delay. No live records or payment state are changed by the lock itself; existing payment retry/claim behavior remains authoritative.

## 9. Verification performed

- [x] Automated: `/tmp/pr5725-venv/bin/python -m pytest backend/tests/test_preauth_capture_coverage.py -k 'loop_lock_not_acquired or survives_a_redis_lock_error or recovers_after_lock_error' -o addopts='' -q` (3 passed); loop-monitor failure/recovery test (1 passed).
- [ ] Manual repro in staging: not run; no production/staging changes were made.
- [x] Blast-radius grep: `preauth_capture_loop`, `_capture_tick`, and settlement/idempotency coverage in both preauth test files.
- [x] State/money scenario: lock unavailable skips `_capture_tick`; contention records a healthy heartbeat; next successful acquisition permits the existing pending/auth-status DB claim and settlement path and clears degraded health.
- [ ] Not verified against live Redis or Stripe; tests patch the lock and settlement dependencies.
- [x] No feature flag needed; this is a backend loop safety gate.

## 10. Sign-off

- [x] Rollback and blast radius are stated.
- [x] Existing DB claim and settlement protections remain unchanged.
- [x] Cancellation and outage recovery behavior are covered.
