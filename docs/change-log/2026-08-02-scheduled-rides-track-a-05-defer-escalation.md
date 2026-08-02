# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Scheduled Rides gap review — Finding #03 |

## 1. Issue / gap identified

If a rider still has an active ride when their scheduled ride's dispatch time
arrives, the scheduled ride is correctly deferred (can't violate
`rides_one_active_per_rider`) — but it retries every ~60s tick indefinitely,
with no cap and no escalation. If the rider's other ride is itself stuck, the
scheduled ride can wait forever with only a one-time rider push and a
per-tick warning log as the only trace.

## 2. Root cause

The deferral branch in `_dispatch_scheduled_ride` (`backend/utils/scheduled_rides.py`)
was written to handle the immediate conflict safely but was never given a
"this has been going on too long" branch.

## 3. Fix / remediation

Added `_track_defer_and_maybe_escalate()`: counts consecutive deferrals per
ride via a Redis counter (`redis_incr` + `redis_expire`, 1h TTL — self-expiring,
no cleanup needed once the ride eventually dispatches or gets cancelled).
Below `_SCHEDULE_DEFER_ESCALATE_AFTER` (20) deferrals, behavior is unchanged
(routine "your ride is waiting" push, deduped). At/past that threshold:
- Error-level log + a new metric (`spinr_dispatch_scheduled_defer_exhausted_total`),
  firing once (Redis `INCR` is atomic, so exactly one caller observes the
  exact threshold value even with multiple replicas polling).
- An admin broadcast (`scheduled_ride_stuck`) so ops has live visibility.
- A distinct, more actionable rider push ("Still working on your scheduled
  ride... contact support if you'd like to cancel or rebook"), on its own
  dedupe key so it can still fire even though the routine notice already did,
  and re-fires roughly hourly for as long as the ride stays stuck.

Deliberately **does not auto-cancel** the ride — the underlying conflict can
still resolve on its own (the rider's other trip ending), and forcing a
cancellation is a bigger behavior change that would need its own product
decision (money/cancellation-fee implications), not something to fold into
an observability fix.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the deferral branch of `_dispatch_scheduled_ride`.**
  The happy-path claim/dispatch, the "claim lost to another replica" path,
  and `check_scheduled_rides()`'s query are all untouched (existing tests
  `test_dispatch_claims_scheduled_to_searching_then_matches` and
  `test_dispatch_aborts_when_claim_lost` still pass unmodified).
- Grepped for other readers of `spinr:sched_delay_notified:*` and
  `spinr:sched_defer_count:*` Redis keys — none found; these are private to
  this module.
- The escalation counter is approximate under multi-replica deployment
  (documented in-code): with N replicas each polling independently, the
  count can climb faster than real elapsed minutes, so escalation may fire
  somewhat earlier than a literal 20 minutes. This is a monitoring signal,
  not a billing or state-machine decision, so early-by-a-few-minutes is an
  acceptable trade-off versus the complexity of exact wall-clock tracking.
- No interaction with money, Stripe holds, or the ride state machine —
  the ride stays in `scheduled` throughout, exactly as before.

## 5. User-experience effect

- **Rider**: previously got exactly one "your ride is waiting" push, ever,
  for a given scheduled ride's conflict. Now also gets a second, distinct,
  more actionable push once the wait crosses roughly 20 minutes, repeating
  roughly hourly while still stuck. This is strictly additive — no existing
  notification is removed or changed.
- **Internal admin**: new `scheduled_ride_stuck` WS broadcast to the admin
  channel. No existing admin dashboard screen currently renders this event
  type — it's a new signal on the wire, not yet a new UI, so there's nothing
  for an admin to click on it it yet. Flagging this explicitly: the value
  today is in server logs/metrics; a dashboard surface for `scheduled_ride_stuck`
  would be a natural follow-up but is out of scope for this fix.
- **Driver / corporate admin**: no effect.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/scheduled_rides.py` | New `_track_defer_and_maybe_escalate()`; `_notify_schedule_delayed()` gained an `escalated` parameter; wired into the conflict-deferral branch | Make a previously-silent unbounded retry visible without changing its safe, non-destructive behavior |
| `backend/tests/test_scheduled_dispatch_cr.py` | Three new tests: below-threshold (routine only), at-threshold (escalates once), past-threshold (doesn't re-escalate every tick) | Pin the escalate-once behavior, which is the part most likely to regress silently |

## 7. Before / after

```python
# Before
if "rides_one_active_per_rider" in msg or ...:
    logger.warning(f"scheduled dispatch deferred: ... ride {ride_id} stays 'scheduled' for retry")
    await _notify_schedule_delayed(ride_id, rider_id, ride)
    return
```

```python
# After
if "rides_one_active_per_rider" in msg or ...:
    logger.warning(f"scheduled dispatch deferred: ... ride {ride_id} stays 'scheduled' for retry")
    await _track_defer_and_maybe_escalate(ride_id, rider_id, ride)  # counts + escalates past 20 deferrals
    return
```

## 8. Rollback plan

- Plain code change, no migration, no data written beyond a self-expiring
  Redis counter (1h TTL — a `git revert` needs no additional cleanup).
- If the escalation threshold or copy needs tuning without a full revert,
  `_SCHEDULE_DEFER_ESCALATE_AFTER` is a single named constant.

## 9. Verification performed

- [x] Automated tests: `backend/tests/test_scheduled_dispatch_cr.py` (7
      passed, including the 4 pre-existing tests unmodified) via the
      session's venv (`/tmp/spinr_venv`).
- [x] `ruff check` on both modified files — clean.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Blast-radius grep performed (see §4).
- [x] Reviewed against CLAUDE.md's background-loop replay-safety and
      "never silently swallow" conventions — this change is a direct
      application of the latter.
- [ ] Feature-flagged — not flagged. This is additive observability with a
      new (currently unconsumed) admin broadcast type and a new rider push
      variant; it doesn't change the ride's actual dispatch outcome, so a
      flag was judged unnecessary. Noting the reasoning rather than skipping
      the checklist item silently.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — isolated to one deferral branch
- [x] No silent behavior change to dispatch outcomes — the ride's fate
      (still retries, never auto-cancelled) is unchanged; only visibility
      is added

## What was NOT verified

Not tested under an actual multi-replica deployment, so the "count climbs
faster than real time with N replicas" behavior is reasoned about, not
measured. No Sentry/dashboard surface was built for the new
`scheduled_ride_stuck` admin broadcast — it exists on the wire only, per §5.
