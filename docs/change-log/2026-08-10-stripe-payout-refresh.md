# Change Impact & Risk Log — Admin Stripe payout refresh (pull all money, with timestamps)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-10 |
| Author | Claude Code session (operator: srikumarimuddana) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | payments |
| PR / commit link | PR #3525, branch `claude/stripe-payout-refresh-bgz9s4` |
| Related issue or gap ID | Operator report: "Refresh from Stripe still shows only the original $200; manual $200 service-area boost invisible" |

## 1. Issue / gap identified

The admin "Refresh from Stripe" button only synced KYC/verification state, never financial data, and the payouts summary ignored `driver_bonuses` entirely — so a manual $200 service-area boost (a `driver_bonuses` row) and any Stripe-side payment history were invisible to the operator, and admin vs driver earnings disagreed.

## 2. Root cause

Two separate gaps: (a) no per-driver entry point existed to the Stripe Transfer sync (`stripe_payout_sync_service`) or connect-ledger sync — they were only reachable via the fleet-wide super-admin import tools; (b) `admin_get_driver_payouts_summary` computed lifetime earnings from `rides.driver_earnings` only, while the driver-facing `get_driver_balance` also counts `driver_bonuses` — the two views could never agree for any driver with a bonus/adjustment.

## 3. Fix / remediation

- New super-admin endpoint `POST /api/admin/drivers/{id}/refresh-stripe-payouts` that runs the existing transfer sync (`build_plan`/`commit_plan`, scoped to one driver) plus `sync_connect_ledger`, and returns the full payout history with timestamps.
- `admin_get_driver_payouts_summary` now includes `driver_bonuses` in `lifetime_earnings` / `ytd_earnings` / `pending_balance` (mirroring `routes/drivers/earnings.py`) and returns a `bonuses` list.
- Summary math now excludes `payout_type='stripe_sync'` rows from `total_paid_out` / `pending_balance` deduction — mirroring the driver-facing exclusion — and surfaces them separately as `legacy_stripe_transfers`.
- Admin dashboard: "Refresh Payouts from Stripe" button, bonuses table, payout Type column, legacy-transfer total on the Total-paid-out card.

## 4. Risk & impact on existing functionality

- **`payouts` table writers/readers**: rows written here are `payout_type='stripe_sync'`, `status='completed'` with deterministic ids — inert to the payout retry loop, the Stripe reconciler, and the migration-250 reservation guard (same rows the fleet-wide sync already writes). `get_driver_balance` (driver app) already excludes them.
- **T4A**: synced rows feed `driver_synced_earnings_for_year` — that is the *intended* effect (legacy income appears on the slip). No double-count: T4A adds only `stripe_sync` rows on top of `rides.driver_earnings`; app-native payouts and `legacy_import` rows are excluded (verified against `tax_exports.py` and `t4a_annual_job.py`).
- **Summary consumers**: `getDriverPayoutsSummary` is called only from the drivers page Payouts tab (single importer, checked). New response fields are additive; existing fields keep their meaning except `lifetime_earnings`/`pending_balance` now include bonuses and exclude legacy transfers — which is the correction, not a regression.
- **Blast radius**: single-surface admin read path + one new super-admin write path. No ride state, dispatch, or wallet-delta interaction. No background-loop interaction (the endpoint is request-scoped; the sync services it calls are already replay-safe/idempotent).
- Repeated clicks are safe: deterministic row ids (`stripe-sync-{transfer_id}`) + upsert-on-id ledger writes; no Stripe *write* calls are ever made.

## 5. User-experience effect

