# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | vikas@ngitservices.com (via Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | local worktree commit (not pushed/PR'd per task instructions) — see commit SHAs in task report |
| Related issue or gap ID | `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` finding #9 in the re-verified-baseline table ("ranked blocker #14" per task framing) |

## 1. Issue / gap identified

`backend/utils/safety_checkin_loop.py`'s per-ride "have we sent the safety
check-in push yet?" state was read (`redis_get`), checked, and only written
(`redis_set`) *after* the send — a classic TOCTOU window. Two overlapping
executions of `_tick()` (the loop runs on every backend replica, polling
every 30 s) could both read "not sent yet" for the same `ride_id` before
either one wrote the flag, and both would fire the FCM push, sending the
rider a duplicate "are you okay?" notification.

## 2. Root cause

The original code combined a read (`redis_get`), a conditional branch, an
external side effect (`send_push_notification`), and a subsequent write
(`redis_set`) as four separate steps with no mutual exclusion between them.
Nothing prevented a second concurrent caller — another replica, or this
replica's own next tick if the previous one was still mid-flight — from
observing the same pre-write state.

As re-scoped in the 2026-08-18 audit pass, the *escalation*-insert path
(`_escalate()`, which opens a `safety_incidents` row) was independently
confirmed already safe — it null-writes the `escalated` Redis key only
*after* a successful DB insert, and re-checks that key on every tick, so it
cannot double-fire an incident. This fix is scoped strictly to the earlier
"sent" flag race — the duplicate-notification risk only.

## 3. Fix / remediation

Replaced the read-then-write with a single atomic claim using
`redis_set_nx` (`SET key value NX EX ttl`) — the same primitive already
used throughout `backend/utils/` for leader-election and dedupe locks
(e.g. `utils/scheduled_rides.py`'s notify-dedupe key, `routes/rides/payments.py`'s
wallet re-drive lock, `utils/period1_distance_finalizer.py`'s and a dozen
other background loops' per-run leader lock). No new primitive was
introduced.

- `redis_set_nx(_sent_key(ride_id), now.isoformat(), ttl=4*3600)` is called
  first. Only the caller that gets `True` back proceeds to send the push —
  everyone else, by construction, cannot have won the same atomic claim.
- A caller that loses the claim (`False`) is expected, routine contention —
  not an error — and is logged at `debug`, not `error`/`warning`, per
  CLAUDE.md's error-surfacing convention (this is normal, not a symptom to
  hide, but also not something to make loud).
- If the FCM send itself fails *after* winning the claim, the claim key is
  deleted (`redis_delete`) so a later tick can retry rather than the rider
  permanently never receiving the check-in because the (unsent) flag stays
  claimed for its full 4 h TTL.
- The subsequent escalation-timing logic (which needs the `sent` timestamp)
  is unchanged — it now reads it via `redis_get` after a lost claim instead
  of before a won one, but the value and its meaning are identical.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one loop file.** Grepped every
  reader/writer of the `safety:checkin:sent:{ride_id}` key across
  `backend/`:
  - `utils/safety_checkin_loop.py` — the only reader and the only writer
    (before and after this fix).
  - `routes/rides/safety.py` (`safety_checkin_response`, the
    `POST /rides/{id}/safety-checkin` handler) writes the **`ok`** key
    (`safety:checkin:ok:{ride_id}`) only — a different key, untouched by
    this change.
  - `core/lifespan.py` only imports and spawns the `safety_checkin_loop()`
    coroutine itself; it does not touch the Redis keys.
  - No other file references `_sent_key`, `safety:checkin:sent:`, or reads/
    writes that key.
- **Replay-safety confirmed**: per CLAUDE.md's "Background task safety"
  requirement (the loop runs on every replica concurrently), this fix makes
  `_tick()`'s "sent" flag genuinely replay-safe with respect to concurrent
  executions — previously the flag *claimed* to be replay-safe via "Redis
  keys provide idempotency" but the underlying primitive (read-then-write)
  did not actually guarantee mutual exclusion. It now does, using the same
  atomic `SET NX` guarantee real Redis provides (and the in-process
  fallback dict mirrors for single-process dev/test, since `_local_get`+
  `_local_set` inside `redis_set_nx` execute with no intervening `await`).
