# Change Impact & Risk Log

> Copy this template into the PR description, or save a filled copy to
> `docs/change-log/YYYY-MM-DD-<short-slug>.md` for anything touching a
> live-tested surface (rides, dispatch, payments, auth, corporate, safety).
> See `CLAUDE.md` → "Change Impact & Risk Log (mandatory)" for the full policy.

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Claude Code (subagent), for ittalenthire.ca@gmail.com |
| Surface(s) | backend |
| Domain (Sentry tag) | payments / drivers / admin |
| PR / commit link | branch `fix/t4a-cancellation-bonus-income`: `7690a39`, `e4ff471`, `d264912` (not pushed) |
| Related issue or gap ID | 2026-09-22 proactive fleet audit (`spinr-regulatory-compliance-checker`), BLOCKER: T4A omits cancellation-fee and bonus income |

## 1. Issue / gap identified

The driver's T4A slip, the $500 CRA eligibility check and the admin T4A filer export all left out cancellation/no-show fees, quest/referral bonuses and per-ride incentive payments. So a driver's T4A could understate real, paid income, or a driver could be wrongly treated as under the $500 T4A threshold, even though Spinr's own earnings statements already showed them this income.

## 2. Root cause

Every T4A total was built from only two sources: completed-ride `rides.driver_earnings`, plus settled legacy Stripe payouts (`payouts.payout_type IN ('stripe_sync','legacy_outstanding_correction')`, `status='completed'`). Three other kinds of paid driver income live somewhere else:

- **Cancellation/no-show fees.** These are stored in `rides.cancellation_fee_driver` on a ride with `status='cancelled'`, and credited through `wallet_apply_delta(type_="cancellation_fee")` (`services/cancellation_service.py`). A cancelled ride is never `completed`, and the fee is never written as a counted `payouts` row, so the T4A queries never saw it.
- **Quest and driver-referral bonuses.** These are rows in the `driver_bonuses` ledger (migration 179). No T4A function read that table.
- **Per-ride incentives.** These are rows in `ride_incentive_claims`. `rides.driver_earnings` is fare-only by design (`routes/drivers/ride_complete.py`), so incentives are *not* already included in the ride total.

`utils/driver_statement.py::_build` (the periodic earnings statement) and `utils/auto_payout.py::_balance_from_rows` (the payable balance that actually drives Stripe payouts) already counted all three. The fix was made in the statement and payout logic but never reached the T4A logic.

**Tracing the cancellation-fee source.** The per-driver wallet ledger entry and `rides.cancellation_fee_driver` record the same money. The driver is credited `fee_driver` whether or not the rider's card charge succeeds, because Spinr absorbs that loss (`routes/rides/cancellation.py`, `routes/drivers/ride_cancel.py`). The ride column is what the payout balance and the statement read, so the T4A now reads that column and **not** the wallet ledger as well. Summing both would double-count.

**How it was introduced:** each income source was added after the T4A code, and the T4A code was never updated. **Why it wasn't caught:** no test put a driver with non-ride income through a T4A function.

## 3. Fix / remediation

- New module `backend/utils/t4a_income.py`. One shared helper fetches, for one driver and a UTC window:
  - cancellations with a fee (`status='cancelled'`, `cancellation_fee_driver > 0`, by `cancelled_at`)
  - `driver_bonuses` rows (by `created_at`)
  - `ride_incentive_claims` for the completed rides the caller already counts (batched `$in`)

  It returns Decimal totals. It takes the db module as a parameter, so each caller's existing test patch point still applies. DB errors propagate; nothing is swallowed.
- `get_t4a_summary` (the downloadable/emailed slip, the PDF and the earnings CSV) adds the three streams to `total_earnings`/`net_earnings`. It also returns three new reconciliation fields: `cancellation_fee_earnings`, `bonus_earnings`, `incentive_earnings`. These follow the existing `legacy_synced_earnings` pattern and are additive.
- `get_t4a_years` (the list of tax years the driver app offers a slip for) buckets the same income by year. A year whose only income was fees or bonuses now appears.
- `_driver_annual_earnings` (the $500 threshold check, and the dollar amount in the "Your T4A is ready" push) uses the same expanded total.
- **Fourth call site found:** `routes/admin/compliance.py::_t4a_filer_handoff_rows`, the super-admin export handed to the third-party CRA filer.
  - Commit `e4ff471` adds the three streams, read fleet-wide with no per-driver N+1 queries.
  - It also adds `order="id"` to the completed-ride pagination. Unordered offset pages can skip or repeat rows.
  - Commit `d264912` fixes two older divergences from the slip in the same export. It now excludes previous-app imported rides (`EXCLUDE_LEGACY_RIDES`) and includes settled legacy Stripe payouts, using the same filters as the slip and the annual job.