- **Internal admin only.** Payouts tab gains a refresh button, a bonuses table, a Type column, and (for migrated drivers) a legacy-transfer note. Visible immediately on next page load after deploy; not visible mid-session to riders/drivers.
- For drivers with bonuses, admin "Lifetime earnings" and "Pending payout" numbers change (increase) to match the driver's own app view. This is a silent behavior change to an already-shipped admin screen — flagged here deliberately: the old numbers were the bug.
- Driver-facing surfaces unchanged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/drivers.py` | New `admin_refresh_driver_stripe_payouts` endpoint; summary includes bonuses, excludes stripe_sync from balance math | Core fix |
| `admin-dashboard/src/lib/api/drivers.ts` | `refreshDriverStripePayouts` + type updates | API client |
| `admin-dashboard/src/lib/api.ts` | Barrel export | API client |
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | Refresh button, bonuses table, Type column, legacy total | UI |
| `backend/tests/test_admin_drivers_coverage.py` | Regression tests: bonuses fold-in, stripe_sync exclusion, 403/404/400/502 paths, success shape | Coverage |

## 7. Before / after

```python
# Before — admin summary deducted EVERY completed payout and ignored bonuses
lifetime_earnings = sum((_dec(r.get("driver_earnings")) for r in rides), Decimal("0"))
total_paid_out = _sum_by_status("completed")
pending_balance = max(lifetime_earnings - total_paid_out - pending_in_flight, Decimal("0"))
```

```python
# After — bonuses counted, legacy stripe_sync rows excluded (mirrors earnings.py)
lifetime_earnings = lifetime_ride_earnings + total_bonuses
def _sum_by_status(*statuses):
    return sum((_dec(p.get("amount")) for p in payouts
                if p.get("status") in statuses and p.get("payout_type") != "stripe_sync"), Decimal("0"))
pending_balance = max(lifetime_earnings - total_paid_out - pending_in_flight, Decimal("0"))
```

## 8. Rollback plan

- The sync endpoint is pull-only reconciliation: if it misbehaves, stop using the button (super-admin-only reach); no automatic caller exists.
- Rows it wrote are identifiable and reversible without redeploy:
  `DELETE FROM payouts WHERE payout_type = 'stripe_sync' AND id LIKE 'stripe-sync-%';`
  (balance-inert by design, so deletion only affects history/T4A display; `driver_stripe_payouts` / `driver_stripe_ledger` rows are read-only mirrors and can be left or deleted by `synced_at`).
- Summary-math change is read-path only — reverting the commit restores the old numbers with no data remediation needed.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_admin_drivers_coverage.py -k "PayoutsSummary or RefreshStripePayouts"` (unit, mocked Supabase/Stripe)
- [ ] Manual repro in staging — NOT performed (no staging Stripe key in this environment)
- [x] Blast-radius grep: `payout_type` across backend; `getDriverPayoutsSummary` importers across admin-dashboard; T4A call sites (`tax_exports.py`, `t4a_annual_job.py`)
- [x] Reviewed against CLAUDE.md money rules (Decimal-only, Stripe idempotency, no error-swallowing) + spinr-money-auditor and spinr-security-auditor agent passes; their findings (super-admin gate, error propagation, stripe_sync exclusion) fixed in follow-up commits on the PR
- [x] Real production build run for admin-dashboard: `npx next build` — compiled successfully
- [ ] Feature flag: not added — the write path is gated to super_admin and manually triggered; the summary correction is a read-path bug fix

## What was NOT verified

- Not tested against live Supabase or live Stripe — all API responses mocked. The Stripe listing behavior (pagination, superseded-account warnings) relies on the existing, already-shipped sync services.
- No visual regression tooling exists for admin-dashboard; UI changes were reasoned about and build-verified, not screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in

---

## Addendum — round-2 review + production hotfix (same day)

- **Prod 500 fixed** (`85d17ba`): `stripe.Transfer.list().auto_paging_iter()` yields StripeObjects that are not Mappings on the deployed SDK build; `.get()` on the raw object raised `AttributeError: get`. Paged objects now go through `stripe_object_to_dict` (same rule the connect-ledger service documents). Regression test uses a non-Mapping StripeObject fake.
- **Admin money-out now mirrors `earnings.py` exactly**: deduct-by-default (everything except `reversed`/`failed`, minus `stripe_sync`), so persistent stuck statuses (`reserved`, `transfer_completed`) no longer inflate `pending_balance` — previously an operator reconciling from that number could double-pay a driver whose instant payout stalled after the Transfer step.
- **Payout aggregation fetch raised 200 → 5000 rows** (mirror of `earnings.py`) so lifetime deduction isn't computed over a truncated window for drivers with long cash-out histories; display list still capped by `limit`.
- **"Refresh Payouts from Stripe" button hidden for non-super-admins** — matches the endpoint's 403 gate instead of toasting a guaranteed failure.
