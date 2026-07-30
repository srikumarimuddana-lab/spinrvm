# Change Impact & Risk Log — Stripe payout-history sync + T4A inclusion

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-30 |
| Author | Claude Code (requested by operator) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/stripe-sync-t4-generation-shcvce` (commits d60e44e, aa9fe54, 8c01baa, c7908cd) |
| Related issue or gap ID | Legacy migration follow-up: payout history deliberately not imported from old app |

## 1. Issue / gap identified

The legacy migration imported drivers and mapped their Stripe Connect accounts
(`stripe_mapping_import_service`), but the old app's payout history was NOT
imported because the operator does not trust the legacy export. With no record
of legacy-era income in this DB, T4A generation (which sums
`rides.driver_earnings` for completed rides) under-reports every migrated
driver's annual income to the CRA, and the driver payout screen shows no
pre-migration history.

## 2. Root cause

T4A totals and payout history both derive from tables the migration skipped:
the old app's rides were never imported (the `booking_import_service` path,
which would have paired imported earnings with offsetting payouts, was not
run), so neither the earnings nor the payouts of the legacy era exist in
Supabase. Stripe, however, holds the authoritative record: every driver payout
on this platform is a Stripe Transfer with `destination=acct_…`
(`routes/drivers/payouts.py` is the only Transfer creator).

## 3. Fix / remediation

A new admin-triggered sync downloads each mapped driver's Stripe Transfer
history and materializes the transfers no `payouts` row tracks as
`payout_type='stripe_sync'`, `status='completed'` rows (deterministic id
`stripe-sync-{tr_id}`). Those rows are balance-inert but feed T4A: both the
driver slip (`get_t4a_summary` → PDF/CSV/email) and the annual issuance job
now add the synced amounts for the year of the transfer (CRA reports amounts
PAID).

## 4. Risk & impact on existing functionality

Everything that reads/writes `payouts` was enumerated (blast-radius grep:
`payout` across `backend/`):

- `routes/drivers/earnings.py::get_driver_balance` — **the critical consumer.**
  It deducts all non-reversed/failed payouts from payable_balance. Synced rows
  are now explicitly excluded by `payout_type='stripe_sync'`; without that,
  every migrated driver's balance would go negative and block withdrawals.
  `legacy_import` offset rows (booking importer) still deduct — they pair with
  imported ride earnings. The default-deduct posture for unknown statuses is
  unchanged.
- `routes/drivers/payouts.py::get_payout_history` — synced rows appear in the
  driver's payout list (dated by transfer date, labeled "Synced from Stripe
  transfer history"). Intended: drivers regain their pre-migration history.
- `utils/payment_retry.py::retry_stuck_payouts` — queries `status='pending'`
  only; synced rows are `completed` → untouched.
- `utils/stripe_reconcile.py::_reconcile_payouts` — queries
  `requires_manual_review=true` / `status='transfer_completed'` → untouched.
- Migration 250 reservation guard — partial unique index covers
  `reserved/pending/transfer_completed` only → `completed` synced rows never
  block a real payout.
- Migration 162 `payout_stats_fn` (`total_paid` sums `status='completed'`) and
  migration 159 `payouts_overview_aggregates_fn` — admin dashboard "paid out"
  totals will INCREASE by the synced legacy amounts after a commit. This is
  accurate (the money was paid) but operators should expect the jump.
  The 159 fn's T4A distribution buckets read `rides`, so those bucket counts
  still exclude legacy-era income — known limitation, listed below.
- `booking_import_service` — unaffected; different id scheme and payout_type.
  If the booking import is EVER run later for the same era, its rides would
  add earnings while the synced payouts (excluded from balance) stay inert —
  T4A would then double-count that era (rides + synced). Do not run both for
  the same period; noted in the runbook-facing docstrings.
- Background loops (`core/lifespan.py`): no new loop; sync is request-scoped
  admin tooling. T4A annual job behavior changes only in the amount summed.
- Ride state machine, wallet deltas, corporate flows: untouched.

Blast radius: single-surface (backend), two live consumers changed
(balance calc, T4A), one admin-triggered writer added.

## 5. User-experience effect

- **Driver**: after an operator commits a sync — payout history shows
  pre-migration entries; T4A slip totals include legacy income (with a new
  `legacy_synced_earnings` line in the API payload); the ≥$500 T4A eligibility
  push can newly trigger for drivers whose new-app earnings alone were under
  the threshold. Visible mid-session on next screen load; no copy changes to
  existing strings.
- **Internal admin**: two new super-admin endpoints; payout aggregate stats
  rise by the synced amounts after commit.
- **Rider / corporate**: no change.
- Nothing changes for anyone until a super_admin explicitly runs the sync —
  the code ships dark by construction (no synced rows → +$0 everywhere).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/stripe_payout_sync_service.py` | NEW — transfer listing, dedupe vs existing payout rows, plan/commit, year-sum helper | Core sync engine |