- GST/PST collected on fares is still **not** added. The statement shows it only as a remittance reminder; it is tax collected, not a fee for services. That is unchanged from before, and a tax advisor should confirm it.

## 4. Risk & impact on existing functionality

**Does this retroactively affect ALREADY-ISSUED T4A slips for a prior tax year?**
No stored record is changed; this code writes nothing. But in practice it is retroactive for anything viewed or exported after deploy. No table stores T4A amounts. Every T4A figure is computed live on each request. So after deploy:

- **Prior-year slips re-downloaded or re-emailed** by a driver (`GET /drivers/t4a/{year}`, `/pdf`, `/email`, `/earnings/export`) show the new, higher total for any past year where the driver had fees, bonuses or incentives. A PDF or CSV the driver already downloaded or received by email keeps the old figure. The same driver can therefore hold two different slips for the same year.
- **The tax-year list** can now include a past year it didn't list before.
- **The annual job does NOT re-run for a past year.** The Redis key `spinr:t4a:issued:{year}` has a 400-day TTL, and the job only runs on the last day of February. The 2025 batch (run 2026-02-28, if it ran) used the old total. Drivers who are over $500 for 2025 only under the new total were **not** notified and will not be notified automatically. The first batch using the new logic is tax year 2026, on 2027-02-28. The 2025 `audit_logs` row (`t4a_annual_issuance`, `batch_2025`) keeps its old `eligible_count`.
- **The filer handoff export**, if re-run for a year that was already filed, now returns different amounts. It may also add drivers (fee/bonus income, synced legacy payouts) and remove or reduce legacy-ride income.
- **Action for a human, separate from this code fix:** find out whether any T4A or Part XX.1 return has already been filed with the CRA for a past tax year (most likely 2025) using the old export or slips. If so, amended or new slips may be needed for drivers whose income was understated, drivers who were left out, or drivers whose legacy-ride income was double-reported. Also decide whether drivers who become newly eligible for a past year should be notified. This code does neither of those things.

**Other readers and writers of the same data.** All reads are unchanged; no writes were added.
- `rides.cancellation_fee_driver`: read by `driver_statement.py`, `auto_payout.py`, `routes/drivers/earnings.py`, `ride_reads.py`, `rides/queries.py`, `receipts.py`, `reconcile_cancelled_captured_refunds.py`.
- `driver_bonuses`: read by `earnings.py`, `auto_payout.py`, `driver_statement.py`, `routes/admin/drivers.py`; written by `quests.py` and `referral_payout.py`.
- `ride_incentive_claims`: read by `earnings.py`, `auto_payout.py`, `driver_statement.py`, `ride_repo.py`, `admin/incentives.py`, `admin/rides.py`; written by `incentive_service.py`.

None of those paths is touched.

**Who consumes the changed functions.**
- `get_t4a_summary` → `download_t4a_pdf`, `export_earnings` (CSV), `email_t4a_summary`, `email_earnings_export`, and the driver-app Tax Documents screen.
- `get_t4a_years` → the driver-app tax-year list.
- `_driver_annual_earnings` → only `_run_issuance`.
- `_t4a_filer_handoff_rows` → only `GET /admin/compliance/t4a-filer-handoff`, which is super_admin only.

**Blast radius:** single surface (backend), with read-only money/tax reporting. No ride state machine, wallet delta, Stripe call, insurance-period row, or migration is involved.

**Load:**
- The annual job does up to 3 more round-trips per driver, once a year. The cancellation and bonus reads run concurrently.
- The slip and year-list endpoints get 2–3 more reads each; these are rate-limited tax endpoints, not SLA paths.
- The filer export does 3 fleet-wide paginated reads plus the incentive-claim lookup, batched 150 IDs per request. That is fine for a once-a-year, rate-limited admin export.

**Known remaining divergences (NOT fixed here):**
1. The admin payouts-overview T4A YTD buckets (`routes/admin/rides.py` Pass 4, SQL function from migration 303) still count completed-ride earnings only. Fixing that needs a new migration. It is a dashboard count, not a slip or a filing.
2. For years, the slip and the year list have assigned rides to a year by `created_at` (from `get_rides_for_driver`), while the annual job and the filer use `ride_completed_at`. A ride created on Dec 31 and completed on Jan 1 can land in different years. Incentive claims follow their ride, so they inherit this edge.
3. `driver_statement.py` still has its own copy of these three queries, using the America/Regina timezone. It was not refactored onto `utils/t4a_income.py`.
4. T4A windows are UTC calendar years. The statement uses America/Regina, a 6-hour offset at the year boundary. This is unchanged.

## 5. User-experience effect

