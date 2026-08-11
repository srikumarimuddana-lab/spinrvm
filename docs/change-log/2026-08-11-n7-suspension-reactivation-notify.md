# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude Code (claude-sonnet-5) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (matches the file's existing `extra={"domain": "admin"}` on all its log calls) |
| PR / commit link | branch `claude/n7-suspension-reactivation-notify` |
| Related issue or gap ID | ACTION_ITEMS.md N7 (D21) |

## 1. Issue / gap identified

`backend/utils/suspension_reactivation.py`'s `_reactivate_tick()` background loop
auto-flips a temporarily suspended rider/driver's `status` back to `'active'`
once `suspended_until` has elapsed, and writes an `audit_logs` row — but never
tells the user. They only find out by trying the app and discovering it works.

## 2. Root cause

The loop was built to fix the admin-side symptom (a stale "Suspended" badge in
the admin Users list) and stopped once the DB write + audit trail were in
place. No notification step was ever added.

## 3. Fix / remediation

After a reactivation update actually takes effect (the existing
`if not updated: continue` guard, which already exists to skip a replica that
lost the atomic-update race), the loop now also calls
`send_push_notification(uid, "Account reactivated", "Your account is active
again. Welcome back!", data={"type": "suspension_lifted"}, target_app=None)`,
wrapped in its own try/except so a delivery failure can never break the loop,
block the next candidate, or retroactively affect the audit-log write that
already happened before it (the notification call is placed *after* the
audit-insert block, matching requirement (d) — order is: atomic update →
audit insert (try/except, `logger.warning` on failure) → notification
(try/except, `logger.warning` on failure)).

