# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | (this branch's PR) |
| Related issue or gap ID | ACTION_ITEMS.md A40 ranked blocker #9/#14 (`docs/audit/2026-08-18-full-fleet-whole-app-audit.md`) |

## 1. Issue / gap identified

`safety_checkin_loop._tick()`'s push-send path was check-then-act: read
`safety:checkin:sent:{ride_id}` from Redis, then (if empty) call
`send_push_notification` — a network round-trip — and only afterward write
the `sent` key. Two backend replicas polling the same ride in the same 30s
tick could both read "not sent" before either wrote the claim, and both
fire the FCM "are you okay?" safety check-in push for the same ride.

## 2. Root cause

The read-check-act sequence had no atomicity across the network call in the
middle. This repo already has a purpose-built primitive for exactly this
shape (`redis_set_nx`, `SET key value NX EX ttl`, `backend/utils/redis_client.py`)
used elsewhere for leader-election/dedupe locks (e.g.
`utils/referral_payout.py`), but this loop never adopted it.

## 3. Fix / remediation

Claim `safety:checkin:sent:{ride_id}` atomically via `redis_set_nx` *before*
sending the push. If the claim fails (`False`), another replica already
won it this tick — skip silently, same outcome as before for the
already-sent case. If the claim succeeds but the push itself then fails,
the claim is released (`redis_delete`) so the next tick still retries the
send — preserving the loop's pre-existing retry behavior for a genuinely
failed send, not just a duplicate.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `safety_checkin_loop()` has exactly one
  caller — `core/lifespan.py`'s `_spawn("safety_checkin (30s)", ...)`. No
  other code calls `_tick()` or reads/writes the `safety:checkin:sent:*`
  key family except `routes/rides/safety.py` (writes the *separate*
  `safety:checkin:ok:*` key on rider confirmation — untouched by this fix).
- **Escalation path unaffected:** the audit that surfaced this finding
  explicitly re-scoped it as a duplicate-*push* risk only — the escalation
  insert (`_escalate`, `safety_incidents` row) was separately confirmed
  already-safe (its own DB-insert-then-Redis-set ordering already prevents
  a duplicate escalation) and is not touched by this change.
- **Redis-down behavior unchanged:** `redis_set_nx`, like the `redis_get`/
  `redis_set` calls already in this file, raises on a real Redis error
  rather than silently falling through — an uncaught exception during
  `_tick()` was already caught by `safety_checkin_loop()`'s outer
  `try/except`, logged, and the loop continues to the next 30s tick. This
  fix does not change that behavior, only which primitive claims the key.
- **Could this regress a currently-working flow?** No — a rider who was
  never at risk of a duplicate push (the common case, since races require
  two replicas hitting the identical 30s window for the identical ride)
  sees byte-for-byte identical behavior. Only the actual race window is
  closed.

## 5. User-experience effect

Rider-facing: previously-possible duplicate "are you okay?" safety
check-in push under a race is now impossible. Not visible mid-session in
any other way — the fix only prevents an occasional extra push, it doesn't
change the check-in flow's timing or UI.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/safety_checkin_loop.py` | Push-send path now claims `safety:checkin:sent:{ride_id}` via `redis_set_nx` before sending, with `redis_delete` release on a failed send | Close the check-then-act race; same retry semantics for genuine failures |
| `backend/tests/test_safety_checkin_loop.py` | Updated 5 existing tests to patch `redis_set_nx` instead of `redis_set` on the send path; added 2 new regression tests (claim-lost-to-another-replica, claim-released-on-failed-push) | Prove the fix and pin the new claim/release contract |

## 7. Before / after

```python
# Before
sent_ts_str = await redis_get(_sent_key(ride_id))
if not sent_ts_str:
    rider_id = ride.get("rider_id")
    if rider_id:
        try:
            await send_push_notification(rider_id, ...)
        except Exception:
            logger.error(...)
            continue
    await redis_set(_sent_key(ride_id), now.isoformat(), ttl=4 * 3600)
    logger.info(...)
    continue
```

```python
# After
sent_ts_str = await redis_get(_sent_key(ride_id))
if not sent_ts_str:
    claimed = await redis_set_nx(_sent_key(ride_id), now.isoformat(), ttl=4 * 3600)
    if not claimed:
        continue  # another replica already claimed this check-in
    rider_id = ride.get("rider_id")
    if rider_id:
        try:
            await send_push_notification(rider_id, ...)
        except Exception:
            logger.error(...)
            try:
                await redis_delete(_sent_key(ride_id))  # release so next tick retries
            except Exception:
                logger.error(...)
            continue
    logger.info(...)
    continue
```

## 8. Rollback plan

`git revert` is safe and sufficient — no schema, no data mutation. Worst
case on revert: the pre-existing (already-shipped, already-accepted-risk)
duplicate-push race returns; no new risk introduced by rolling back.

- `spinr-safety-sos-reviewer` agent pass: race-closure logic confirmed
  correct (claim strictly precedes the network call; release-on-failure
  can't fall through into the escalation branch in the same iteration;
  escalation path genuinely untouched; PII/copy surfaces clean). One
  WARNING raised and fixed before commit: the dual-import fallback branch
  (`except ImportError: from utils.redis_client import ...`) hadn't picked
  up the two new imports (`redis_delete`, `redis_set_nx`) — would have
  `NameError`'d if that branch were ever exercised. Fixed to match the
  primary branch exactly, per CLAUDE.md's dual-import convention.

## 9. Verification performed

- `pytest tests/test_safety_checkin_loop.py -q --no-cov` → 20 passed (18 existing + 2 new).
- `ruff check backend/utils/safety_checkin_loop.py backend/tests/test_safety_checkin_loop.py` → clean.
- `pytest tests/test_loguru_call_conventions.py -q --no-cov` → 5 passed (this module uses stdlib `logging`, not loguru — confirmed unaffected).
- `pytest tests/test_dual_import_parity.py tests/test_safety_checkin_loop.py -q --no-cov` → 23 passed, after fixing the fallback-import gap the reviewer found.
- Grepped every caller of `safety_checkin_loop`/`_tick` to confirm isolation (1 caller, `core/lifespan.py`).
- Full backend unit-marked suite reverified after the change: `pytest -q --no-cov -m unit` → 2874 passed, 1 skipped (same baseline as before this fix — no regression).

## 10. What was NOT verified

- No live multi-replica test — the actual race (two replicas hitting the
  same ride in the same 30s window) is inherently timing-dependent and not
  reproducible in this repo's mocked-Redis unit-test harness; the fix is
  verified by contract (claim-first-then-act, atomic `SET NX`) rather than
  by reproducing the race under load.
- No live Redis instance exercised — `redis_set_nx`'s own atomicity
  guarantee is Redis's `SET NX` semantics, already covered by that
  function's own existing test suite, not re-verified here.