- **Escalation logic is unchanged** — not touched, not re-scoped, per the
  audit's explicit re-scoping to duplicate-push risk only.
- **Ride state machine, wallet/payment paths**: not touched at all — this
  loop only reads `rides` rows (`status = in_progress`) and writes to
  `safety_incidents` (in `_escalate`, unchanged) and Redis. No money code,
  no ride-state transition.

## 5. User-experience effect

- **Rider-facing, mid-ride.** Riders on a trip ≥ 20 minutes long receive the
  automated "are you okay?" safety check-in push. Before this fix, a rider
  could — under replica-race conditions — receive that push twice for the
  same ride. After this fix, they receive it exactly once (barring the
  send-failure-then-retry path, which is pre-existing retry behavior, not
  new).
- No copy change, no new notification type, no change to the 20-minute
  trigger threshold or the 90-second escalation window.
- Not visible to drivers, corporate admins, or internal admins.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/safety_checkin_loop.py` | Replaced `redis_get`-check-then-`redis_set`-write of the `sent` flag with an atomic `redis_set_nx` claim; on FCM-send failure after winning the claim, release it via `redis_delete` so retry remains possible; lost-claim path now logs at `debug` and falls through to read the existing `sent` timestamp for escalation timing. Updated the module docstring to describe the new atomic-claim mechanism. | Close the TOCTOU race that could send a duplicate safety check-in push to the same rider. |
| `backend/tests/test_safety_checkin_loop.py` | Updated all send-path tests to patch/assert against `redis_set_nx` instead of `redis_set` for the claim; added `test_concurrent_claim_attempts_only_one_sends` (exercises the real, unmocked `redis_set_nx` in-process-fallback semantics under `asyncio.gather` to prove only one of two concurrent ticks sends), `test_tick_does_not_resend_if_claim_already_taken`, `test_tick_claim_lost_race_does_not_error_when_sent_key_missing`, and `test_tick_releases_claim_on_push_failure_so_retry_is_possible`. | Regression coverage for the fix, per CLAUDE.md's "every new state/behavior change needs a test." |

## 7. Before / after

```python
# Before (utils/safety_checkin_loop.py, ~lines 104-127)
sent_ts_str = await redis_get(_sent_key(ride_id))

if not sent_ts_str:
    # Haven't sent a check-in yet — send one now.
    rider_id = ride.get("rider_id")
    if rider_id:
        try:
            await send_push_notification(
                rider_id, "Safety check-in",
                "Just checking in — are you okay? Tap to confirm.",
                data={"type": "safety_checkin", "ride_id": ride_id},
            )
        except Exception:
            logger.error(f"[SAFETY_CHECKIN] FCM push failed ride_id={ride_id}", exc_info=True)
            continue

    # Record that we sent the push; TTL = 4 h ...
    await redis_set(_sent_key(ride_id), now.isoformat(), ttl=4 * 3600)
    logger.info(f"[SAFETY_CHECKIN] Check-in sent for ride {ride_id}")
    continue
```

```python
# After
claimed = await redis_set_nx(_sent_key(ride_id), now.isoformat(), ttl=4 * 3600)

if claimed:
    rider_id = ride.get("rider_id")
    if rider_id:
        try:
            await send_push_notification(
                rider_id, "Safety check-in",
                "Just checking in — are you okay? Tap to confirm.",
                data={"type": "safety_checkin", "ride_id": ride_id},
            )
        except Exception:
            logger.error(f"[SAFETY_CHECKIN] FCM push failed ride_id={ride_id}", exc_info=True)
            try:
                await redis_delete(_sent_key(ride_id))  # release claim so a later tick can retry
            except Exception:
                logger.error(f"[SAFETY_CHECKIN] Failed to release claim ... ride_id={ride_id}", exc_info=True)
            continue

    logger.info(f"[SAFETY_CHECKIN] Check-in sent for ride {ride_id}")
    continue

