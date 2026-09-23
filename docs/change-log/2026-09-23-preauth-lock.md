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

If the preauthorization capture leader-lock command failed, the loop treated the exception as permission to process the tick, potentially letting every replica attempt money movement.

## 2. Root cause

The legacy best-effort Redis helper could raise on Redis outages, and the exception handler explicitly set `got_lock = True` to keep processing without distributed ownership.

## 3. Fix / remediation

The loop now uses the strict Redis lock primitive. An unavailable lock logs only the exception class, increments `spinr_loop_lock_unavailable_total{loop="preauth_capture"}`, skips that tick, and tries again next interval.

## 4. Risk & impact on existing functionality

- Blast radius: isolated to the preauthorization capture background loop.
- `preauth_capture_loop` is the only consumer changed. `_capture_tick` still uses the existing conditional DB claim (`payment_status=pending` and unchanged `auth_status`) before calling settlement; `settle_card` keeps its Stripe idempotency behavior.
- Before: a lock error set `got_lock = True`, so each replica could invoke the tick. After: lock error skips this pass and later lock acquisition resumes capture.
- If Redis is unavailable, eligible captures wait at most for the next loop interval; DB claim and Stripe protection remain in place for overlapping/expired locks.
- No ride state or wallet delta logic changed.

## 5. User-experience effect

No direct app copy or UI change. A driver may receive an eligible fare capture later if Redis is down during a tick; normal settlement and retry flows remain unchanged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/preauth_capture.py` | Alias strict lock under the existing `redis_set_nx` seam; fail closed and emit metric | Prevent ticks without Redis ownership |
| `backend/tests/test_preauth_capture_coverage.py` | Replace fail-open expectation; assert skip, recovery, metric, redacted log, and cancellation | Pin outage behavior while preserving loop liveness |
| `docs/change-log/2026-09-23-preauth-lock.md` | Record impact and verification | Required runtime change record |

## 7. Before / after

```python
# Before
logger.error(f"preauth_capture: leader lock unavailable ({lock_err}), proceeding without it")
got_lock = True
```

```python
# After
logger.error("preauth_capture: leader lock unavailable (%s); skipping tick", type(lock_err).__name__)
_metric_inc("spinr_loop_lock_unavailable_total", {"loop": "preauth_capture"})
got_lock = False
```

## 8. Rollback plan

Revert this isolated loop change and redeploy if a Redis outage causes unacceptable capture delay. No live records or payment state are changed by the lock itself; existing payment retry/claim behavior remains authoritative.

## 9. Verification performed

- [x] Automated: `/tmp/pr5725-venv/bin/python -m pytest backend/tests/test_preauth_capture_coverage.py backend/tests/test_preauth_capture.py -q --no-cov` (19 passed).
- [ ] Manual repro in staging: not run; no production/staging changes were made.
- [x] Blast-radius grep: `preauth_capture_loop`, `_capture_tick`, and settlement/idempotency coverage in both preauth test files.
- [x] State/money scenario: lock unavailable skips `_capture_tick`; next successful acquisition permits the existing pending/auth-status DB claim and settlement path.
- [ ] Not verified against live Redis or Stripe; tests patch the lock and settlement dependencies.
- [x] No feature flag needed; this is a backend loop safety gate.

## 10. Sign-off

- [x] Rollback and blast radius are stated.
- [x] Existing DB claim and settlement protections remain unchanged.
- [x] Cancellation and outage recovery behavior are covered.
