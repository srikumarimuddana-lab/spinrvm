# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, drivers |
| PR / commit link | https://github.com/srikumarimuddana-lab/spinrvm/pull/4759, commit `360f614` (+ Sentry-parity follow-up) |
| Related issue or gap ID | #4639 (findings 1 and 2) |

## 1. Issue / gap identified

Two independent correctness bugs found by the round-2 corporate/driver-reporting audit:

1. The driver GST-remittance statement (a document captioned "must be remitted to CRA by you as a registrant") collapsed GST and PST into a single combined tax line, with no way for a driver to tell how much of it was GST vs PST.
2. `list_company_ride_payment_sources` used an **inclusive** upper-bound filter (`.lte`) on a window (`_month_bounds()`) explicitly documented and treated everywhere else as **half-open** `[inclusive, exclusive)` — a ride whose `created_at` landed exactly at the first instant of the next month was counted into both months' company statement totals.

## 2. Root cause

1. `utils/driver_statement.py::_ride_tax()` only ever summed `rides.tax_amount`, never reading `rides.tax_breakdown` — the field the corporate statement PDF, rider receipt PDF, and email receipt all already use to render separate GST/PST lines. The 2026-08-19 corporate-statement fix that introduced this pattern never propagated to the driver-statement module.
2. `_month_bounds()` (`routes/corporate_company.py`) computes `to_iso` as the first instant of the *next* month (genuinely exclusive), but `list_company_ride_payment_sources` was written with `.lte()` instead of `.lt()` — a simple operator mismatch against the function's own documented contract.

## 3. Fix / remediation