**`target_app` resolution decision**: rather than resolving `target_app` from
a per-row role signal, this fix always passes `target_app=None` (routes to
the legacy `fcm_token` column). Reasoning, found via research rather than
assumption:
- `users.role` is reserved for admin RBAC only
  (`migrations/256_users_role_reject_admin_values.sql`'s own comment: "Real
  admin identities live in `admin_staff`, NOT in `users.role`... This column
  is [not] a rider/driver discriminator"). It is not a valid signal for
  push-token routing.
- The actual rider/driver signal is the pair of boolean flags `is_rider` /
  `is_driver` (`migrations/101_users_add_is_rider.sql`), and an account can
  be **both** (dual-role, Uber-style) — so even reading those flags would not
  yield a single correct `target_app` for every suspended account this loop
  might touch.
- There is a directly analogous precedent already shipped for this exact
  event: `routes/admin/users.py`'s `PATCH /admin/users/{id}/status` — the
  *manual* admin equivalent of what this loop does automatically — already
  reactivates a user (`new_status == "active"`) and sends
  `send_push_notification(user_id, "Account reactivated", "Your account is
  active again. Welcome back!", data={"type": "account_status", ...})` with
  **no `target_app` argument at all** (defaults to `None`). This fix reuses
  the identical title/body copy (so a user hears the same thing whether an
  admin manually restored them or the timer did) and the identical
  `target_app=None` choice, for symmetry with the one other place in the
  codebase that already does this exact thing.
- `get_rows`'s `columns="id,suspended_until"` selection was deliberately
  **not** widened to add `role` — a role column exists but is not a usable
  rider/driver signal (see above), so there was nothing useful to add.

**Email-vs-push scope decision**: kept push-only, no email. Checked whether
the suspension action itself (the thing this loop is the automatic mirror of)
notifies via email — it does not: `routes/admin/users.py`'s status-change
endpoint sends push only (no email import, no email call in that code path).
Per the task's own guidance ("if suspension itself sends no notification of
any kind today [beyond one channel], it's reasonable to keep this fix
[matching] rather than inventing a new email template"), push-only is the
correct scope for symmetry with the existing precedent — a wider addition
(e.g. also emailing both the manual and automatic paths) would be a separate,
larger product decision affecting a shared code path outside N7's scope.
Note this is *not* the same as the separate `driver_status_notifications.py`
policy module, which is a different admin action target (the driver-specific
approve/reject/suspend/ban workflow with its own `EMAIL_STATUSES` set) that
this fix does not touch or attempt to reconcile with.

## 4. Risk & impact on existing functionality

- **Callers of `_reactivate_tick` / `suspension_reactivation_loop`**: grepped
  the whole backend tree. The only caller is
  `backend/core/lifespan.py:512,514`, which spawns
  `suspension_reactivation_loop` as one of the startup background loops.
  No other module imports or calls either function. Blast radius: **isolated**
  to this one loop.
- **`send_push_notification` itself**: not modified. This fix is purely a new
  *caller* of the existing, already-well-tested function (used by dozens of
  other call sites across `routes/`, `utils/`, per the grep performed during
  investigation). No risk to any other consumer of that function.
- **The DB write and audit-log write this fix sits after are unchanged** —
  same filter, same fields, same audit payload, byte-for-byte. The only new
  behavior is the additional best-effort call after them.
- **Race condition**: notification firing is gated on the exact same
  `if not updated: continue` check that already guards the audit-log insert,
  so a replica that loses the atomic-update race (another replica or an
  admin already flipped the row) neither audits nor notifies — no risk of
  double-notifying the same user from two replicas racing the same tick.
- **`_record_inbox_notification`** (inside `send_push_notification`) also
  writes a `notifications` (in-app inbox) row via a fire-and-forget
  `asyncio.create_task` — this is existing, unmodified behavior of the
  function being called, not new to this fix; it now simply gets exercised
  from one more call site.

## 5. User-experience effect

**Rider- and driver-facing** (the `users` table this loop touches has no role
filter — a temporarily suspended account can be a rider, a driver, or both).
A user whose temporary suspension window has elapsed now receives a push
notification ("Account reactivated" / "Your account is active again. Welcome
back!") telling them they can use the app again, instead of finding out only
by trying and discovering it works.

This is **net-new, additive** behavior: previously nothing notified the user
at all on this path, so there is no existing notification flow this change
could regress or conflict with. It is not visible mid-session in the sense of
changing an existing screen's behavior — it is a new push notification firing
for an account that, by definition, was not actively mid-session on Spinr's
own booking/dispatch flow while suspended (a suspended account cannot book;
see `routes/rides.py`'s booking gate).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/suspension_reactivation.py` | Import `send_push_notification` (dual-import pattern); after a successful reactivation (post audit-insert), call it with `target_app=None`, wrapped in its own try/except (`logger.warning` on failure); expanded module docstring to explain the notification, the `target_app=None` decision, and that the table covers both riders and drivers | N7/D21: tell the user their suspension lifted |
| `backend/tests/test_suspension_reactivation.py` | Patched `send_push_notification` in both existing tests; added assertions that a successful reactivation notifies once with `target_app=None`, and that a race-loss notifies zero times | Keep existing tests deterministic against the new call; prove the two core guarantees |
| `backend/tests/test_suspension_reactivation_coverage.py` | Patched `send_push_notification` in the five existing tests that reach a successful update; added a new `TestReactivationNotification` class (6 tests) covering: exact-shape single notification, race-loss-never-notifies (with a driver-shaped row), `target_app` always `None` for both rider- and driver-shaped rows, a notification failure not blocking the next candidate or the already-written audit row, and the failure being logged at `WARNING` not `ERROR`; updated module/class docstrings | Full regression coverage per task requirements (a)-(d) |

## 7. Before / after

```python
# Before — utils/suspension_reactivation.py, end of the per-candidate loop body
        except Exception:
            logger.warning(
                "[suspension-reactivation] audit insert failed", exc_info=True, extra={"domain": "admin", "user_id": uid}
            )
# (loop continues to next candidate — nothing else happens for this one)
```

```python
# After
        except Exception:
            logger.warning(
                "[suspension-reactivation] audit insert failed",
                exc_info=True,
                extra={"domain": "admin", "user_id": uid},
            )

        try:
            await send_push_notification(
                uid,
                "Account reactivated",
                "Your account is active again. Welcome back!",
                data={"type": "suspension_lifted"},
                target_app=None,
            )
        except Exception:
            logger.warning(
                "[suspension-reactivation] notification failed",
                exc_info=True,
                extra={"domain": "admin", "user_id": uid},
            )
```

## 8. Rollback plan

**`git revert`-safe** — this is purely additive application code (new import
+ one new best-effort side-effecting call), with no schema change, no data
migration, and no mutation of any already-applied DB state. Reverting the
commit removes the notification call and restores the prior silent behavior
exactly; the reactivation flip and audit-log write it sits after are
byte-for-byte unchanged either way, so a revert cannot leave any live data in
an inconsistent state. No feature flag was added because the change has no
destructive or money-touching failure mode to gate — worst case on a bug is a
missing or malformed push notification, not a broken reactivation.

## 9. Verification performed

- [x] Automated tests run — unit only (this module has no integration/e2e
  tier). `python3.11 -m venv .venv && pip install -r backend/requirements.txt`,
  then `pytest backend/tests/test_suspension_reactivation.py
  backend/tests/test_suspension_reactivation_coverage.py -q`: **25 passed**
  (2 in the smaller file + 23 in the coverage file, up from 21 before this
  change — 6 new notification-specific tests added, 5 of the 21 pre-existing
  ones extended with new assertions rather than counted as new). Also ran the
  broader `-k "suspension or lifespan_loops or lifespan_startup"` sweep (47
  tests across corporate-suspension and document-expiry suites too, to check
  for accidental cross-contamination from the shared `db`/`features` module
  bindings): **47 passed, 1 skipped**.
- [x] `ruff check` and `ruff format --check` on all three touched files:
  clean.
- [x] Blast-radius grep performed: `grep -rn "suspension_reactivation"
  --include="*.py" backend` (excluding the module and its own test files) →
  only `backend/core/lifespan.py:512,514`. `grep -rn 'target_app="rider"\|
  target_app="driver"'` across `backend/` to survey the established
  push-targeting pattern before deciding to diverge from it (documented in
  §3).
- [x] Reviewed against relevant CLAUDE.md conventions: background-loop
  replay-safety (notification gated on the same idempotent-update check as
  the audit log), "do not silently swallow errors" (the notification failure
  path is a deliberate, documented best-effort exception — not a masked
  DB/auth/payment error), observability (`logger.warning` with
  `extra={"domain": "admin", ...}`, matching this file's existing style
  exactly for the audit-insert-failure case).
- [ ] Feature-flagged: not applicable — see §8 rollback reasoning for why a
  flag wasn't warranted for a purely additive, non-destructive, non-money
  side-effect.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data cleanup
  needed).
- [x] Blast radius is stated, not assumed: isolated to this one loop, one new
  caller of an already-shared, already-tested function.
- [x] No silent behavior change to an already-shipped flow: the flow this
  touches (`_reactivate_tick`) previously did nothing user-visible at all on
  this axis, so there is no existing UX to have silently changed — the UX
  field above states this explicitly rather than leaving it implied.

## What was NOT verified

- **No live FCM/Expo push was actually sent.** All verification is against
  `unittest.mock.AsyncMock`-patched `send_push_notification` calls; the real
  device-delivery path (Firebase Admin SDK / Expo REST API) was not
  exercised.
- **No real production traffic** — no staging or production deploy was
  performed as part of this task; only local unit tests were run.
- **No multi-replica race was actually exercised against a live Redis
  lock/live Supabase.** The "another replica won the race" scenario is
  simulated by mocking `db.update_one` to return a falsy value in a single
  process; the real cross-replica atomic-update race (two Fly.io/Railway
  replicas hitting the same conditional `UPDATE ... WHERE status='suspended'`
  concurrently against real Postgres) was not reproduced.
- **The in-app notification-inbox side effect** of `send_push_notification`
  (`_record_inbox_notification`, a fire-and-forget `asyncio.create_task`) was
  not directly asserted in the new tests — it's pre-existing, unmodified
  behavior of the function being called, and the tests mock
  `send_push_notification` itself rather than reaching into its internals.
- **No visual regression tooling exists for this change** — not applicable
  here since there is no UI touched (backend-only, no admin-dashboard/
  rider-app/driver-app files modified, so no `npm run build` was run or
  needed).
- **Copy was not reviewed by a human/product owner** — it reuses the exact,
  already-shipped copy from `routes/admin/users.py`'s manual-reactivation
  path verbatim, on the theory that already-shipped, already-approved copy
  needs no fresh review, but this assumption itself was not confirmed with
  the user.
