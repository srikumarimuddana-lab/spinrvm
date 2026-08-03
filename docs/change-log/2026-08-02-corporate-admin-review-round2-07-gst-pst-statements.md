# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard (company-portal) |
| Domain (Sentry tag) | corporate, payments |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "no GST/PST breakdown on corporate statements" |

## 1. Issue / gap identified

Tax (GST/PST) is computed and persisted per-ride (`rides.tax_amount` /
`rides.tax_breakdown`, migration 46) and already shown on rider receipts
(`utils/email_receipt.py`, `utils/receipt_pdf.py`), but never surfaced on
the one place a corporate finance manager could self-serve it: the
company-portal billing summary/statement/CSV export. Without it, a
company can't reconcile input tax credits from Spinr's own numbers.

## 2. Root cause

`ride_payment_sources` (the table backing corporate billing) has no tax
columns of its own — tax lives on `rides`, joined by `ride_id`. The
billing endpoints (`billing_summary`, `billing_statement`) never joined
to `rides` to pull it in; nothing else was broken, the join was simply
never built.

## 3. Fix / remediation

- New `_attach_ride_tax()` helper in `routes/corporate_company.py` —
  fetches `rides` by `$in` on the payment-source rows' `ride_id`
  (CLAUDE.md's established convention for a two-table lookup, never a
  PostgREST embed), merges `tax_amount`/`tax_breakdown` onto each row.
  Short-circuits (no query) when no row has a `ride_id`.
- `_aggregate_rows()` now sums `tax_total` and a `tax_by_type` breakdown
  (keyed by the same labels — "GST", "PST" — already used on receipts),
  alongside the existing allowance/master totals.
- `billing_summary` and `billing_statement` call `_attach_ride_tax()`
  before aggregating/returning, so both the per-ride line items and the
  monthly summary now carry tax data.
- Company-portal billing page: new "Tax (GST/PST)" summary card (mirrors
  the existing Allowance/Master-fallback cards), and the CSV export gains
  a `tax_amount` column.

## 4. Risk & impact on existing functionality

- **Blast radius: two billing GET endpoints, their shared aggregation
  helper, one UI page.** `billing_transactions` (the wallet ledger view)
  is untouched — it doesn't page through rides at all.
- Grepped every caller of `_aggregate_rows`: only `billing_summary` and
  `billing_statement`, both updated together in this same commit, so no
  caller is left seeing the old shape without the new fields.
- Grepped every consumer of `BillingSummary`/`BillingStatement`/
  `BillingLineItem` frontend types: only the one company-portal billing
  page, updated in this same commit.
- Additive-only on the API surface: `tax_total`/`tax_by_type` are new
  keys on an existing response object, `tax_amount`/`tax_breakdown` are
  new optional keys on each line item — no existing key removed or
  renamed, so any other caller reading the old fields is unaffected.
- Extra query cost: one additional `rides` `$in` query per call to
  `billing_summary`/`billing_statement` (two extra in `billing_statement`,
  since it already double-fetches for the paginated page vs. the
  full-month aggregate — a pre-existing pattern, not introduced here).
  Bounded by the same page sizes the existing pagination already uses.

## 5. User-experience effect

**Corporate-admin-facing** (company-portal billing page, `require_company_admin`
gated — unchanged). A finance manager can now see GST/PST as a distinct
line and in the CSV export, for input-tax-credit reconciliation. No
change to any other figure already shown (total spend, allowance,
master fallback) — tax was always included in `total_fare`/`grand_total`
charged to the rider; this only makes the *breakdown* visible, it
doesn't change what was billed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/corporate_company.py` | New `_attach_ride_tax()`; `_aggregate_rows()` now sums `tax_total`/`tax_by_type`; both billing endpoints call the new helper | Surface already-computed tax data |
| `admin-dashboard/src/lib/api/corporate.ts` | `BillingSummary`/`BillingStatement`/`BillingLineItem` types gain tax fields | Match the new response shape |
| `admin-dashboard/src/app/company-portal/[id]/billing/page.tsx` | New "Tax (GST/PST)" summary card; CSV export gains a `tax_amount` column | Give the finance manager a self-serve view |
| `backend/tests/test_corporate_company_gap_coverage.py` | 5 new tests: tax merge, empty-ride-id short-circuit, aggregate sum/breakdown, zero-default when tax fields absent, end-to-end route test | Lock in both halves of the fix |

## 7. Before / after

```python
# Before
return {
    "month": month,
    "wallet_balance": ...,
    **_aggregate_rows(all_rows),  # no tax fields
}

# After
all_rows = await _attach_ride_tax(all_rows)
return {
    "month": month,
    "wallet_balance": ...,
    **_aggregate_rows(all_rows),  # now includes tax_total, tax_by_type
}
```

## 8. Rollback plan

`git revert` the commit. No migration, no data written — purely additive
fields on existing read endpoints and a new UI card.

## 9. Verification performed

- [x] 5 new backend tests: `_attach_ride_tax` merges amount/breakdown
      correctly, short-circuits with no `ride_id`s (no empty-`$in` query),
      `_aggregate_rows` sums `tax_total`/`tax_by_type` correctly across
      multiple rides, defaults to zero when tax fields are absent (a
      caller that forgot to call `_attach_ride_tax` doesn't `KeyError`),
      and an end-to-end `billing_summary` route test confirming the
      response includes the new fields.
- [x] `python3 -c "import ast; ast.parse(...)"` on both touched Python
      files — clean.
- [x] Bracket-balance check on both touched `.ts`/`.tsx` files (no TS/JS
      toolchain run, per this round's instruction) — balanced.
- [x] Confirmed the existing `test_billing_summary_pages_through_all_rows`
      / `test_billing_statement_pages_through_all_rows` tests (unmodified)
      still pass under reasoning: their fixture rows have no `ride_id`
      key, so `_attach_ride_tax` short-circuits without touching
      `db_supabase.get_rows` — no new mock needed, no behavior change for
      those tests.
- [x] Blast-radius grep performed (see §4): every caller of
      `_aggregate_rows` and every consumer of the touched frontend types.

## 10. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — every caller/consumer of the
      touched functions and types grepped and confirmed
- [x] No silent behavior change to a working flow — every existing field
      in both responses keeps its exact prior value; only new fields were
      added, verified by tracing `_aggregate_rows`'s existing math (untouched)
      alongside the new tax accumulation (additive)

## What was NOT verified

Did not run `pytest`, `eslint`, `tsc --noEmit`, or a production build —
per this round's explicit instruction, deferred to a single pass at the
end. Did not run against a live Postgres instance — the `rides` `$in`
join was verified by structural comparison to this session's earlier
established two-table-lookup pattern, not executed. Did not manually
click through the billing page in a browser — reasoned through the
existing `Metric` component's established prop shape rather than
screenshotted; no visual-regression tooling exists in this repo for this
surface (a standing, previously-flagged gap). Did not extend this to the
admin-side corporate billing views (if any exist beyond the
company-portal) — the finding specifically named "the one place a
corporate finance manager could self-serve it," which is the
company-portal billing page; an admin-side equivalent was not identified
as in scope for this fix.