# Did not win the claim — expected contention, not a failure.
logger.debug(f"[SAFETY_CHECKIN] Check-in already claimed for ride {ride_id}; skipping duplicate send")
sent_ts_str = await redis_get(_sent_key(ride_id))
if not sent_ts_str:
    continue
```

## 8. Rollback plan

No feature flag or `app_settings` toggle exists for this loop's internal
locking strategy — it's a pure logic fix inside a background loop, not a
user-visible feature. Rollback is:

- `git revert` of the fix commit in this worktree/branch, then redeploy.
  This is safe because the change writes **no persistent/durable data** —
  the `sent`/`escalated`/`ok` Redis keys are ephemeral (4 h TTL) and this
  fix does not touch `safety_incidents`, `rides`, wallet, or any other
  durable table. A revert simply restores the prior (racy but functional)
  read-then-write behavior; no data-level remediation is needed because
  nothing here mutates Stripe charges, wallet deltas, or ride state.
- If a revert is needed mid-incident, no manual Redis key cleanup is
  required — the worst-case blast radius of reverting is reverting to the
  pre-existing (rare) duplicate-push behavior, not a functional regression.

## 9. Verification performed

- [x] Automated tests run — unit tests only, via `pytest`:
  `pytest tests/test_safety_checkin_loop.py tests/test_admin_safety_incidents.py tests/test_replay_safety_payment_loops.py --no-cov -q`
  → **65 passed**, 1 pre-existing unrelated deprecation warning
  (`httpx`/`starlette.testclient`), 0 failures.
  Ran via the pre-existing venv at `/tmp/spinr-venv/bin/pytest` (confirmed
  present; used as instructed rather than creating a new one).
- [ ] Manual repro steps followed in staging — **not performed** (no
  staging environment access in this session).
- [x] Blast-radius grep performed — see Section 4 above (searched for
  `safety:checkin:sent`, `_sent_key`, and `safety_checkin_loop` across
  `backend/`).
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — "Background
  task safety" (replay-safety across replicas — this is precisely what the
  fix restores), "Do not silently swallow errors" (lost-claim contention is
  logged at `debug`/expected, not swallowed as an error; push-send failure
  still logs at `error` as before).
- [ ] Feature-flagged — **not applicable**; this is an internal locking-
  mechanism fix inside an existing background loop with no new user-facing
  behavior, config surface, or opt-in/opt-out semantics to flag.
- **Real production build**: N/A — backend-only Python change, no
  `admin-dashboard`/`rider-app`/`driver-app` frontend build applies here.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no
  durable-data cleanup needed — see Section 8).
- [x] Blast radius is stated, not assumed (Section 4: isolated to this one
  file's `sent`-key claim; every other reader/writer of adjacent
  check-in keys named).
- [x] No silent behavior change to an already-shipped flow without the UX
  field filled in (Section 5: rider-facing effect stated — fixes a
  duplicate-push edge case, no new copy/threshold change).

## What was NOT verified

- Not tested against a real Redis instance (`REDIS_URL` unset in this test
  environment) — the concurrency test
  (`test_concurrent_claim_attempts_only_one_sends`) exercises the
  in-process fallback's `SET-NX`-equivalent logic (`_local_get`/`_local_set`
  inside `redis_set_nx`, which executes synchronously with no intervening
  `await`, so it is genuinely atomic with respect to `asyncio.gather`
  interleaving) rather than real Redis's `SET key value NX EX ttl`. The two
  code paths share the same `redis_set_nx` function and contract
  (`redis_client.py`'s own docstring: "returns True iff the caller acquired
  the lock"), and this exact primitive is already relied on in production
  by a dozen other loops, but a live-Redis two-process race was not
  independently reproduced in this session.
- No staging/manual QA pass was performed — this session had no staging
  access.
- No visual/UX regression tooling applies here (backend-only change, no UI
  surface).
