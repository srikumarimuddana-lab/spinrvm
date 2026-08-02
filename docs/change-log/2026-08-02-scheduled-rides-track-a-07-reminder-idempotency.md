# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Scheduled Rides gap review — Finding #14 |

## 1. Issue / gap identified

`_send_reminder`'s push send and its `reminder_sent` DB flag write shared one
try/except with no idempotency guard between them. If the push succeeded but
the following DB write failed, `reminder_sent` stayed `False` — so the next
tick would send the reminder again (a duplicate push). If the push itself
failed, the flag correctly stayed unset and the next tick retried it (this
part was already correct and must not regress).

## 2. Root cause

Unlike `_notify_schedule_delayed` a few lines up in the same file, which
already uses a Redis NX dedupe key, `_send_reminder` had no equivalent guard
— the `reminder_sent` DB column was the only idempotency mechanism, and it's
written *after* the send, so a failure between the two leaves no record that
the push already went out.

## 3. Fix / remediation

Added a Redis NX claim (`spinr:sched_reminder_pushed:{ride_id}`, 1h TTL)
around the push send specifically, decoupled from the DB flag:
- Claim taken before attempting the send.
- If the send fails, the claim is explicitly released (`redis_delete`) before
  re-raising, so the next tick retries the send — preserving existing,
  correct behavior for push failures.
- If the send succeeds but the DB write then fails, the claim is left in
  place. The next tick's claim attempt returns `False` (already claimed),
  so the push is skipped while the DB write is still retried — this is the
  actual bug fix.
- Redis-unavailable falls through and sends anyway (worst case: one
  duplicate push), matching the existing risk tolerance already documented
  for `_notify_schedule_delayed`'s identical fallback.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `_send_reminder`.** Grepped for other callers
  — only `check_scheduled_rides()`'s reminder branch calls it; no other
  module references `_send_reminder` or the new dedupe key prefix.
- The existing `reminder_sent` DB column and its role in
  `check_scheduled_rides()`'s "already reminded" filter are unchanged — this
  fix adds a second, faster-expiring guard in front of it, it doesn't replace
  it.
- No interaction with money, dispatch claiming, or the ride state machine.

## 5. User-experience effect

Rider-facing: fixes a real (if narrow) bug — a rider could previously
receive the same "your ride is in 10 minutes" push more than once if the DB
write happened to fail after a successful send. After this fix, at most one
push per ride within the dedupe window, regardless of how the flag write
goes. No new notification copy or type was introduced.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/scheduled_rides.py` | `_send_reminder` now claims/releases a Redis dedupe key around the push send, independent of the `reminder_sent` DB write | Stop a DB-write failure from causing a duplicate reminder push |
| `backend/tests/test_scheduled_dispatch_cr.py` | New `TestSendReminderIdempotency`: happy path, push-failure-retries, and the flag-write-failure-doesn't-duplicate regression case | Pin exactly the failure-mode split described above |

## 7. Before / after

```python
# Before
if rider_id:
    await send_push_notification(...)
await db.update_one("rides", {"id": ride_id}, {"$set": {"reminder_sent": True}})
# (both inside one try/except — a failure after a successful send leaves
#  reminder_sent False, so the next tick re-sends the push)
```

```python
# After
should_push = await redis_set_nx(dedupe_key, "1", ttl=3600)  # False if already claimed
if rider_id and should_push:
    try:
        await send_push_notification(...)
    except Exception:
        await redis_delete(dedupe_key)  # release so the next tick retries the send
        raise
await db.update_one("rides", {"id": ride_id}, {"$set": {"reminder_sent": True}})
```

## 8. Rollback plan

Plain code change, no migration. `git revert` fully restores prior behavior.
The dedupe key is self-expiring (1h TTL); no cleanup needed either way.

## 9. Verification performed

- [x] Automated tests: `backend/tests/test_scheduled_dispatch_cr.py`, full
      file, 15 passed (12 pre-existing/prior-fix + 3 new) via the session's
      venv.
- [x] `ruff check` — clean. Also manually verified the dual-import
      try/except block stayed symmetric (`redis_delete` present in both the
      relative and absolute import branches) — the repo's format-on-save
      hook has stripped "currently unused" names from the except branch of
      this exact pattern twice earlier in this session, so this was checked
      by hand rather than assumed.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Blast-radius grep performed (see §4).
- [x] Reviewed against CLAUDE.md's "never silently swallow" and
      background-loop replay-safety conventions.
- [ ] Feature-flagged — not flagged; this is a bug fix to an existing
      notification's delivery guarantee, not new user-visible behavior.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — the reminder
      still sends at the same 10-minute mark with the same copy; only the
      duplicate-on-DB-failure bug is closed

## What was NOT verified

Not tested against a real Redis instance or a real Supabase write failure —
both failure paths are simulated via mocks. The "Redis unavailable" fallback
branch (falls through and sends) is exercised implicitly by the existing
`redis_set_nx` exception-handling pattern elsewhere in this file but not by
a dedicated test for `_send_reminder` specifically.