- **Drivers:** for any year with cancellation fees, bonuses or incentives, the T4A total on screen, in the PDF, in the CSV and in the email goes up to match their statements. The slip response has three new fields; the current PDF template does not render them, so the PDF shows only the corrected total. The tax-year list can gain years. From the Feb 2027 run on, the push notification amount includes this income. The change shows up on the driver's next request, not live mid-session. No notification copy changed.
- **Internal super-admins:** the T4A filer handoff export can list more drivers, with totals that include these streams and synced legacy payouts, and exclude legacy imported rides.
- **Riders, corporate:** no change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/t4a_income.py` | New shared fetch/sum helper for cancellation fees, `driver_bonuses`, incentive claims | One definition for all T4A sites; stops them drifting apart again |
| `backend/routes/drivers/tax_exports.py` | `get_t4a_summary` and `get_t4a_years` add the three streams; three new slice fields in the summary | The slip and year list were missing paid income |
| `backend/utils/t4a_annual_job.py` | `_driver_annual_earnings` adds the three streams | The $500 eligibility check has to use the same total as the slip |
| `backend/routes/admin/compliance.py` | Filer handoff adds the three streams, `order="id"` pagination, `EXCLUDE_LEGACY_RIDES`, settled legacy Stripe payouts | Fourth call site with the same gap; the CRA filer must get the slip's total |
| `backend/tests/test_t4a_annual_job.py` | 5 new tests (fees/bonuses only, incentives, bonus reversal, threshold crossing through real `_run_issuance`, error propagation) | Regression coverage |
| `backend/tests/test_p2_payout_t4a.py` | 3 new tests (slip with fees/bonuses only, slip incentives excluding legacy rides, year list offering a fee/bonus-only year) | Regression coverage |
| `backend/tests/test_compliance_reports_http.py` | 2 new tests (filer counts the three streams and qualifies a driver who is over $500 only with them; filer applies legacy exclusion and synced payouts) | Regression coverage |

## 7. Before / after

```
# Before — utils/t4a_annual_job.py::_driver_annual_earnings
    return ride_total + synced_total
```

```
# After
    cancelled, bonuses, claims = await fetch_supplementary_income(
        db_supabase, driver_id,
        f"{year}-01-01T00:00:00+00:00", f"{year + 1}-01-01T00:00:00+00:00",
        [r.get("id") for r in rides],
    )
    cancel_total, bonus_total, incentive_total = sum_supplementary_income(cancelled, bonuses, claims)
    return ride_total + synced_total + cancel_total + bonus_total + incentive_total
```

```
# Before — routes/drivers/tax_exports.py::get_t4a_summary
    total_earnings = _money_str(sum((_ride_income(r) for r in rides), Decimal("0")) + synced_earnings)
```

```
# After
    total_earnings = _money_str(
        sum((_ride_income(r) for r in rides), Decimal("0"))
        + synced_earnings + cancel_earnings + bonus_earnings + incentive_earnings
    )
```

```
# Before — routes/admin/compliance.py::_t4a_filer_handoff_rows
        {"status": "completed", "ride_completed_at": {"$gte": start, "$lt": end}},
    # ... total = completed-ride income only
```

```
# After
        {"status": "completed", "ride_completed_at": {"$gte": start, "$lt": end}, **EXCLUDE_LEGACY_RIDES},
        order="id",
    # ... + settled stripe_sync/legacy_outstanding_correction payouts
    # ... + cancellation fees + driver_bonuses + incentive claims