| `backend/routes/admin/stripe_payout_sync.py` | NEW — validate/commit endpoints, super_admin, audited | Operator entry point |
| `backend/routes/admin/__init__.py` | Mount new router with `require_super_admin` | Same posture as booking import (writes payouts) |
| `backend/routes/drivers/earnings.py` | Exclude `payout_type='stripe_sync'` from balance deduction | Prevent negative balances / blocked withdrawals |
| `backend/routes/drivers/tax_exports.py` | `get_t4a_summary` adds synced amounts + `legacy_synced_earnings` field | Slip/PDF/CSV report full income |
| `backend/utils/t4a_annual_job.py` | `_driver_annual_earnings` adds synced amounts | Eligibility + notified amount match the slip |
| `backend/tests/…` (4 files) | New/extended tests for all of the above | Regression pins |

## 7. Before / after

`get_driver_balance` payout deduction:

```python
# Before
total_payouts = sum(
    (_d(p.get("amount") or 0) for p in payout_rows if str(p.get("status") or "").lower() not in _not_money_out),
    Decimal("0"),
)
```

```python
# After — stripe_sync history rows no longer deduct
total_payouts = sum(
    (
        _d(p.get("amount") or 0)
        for p in payout_rows
        if str(p.get("status") or "").lower() not in _not_money_out
        and p.get("payout_type") != "stripe_sync"
    ),
    Decimal("0"),
)
```

T4A annual earnings:

```python
# Before
return sum((Decimal(str(r.get("driver_earnings") or "0")) for r in rides), Decimal("0"))
```

```python
# After
return ride_total + synced_total  # synced_total = Σ payouts where payout_type='stripe_sync' in the year
```

## 8. Rollback plan

No feature flag: the capability is gated behind an explicit super_admin action
instead (nothing changes until a commit is run), and the read-side changes are
no-ops while zero `stripe_sync` rows exist. If a committed sync turns out
wrong, rollback is data-level and needs **no redeploy**:

```sql
-- Remove one batch's synced rows (or all synced rows):
DELETE FROM payouts WHERE payout_type = 'stripe_sync' AND id LIKE 'stripe-sync-%';
```

Deleting the rows returns balance math (already excludes them), payout
history, T4A totals, and admin aggregates to their pre-sync values in one
statement. The rows are copies of Stripe data, so deletion loses nothing —
re-running the sync recreates them identically (deterministic ids). No Stripe
object is created/mutated by this feature (read-only listing), so there is no
Stripe-side remediation.

## 9. Verification performed

- [x] Automated tests run: unit — `test_stripe_payout_sync_service.py` (10),
  `test_admin_stripe_payout_sync.py` (6), `test_drivers_extended.py`
  balance class, `test_p2_payout_t4a.py`, `test_t4a_annual_job.py`,
  `test_t4a_email.py`, plus regression suites `test_instant_payout.py`,
  `test_payout_toctou.py`, `test_stripe_reconcile.py`,
  `test_replay_safety_payment_loops.py`, `test_admin_stripe_import.py`,
  `test_admin_booking_import.py` — all passing (see branch CI).
- [x] Blast-radius grep performed: `payout` / `payout_type` /
  `stripe_transfer_id` across `backend/` — consumers enumerated in §4.
- [x] Reviewed against CLAUDE.md conventions: Decimal-only money
  (`utils/money.cents_to_dollars`, float only at the insert boundary matching
  the booking importer), no PII in reports/audit rows, errors surface loudly
  (per-driver Stripe list failure blocks commit rather than under-reporting),
  super_admin + audit for a payouts writer.
- [ ] Manual staging run: NOT performed — no staging Stripe key in this
  environment. Operator should run `/validate` first (dry run, no writes) and
  compare `stats.sum_by_year` against the Stripe dashboard's transfer totals
  before committing.

## 9b. Self-review finding (found and fixed before merge)

**`_fetch_sync_targets` silently processed at most ~1000 drivers.** The
all-drivers branch called `.execute()` with no `.limit()` / `.range()`, so
PostgREST's `db-max-rows` (1000 on Supabase) capped the result **with no
truncation signal**. Every driver past the cap would have been skipped
without appearing in the report's `drivers_scanned` as missing — leaving
their legacy payout history unsynced and, because this feeds the T4A slip,
under-reporting their income to the CRA. Fixed with explicit
`.order("id").range(...)` pagination; the test fake now models the
server-side cap and a new test with 1250 drivers proves all are scanned.
Severity: medium (CRA reporting), reachable as soon as driver count exceeds
1000 — the legacy import alone brought in ~900.

## What was NOT verified

- Not tested against live Supabase or live Stripe — only mocked clients
  (per repo convention, unit tests never hit real services). The Stripe
  pagination path (`auto_paging_iter`) is exercised against a fake.
- Drivers migrated onto a brand-new Stripe account (Stripe-support "scenario
  B" migrations) keep their transfer history on the OLD account; the sync
  surfaces them as `no_transfers` warnings but cannot recover that history.
- Admin dashboard T4A distribution buckets (`payouts_overview_aggregates_fn`,
  migration 159) still read `rides` only and will not include synced legacy
  income — standing gap, candidate for `ACTION_ITEMS.md`.
- No production build applies (backend-only change; no
  admin-dashboard/rider-app/driver-app code touched).
