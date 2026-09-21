# Change Impact & Risk Log — uncollected fares stay payable to the driver (flag removed)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code session, on the repository owner's product decision |
| Surface(s) | backend |
| Domain (Sentry tag) | payments / drivers |
| PR / commit link | branch `claude/loving-thompson-amjk8b` |
| Related issue or gap ID | reverses the 2026-09-20 review's C1 |

## 1. Issue / gap identified

The 2026-09-20 review filed C1 as a cash leak: a completed ride whose card charge failed still
counts toward `payable_balance` and is paid to the driver by the weekly `auto_payout` batch, so
Spinr absorbs the fare. A flag-gated filter
(`utils/payment_collection.payable_ride_filter`, `app_settings.uncollected_rides_excluded_from_payable`)
was built to exclude those rides, shipped **off**.

**That framing was wrong.** The repository owner has confirmed this behaviour is deliberate
product policy: the driver drove the trip, so Spinr pays them and absorbs the failed charge, then
pursues the rider separately. The flag therefore has no future in which it is turned on — leaving
it in place would be a loaded gun pointed at driver payouts, and its presence in the code and in
`GET /admin/settings` invites exactly the mistake it was built to avoid.

## 2. Root cause

The review inferred intent from code plus the 0%-commission model ("every failed card is 100%
platform loss") without a stated policy to check it against. The reasoning was internally sound and
the conclusion still wrong, which is why the mechanism shipped dark rather than on — the one
decision in that chain that held up.

## 3. Fix / remediation

Remove the flag and the filter outright rather than leave them dormant:

- `utils/payment_collection.py` — drop `ONLY_COLLECTED_RIDES`, `PAYABLE_FILTER_FLAG` and
  `payable_ride_filter`.
- `routes/drivers/earnings.py`, `utils/auto_payout.py` (both the single-driver and batch paths) and
  `utils/driver_statement.py` — drop the `**_collected_filter` spread and the `await`.
- `routes/admin/settings.py` — drop `uncollected_rides_excluded_from_payable` from
  `SettingsUpdateRequest`, so it is no longer an admin-settable field.
- Delete `migrations/434_uncollected_rides_excluded_from_payable_flag.sql`. Safe and complete: the
  file only ever existed on this branch and **has never been applied to any database**, so there is
  no column anywhere to drop.
- Delete `tests/test_uncollected_rides_not_payable.py`, which tested only the removed mechanism.

Each call site keeps a comment stating the policy positively — that an uncollected fare **is**
payable and why — so the next reader does not re-file this as a bug. That is the actual
deliverable here: the code now says what it means.

**What is deliberately KEPT**, because it is unrelated to the payout question and was a genuine
fix: `COLLECTED_PAYMENT_STATUSES` / `SETTLED_PAYMENT_STATUSES`, which `routes/webhooks.py` uses as
its already-settled guard. Before that shared set existed, a redelivered
`payment_intent.payment_failed` could relabel a `partially_refunded` / `disputed` / `dispute_lost`
ride as `failed`. `is_collected` and `drop_uncollected_rides` are also kept: they have no callers
today, but they are the natural building blocks for reporting on this cohort (e.g. the admin
unpaid-rides screen) and deleting them would be a second guess at intent.

**Alternative considered:** leave the flag in place, permanently off, as documentation of the
decision. Rejected — a settings toggle that must never be switched on is a hazard, not
documentation. This log is the documentation.

## 4. Risk & impact on existing functionality

**No behaviour change on deploy.** The flag defaulted to `FALSE` and its column was never created,
so `payable_ride_filter()` has always returned `{}` in every environment. Removing a call that
returns an empty dict and a spread of that empty dict into a query is a no-op by construction.

**Blast radius — the four money surfaces that called it**, all now back to their pre-C1 shape:

| Caller | What it feeds |
|---|---|
| `routes/drivers/earnings.py` `get_driver_balance` | the driver's in-app balance |
| `utils/auto_payout.py` `_compute_payable_balance` | the weekly Stripe Transfer amount |
| `utils/auto_payout.py` `_compute_payable_balances_batch` | same, batched |
| `utils/driver_statement.py` | the driver's periodic statement |

All four now agree unconditionally, which also closes the `/balance` vs `/earnings` divergence
(ACTION_ITEMS.md A28) that having the flag on would have opened.

**`test_settings_column_parity` is fixed by this change, not broken by it.** That test asserts
every field on `SettingsUpdateRequest` has an `ADD COLUMN` behind it. `main` currently fails it:
the field was merged in PR #5599 while its migration stayed on this branch. Removing the field and
the migration together restores parity from the other direction.

**What is NOT changed:** nothing about how an uncollected ride is recovered. The rider is still
blocked from booking (`routes/rides/booking.py:581`), still retried three times with backoff
(`utils/payment_retry.py`), still emailed, and admins are still alerted and can still send a
payable Stripe invoice (`POST /admin/rides/{id}/send-invoice`). Those paths are untouched.

## 5. User-experience effect

None, on any surface. Drivers see the same balance, the same statement and the same payout they
see today, because the filter was never active. Admins lose a settings toggle that did nothing.
Riders are unaffected.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/payment_collection.py` | removed `ONLY_COLLECTED_RIDES`, `PAYABLE_FILTER_FLAG`, `payable_ride_filter`; kept the settled-status set and the two predicates | the filter contradicts policy; the status set does not |
| `backend/routes/drivers/earnings.py` | dropped the filter; comment now states the policy | driver balance |
| `backend/utils/auto_payout.py` | dropped the filter at both call sites | the weekly Transfer |
| `backend/utils/driver_statement.py` | dropped the filter | driver statements |
| `backend/routes/admin/settings.py` | removed the settings field (and the `List` import it orphaned) | stop offering a toggle that must never be used |
| `backend/migrations/434_…sql` | **deleted** | never applied anywhere |
| `backend/tests/test_uncollected_rides_not_payable.py` | **deleted** | tested only the removed mechanism |

## 7. Before / after

```python
# Before — routes/drivers/earnings.py
_collected_filter = await payable_ride_filter()
rides = await db_supabase.get_rows("rides", {
    "driver_id": driver["id"], "status": RideStatus.COMPLETED,
    **EXCLUDE_LEGACY_RIDES, **_collected_filter,
}, limit=10000)
```

```python
# After
rides = await db_supabase.get_rows("rides", {
    "driver_id": driver["id"], "status": RideStatus.COMPLETED,
    **EXCLUDE_LEGACY_RIDES,
    # A completed ride is payable REGARDLESS of whether its fare was
    # collected. Deliberate policy: the driver drove the trip, so Spinr pays
    # them and absorbs a failed card charge, then pursues the rider — the same
    # posture Uber and Lyft take.
}, limit=10000)
```

**Scenario.** A rider's card fails on a $45 ride; `payment_retry` exhausts its three attempts and
parks the ride at `payment_status='failed'`. **Before and after this change, identically:** the
driver's $45 stays in `payable_balance` and is paid in the next weekly batch; the rider is blocked
from booking until they settle; an admin can send them a payable Stripe invoice. The only
difference is that the code no longer carries a switch that would have stopped the driver being
paid.

## 8. Rollback plan

`git revert` + deploy. No migration to reverse (the file was never applied), no data written, no
settings row to clean up — the column does not exist in any environment.

Reverting restores a dormant, default-off flag. It would not change behaviour either, so there is
no urgency to revert under any failure mode.

## 9. Verification performed

- `ruff check` / `ruff format` / `py_compile` clean on all five changed Python files.
- Grepped for every remaining reference to `payable_ride_filter`, `PAYABLE_FILTER_FLAG`,
  `ONLY_COLLECTED_RIDES` and `uncollected_rides_excluded_from_payable` across `backend/` — none
  outside `__pycache__` and this log.
- Confirmed `is_collected` / `drop_uncollected_rides` have no callers before deciding to keep them
  (kept as reporting building blocks, stated in §3 rather than left implicit).
- Confirmed the migration exists only on this branch (`git ls-tree origin/main` shows `432` as the
  RLS migration, no `434`), so deleting the file removes it everywhere it has ever existed.
- Read the recovery path end to end to confirm it is untouched: `booking.py:581` blocks the rider,
  `payment_retry.py` retries and alerts, `POST /admin/rides/{id}/send-invoice` issues the payable
  invoice.

## 10. What was NOT verified

- **pytest was not run** (this sandbox cannot reach PyPI). CI is the gate — in particular
  `tests/test_auto_payout.py`, `tests/test_driver_earnings*.py` and
  `tests/test_settings_column_parity.py`, the last of which this change should turn from red to
  green.
- Not exercised against a real Supabase. The claim that removing an always-empty spread is a no-op
  is from reading the code, not from observing two identical query plans.
- **No check of whether any environment somehow has the column.** The migration was never merged,
  but if someone applied the file by hand from this branch, the column would linger harmlessly
  (nothing reads it). Dropping it would then be a manual cleanup, not a migration.
- The admin-facing consequence — that these rides need a screen of their own — is deliberately a
  separate change, not folded in here.