1. Added `_ride_tax_by_type()` (mirrors `routes/corporate_company.py::_aggregate_rows`'s existing per-type aggregation exactly), summed it into the statement's `earnings.tax_by_type` dict, and updated `driver_statement_pdf.py` to render separate GST/PST lines when available — falling back to the combined line (with a loud error-log + Sentry flag, silent when tax is genuinely zero) only when the breakdown is genuinely absent, e.g. legacy rows. The Sentry capture mirrors `utils/corporate_statement_pdf.py`'s identical fallback flag for parity (added in a follow-up commit after `spinr-corporate-reporting-reviewer` caught the initial omission).
2. Changed `repositories/corporate_repo.py::list_company_ride_payment_sources`'s `to_iso` filter from `.lte()` to `.lt()`.

## 4. Risk & impact on existing functionality

- **Other readers/writers**: `list_company_ride_payment_sources` is called from exactly four sites, all in `routes/corporate_company.py` (`billing_summary`, `billing_statement`, its PDF variant, and the full-month aggregation helper) — all four derive `from_iso`/`to_iso` from the same `_month_bounds()` call, which already treats `to_iso` as exclusive. No caller relies on the old inclusive semantics; grepped and confirmed no other caller exists repo-wide.
- **Could this regress a working flow?** The month-boundary fix can only ever *decrease* a company's per-month ride count by at most the handful of rides (realistically 0–1 per company per month) whose `created_at` lands at the exact microsecond of a month rollover — it moves such a ride from "counted in both months" to "counted in the later month only" (the month `gte` on the next window actually owns it). The driver-statement change is additive display only — no totals change, only how the existing `tax_collected` figure is broken down visually.
- **Blast radius**: isolated. The tax-breakdown change touches only `utils/driver_statement.py`/`driver_statement_pdf.py`. The month-boundary change touches only the one filter in `corporate_repo.py`.
- **Background loops / state machine / money**: no interaction — both are read-path query/rendering fixes, not writes.
- **Historical/already-issued statements**: neither fix retroactively touches any already-generated PDF or already-sent statement email — both apply only to statements generated *after* this deploy. A corporate admin who downloaded a prior month's statement before this fix keeps whatever total that document showed; this fix does not reissue or invalidate it. **Reissuing historical statements affected by the pre-fix `.lte` double-count is explicitly out of scope for this change** — no backfill or reissue job is included. If a company disputes a past total, that is a support/finance follow-up, not something this commit automates.

## 5. User-experience effect

- **Driver-facing**: a driver's next earnings statement (weekly/monthly, whichever period type they're on) will show separate "GST collected on fares" / "PST collected on fares" lines instead of one combined "GST/PST collected on fares" line, whenever the underlying rides have a `tax_breakdown` populated (all rides going forward; legacy rows without it still show the combined line, unchanged from before). This is visible on the next document generated, not mid-session to someone already viewing an existing document — statements are generated fresh per request/scheduled job, not live-updated.
- **Corporate-admin-facing**: a company statement generated after this deploy for a month containing a boundary-timestamp ride will show a total 1 ride-transaction lower than an identical query would have shown before this fix (the ride now counts in the following month's statement instead of both). This is a correction, not a new behavior a company opted into — no notification is sent about the change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/driver_statement.py` | Added `_ride_tax_by_type()`; aggregated into `earnings.tax_by_type` | Surface per-type tax breakdown, same field the corporate/receipt PDFs already use |
| `backend/utils/driver_statement_pdf.py` | Render separate GST/PST lines when available; combined-line fallback with loud log + Sentry capture | Match `corporate_statement_pdf.py`'s existing pattern for parity |
| `backend/repositories/corporate_repo.py` | `list_company_ride_payment_sources`'s `to_iso` filter: `.lte` → `.lt` | Match `_month_bounds()`'s documented half-open contract |
| `backend/tests/test_driver_statement.py`, `test_driver_statement_pdf.py`, `test_corporate_repo_coverage.py` | New/updated regression tests | Prove both fixes and their fallback paths |

## 7. Before / after

```python
# Before (repositories/corporate_repo.py)
if to_iso:
    q = q.lte("created_at", to_iso)

# After
if to_iso:
    q = q.lt("created_at", to_iso)
```

```python
# Before (driver_statement_pdf.py earnings breakdown)
line_item("GST/PST collected on fares", money("tax_collected"))

# After
tax_by_type = earnings.get("tax_by_type") or {}
if isinstance(tax_by_type, dict) and tax_by_type:
    for label, amount in tax_by_type.items():
        line_item(f"{label} collected on fares", str(amount))
else:
    line_item("GST/PST collected on fares", money("tax_collected"))  # + loud fallback log
```

## 8. Rollback plan

`git revert` is sufficient and complete for both fixes: neither writes data, runs a migration, or has any downstream consumer that would be left in an inconsistent state by a revert. Reverting restores the prior (inclusive-boundary, combined-tax-line) behavior exactly. No feature flag exists or is needed — both are narrow, low-blast-radius correctness fixes to read/render paths.

## 9. Verification performed

- [x] Automated tests run (unit) — `test_driver_statement.py` (17 tests incl. 2 new), `test_driver_statement_pdf.py` (11 tests incl. 3 new), `test_corporate_repo_coverage.py` (102 tests incl. 1 updated) all pass. Broader sweep (`-k "corporate or driver_statement"`, 1023 tests) passes.
- [ ] Manual repro steps followed in staging — not performed; no staging/live Supabase access from this sandbox.
- [x] Blast-radius grep performed — confirmed all 4 callers of `list_company_ride_payment_sources` use the same half-open `_month_bounds()` contract; confirmed no other file duplicates `_ride_tax()`'s logic outside the documented duplication with `routes/drivers/_shared.py::_ride_income` (unrelated field).
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — money (Decimal-only via `_d`), corporate tenant scoping (unaffected — `company_id` filter untouched), observability (Sentry tags `domain=corporate`/`drivers`, `surface=backend`). Independently reviewed by `spinr-corporate-reporting-reviewer`: verified no cross-tenant issue, verified the `.lte`/`.lt` semantics against `_month_bounds()`'s actual computation, and flagged the initial Sentry-parity gap (closed in a follow-up commit) and this Change Impact Log requirement (this document).
- [ ] Feature-flagged — not applicable; see Section 4 rollback discussion.

## What was NOT verified

- Not exercised against real production/staging data — no live Supabase access from this sandbox.
- Whether any real corporate company has ever actually had a ride land at the exact month-boundary microsecond that would have triggered the old double-count bug in production — not investigated; the fix is correct regardless of historical incidence, but the actual historical financial impact (if any) is unquantified.
- Whether any already-issued statement, invoice, or downstream accounting record for an affected company needs manual correction — explicitly out of scope for this change (see Section 4); flagging for the account owner to decide whether a finance-side review is warranted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-level unwind needed)
- [x] Blast radius is stated, not assumed (isolated to the two named files/functions)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — see Section 5 above