```

**Concrete scenario (dry run in `test_run_issuance_eligibility_uses_expanded_total`).** A driver has $400.00 of completed rides, a $50.00 no-show fee and a $60.00 quest bonus in 2025.
- **Before:** the total was $400.00, which is under $500. There was no push notification, and the slip read $400.00.
- **After:** the total is $510.00, which is at or over $500. The driver is pushed "Your 2025 T4A tax slip ($510.00 in earnings) is ready", and the slip reads $510.00, with `cancellation_fee_earnings` of 50.00 and `bonus_earnings` of 60.00.

## 8. Rollback plan

`git-revert-safe`. The change is read-only. It adds no writes, migration, wallet delta, Stripe call or stored state, so reverting the three commits and redeploying fully restores the previous totals, and there is no data to clean up. There is no feature flag, so rollback needs a redeploy (Fly primary and Railway standby both deploy from `main`).

The one thing a revert cannot undo: any slip a driver downloads, or filer export an admin pulls, while the new code is live keeps the new figure. That is the same "two different slips for one year" situation described in §4.

## 9. Verification performed

- [x] **Automated tests run.** Unit and e2e-marked, all mocked Supabase; command: `pytest --no-cov`.
  - The T4A and compliance suites passed after each change: `tests/test_t4a_annual_job.py`, `tests/test_p2_payout_t4a.py`, `tests/test_t4a_email.py`, `tests/test_driver_sin_collection.py`, `tests/test_tax_exports_app_name.py`, `tests/test_compliance_reports_http.py`, `tests/test_compliance_reports.py`, `tests/test_compliance_super_admin_mount.py`, and `tests/test_loguru_call_conventions.py`, which statically checks logger usage. Counts: 230 passed on the combined run before the review fixes; after those fixes, 172 passed (T4A + compliance-http suites) and 101 passed (all compliance suites).
  - The 8 new slip, year-list and annual-job tests were checked against the old code (`origin/main` versions of `t4a_annual_job.py` and `tax_exports.py`); all 8 **fail** there and pass with the fix.
  - The 2 new filer tests were **not** run against the old code; they are written to fail on the old totals and query filters.
  - `ruff check` and `ruff format --check` are clean on all 7 touched Python files, and the repo pre-commit hook (including the money-arithmetic check) passed on all three commits.
- [ ] Manual repro steps followed in staging. Not done: this runs in a sandbox with no staging access.
- [x] **Blast-radius grep performed:**
  - `t4a` / `T4A` across `backend/` (non-test) gave 36 files. Each hit that computes a T4A total was inspected: the slip, the year list, the annual job, the filer handoff, and the admin YTD SQL snapshot (the last one is flagged in §4, not fixed).
  - Also grepped: `cancellation_fee_driver`, `driver_bonuses`, `ride_incentive_claims`, `wallet_apply_delta` with `cancellation_fee`, and `get_t4a_summary` / `get_t4a_years` / `_driver_annual_earnings` / `_t4a_filer_handoff_rows` callers.
- [x] **Reviewed against CLAUDE.md conventions:** Decimal-only money math; the dual-import pattern in all three importing modules; no swallowed DB errors (the helper raises; the filer already maps errors to 503; the job's existing per-driver `logger.error` path is unchanged); query-filter rules (`$in` batched through `get_rows_batched_in`, `$gt` supported by `_apply_filters`); no PII in logs; no new logging.
- [ ] **Feature-flagged if user-visible and non-trivial.** Not flagged. Justification: this corrects under-reported, CRA-reportable income. Shipping it dark would keep publishing slips known to be wrong, and the change is read-only with a clean code-revert rollback (§8). **CLAUDE.md gate 3 asks for a flag for user-visible changes, so a human should explicitly accept this exception before merge.**
- **Adversarial review:** `/code-review` at high effort was run on the diff (the `spinr-money-auditor` agent could not be run: this environment has no Agent tool).
  - Fixed as a result: the filer's legacy-ride and synced-payout divergence (commit `d264912`), unstable filer pagination (`order="id"`), cancelled-ride reads now filtered to `cancellation_fee_driver > 0` (fewer rows, no risk of the 10k cap cutting off fee-bearing rows), and the cancellation and bonus reads now run concurrently.
  - Flagged, not fixed: items 1–4 in §4, and the feature-flag question above.

**What was NOT verified**
- Not tested against live or staging Supabase; mocked `get_rows` / `get_rows_batched_in` only. In particular, `cancellation_fee_driver` is a FLOAT column (migration 81), and the PostgREST behaviour of `{"$gt": 0}` on it was reasoned about, not executed.
- Did not check production data for how much past-year income is affected, or whether any T4A has already been filed or emailed for 2025. See the human action in §4.
- Did not verify that `rides.cancellation_fee_driver` always equals the wallet `cancellation_fee` credit. If `pay_driver_cancellation_fee` returns False (driver has no wallet row, for example), the column is still written, so the T4A, like the payout balance and the statement, would count a fee the wallet never received.
- A tax advisor has not confirmed CRA box treatment: whether bonuses and incentives belong in Box 048 alongside fees, and whether GST/PST belongs anywhere. This change is consistent with Spinr's own payable-income definition, not with a legal opinion.
- The PDF template (`utils/t4a_pdf.py`) does not render the new slice fields; only the total changes on the PDF. It was not visually checked, since there is no visual regression tooling for generated PDFs.
- No production build applies (backend only; no admin-dashboard, rider-app or driver-app change).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (read-only change; revert and redeploy)
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in

**Human decisions (2026-09-23, ittalenthire.ca@gmail.com):**
- **Prior filings:** confirmed no 2025-or-earlier T4A slip or CRA platform-operator return was already filed using the old, understated numbers. No amended-slip/correction process is needed; the forward-looking code fix is sufficient on its own.
- **Feature-flag exception (gate 3):** explicitly accepted. Ship without a flag — the change is read-only reporting logic with a clean, testable code-revert rollback (§8), consistent with the agent's own reasoning above.
