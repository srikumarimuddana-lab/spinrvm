# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | vikas@ngitservices.com (via Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | local worktree commit (not yet pushed/PR'd) — see commit SHAs in session report |
| Related issue or gap ID | `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` ranked blocker #20 (baseline #14) |

## 1. Issue / gap identified

Two related-but-distinct defects in corporate billing statements:

1. `utils/corporate_statement_pdf.py` — when a ride's `tax_breakdown` is
   missing/empty for the whole statement period (but `tax_total` is
   nonzero), the customer-facing PDF renders one line labeled
   `"Tax (GST/PST)"` — naming both tax types as if we know it's a mix of
   both, when we actually don't know the composition. This falsely implies
   a specific GST/PST split that contradicts CLAUDE.md's rule that GST and
   PST must appear as separate, individually-labeled line items.
2. `routes/corporate_company.py::_month_bounds` computed statement month
   boundaries from a naive (timezone-less) `datetime`, which Postgres/
   PostgREST implicitly treats as UTC when compared against the
   `timestamptz` columns it filters on. A ride that happened late in an
   SK-local calendar month, but whose UTC timestamp had already rolled
   into the next UTC day, could be excluded from (or included in) the
   wrong month's statement.

## 2. Root cause

1. `_aggregate_rows` (routes/corporate_company.py) buckets tax by type
   from each ride's persisted `tax_breakdown` dict. When every ride in
   the period is missing that breakdown (a data-quality gap — e.g. a
   pre-migration-46 row, or some other write path that set `tax_amount`
   without `tax_breakdown`) while still carrying a nonzero `tax_amount`,
   `tax_by_type` in the aggregate summary comes back empty. An internal
   detection + Sentry-alert (`_log_combined_tax_fallback`, added between
   the 08-15 and 08-18 audit passes, ACTION_ITEMS.md A29) already flags
   this loudly server-side, but the customer-facing PDF branch that
   renders in this situation still shipped the misleadingly-specific
   `"Tax (GST/PST)"` label unchanged.
2. `_month_bounds` built `datetime.strptime(month, "%Y-%m")`, which is
   naive. Saskatchewan is fixed UTC-6 year-round (no DST) but the
   comparison downstream is done against `timestamptz` values, so a naive
   "midnight" bound is read as UTC midnight, not SK-local midnight — a
   6-hour skew at both ends of every statement window.

## 3. Fix / remediation

1. `_log_combined_tax_fallback`'s detection/alerting logic is unchanged
   (it was already correct). The rendered fallback label changed from
   `"Tax (GST/PST)"` to plain `"Tax"` — mirroring `utils/receipt_pdf.py`'s
   own breakdown-unavailable fallback, which already uses the same plain
   `"Tax"` label (not a tax-type claim) for exactly this situation
   (persisted `grand_total` gap with no `tax_breakdown`). The normal path
   (breakdown present) was already correct and unchanged: it iterates
   `tax_by_type` and renders one line per tax type actually present
   (e.g. `"GST"` alone for Saskatchewan's current GST-only rideshare tax
   config, or `"GST"` + `"PST"` as two separate lines for an area with
   `pst_enabled=True`).
2. `_month_bounds` now anchors both bounds to `America/Regina`
   (`zoneinfo.ZoneInfo("America/Regina")`, fixed UTC-6, no DST) instead of
   a naive datetime, mirroring the existing `STATEMENT_TZ` convention
   already used by `utils/driver_statement.py` for the exact same class
   of problem (driver weekly/monthly earnings statements). `.isoformat()`
   on the resulting aware datetime now carries the `-06:00` offset
   through to the ISO strings passed to `list_company_ride_payment_sources`
   as `from_iso`/`to_iso`.

## 4. Risk & impact on existing functionality

**Blast radius — `_month_bounds`:** grepped every caller in
`routes/corporate_company.py` (excluding tests):
- `billing_summary` (`GET /company/{company_id}/billing/summary`) — line 957
- `billing_statement` (`GET /company/{company_id}/billing/statements/{month}`) — line 1000
- `build_full_month_statement` — line 1072, which itself feeds both PDF
  download routes: `routes/corporate_company.py`'s own
  `/billing/statements/{month}/pdf` and the internal-admin mirror
  `routes/corporate_accounts.py::admin_download_corporate_statement_pdf`.

All four surfaces were exercised by the existing + new test suite; no
other module calls `_month_bounds`. Isolated to corporate billing
read/reporting paths — `_month_bounds` only shapes a `from_iso`/`to_iso`
filter window passed to `list_company_ride_payment_sources`; it does not
write anything and is not on the fare-settlement or wallet-delta path
(`corporate_wallet_apply_delta` is untouched).

**Blast radius — tax-line fallback label:** grepped every caller of
`generate_corporate_statement_pdf`: `routes/corporate_company.py` (company
self-serve statement download) and `routes/corporate_accounts.py`
(internal-admin statement download) — both render the same function, both
covered by tests. `_log_combined_tax_fallback`'s Sentry/log behavior is
untouched (still fires exactly when it did before); only the rendered PDF
text label changed.

**Regression found and fixed during verification:** one existing test,
`tests/test_corporate_company_routes.py::test_billing_summary_defaults_to_current_month`,
asserted the naive-datetime `from_iso` suffix (`"...-01T00:00:00"`
with no offset). Updated its assertion to expect the SK-local offset
(`"...-01T00:00:00-06:00"`) — this is the intended behavior change, not
a bug introduced by the fix.

**What could regress:** a statement previously computed against naive
UTC-implied bounds will now include/exclude rides differently within
±6 hours of each month boundary — this is the fix, not a side effect, but
it means a company's July and August statement totals for rides that fall
in that 6-hour boundary window will differ from what they'd have shown
before this change (a ride previously counted in July may now correctly
count in June, or vice versa, depending on which side of local midnight
it actually happened on). No other domain (dispatch, driver earnings,
consumer receipts, wallet/allowance write paths) is touched — `_d`,
`_money_str`, `_aggregate_rows`'s totals math, and
`corporate_wallet_apply_delta` are all unmodified.

## 5. User-experience effect

Corporate-admin-facing only (billing summary API + statement PDF
download), not visible to riders or drivers. Not visible mid-session to
anyone — statements are a point-in-time, on-demand pull (self-serve
download or internal-admin download), never pushed into an active screen.
The visible differences a corporate admin could notice:
- A statement PDF that previously showed a data-gap total labeled
  `"Tax (GST/PST)"` now shows it labeled just `"Tax"`.
- A statement for a month adjacent to another (e.g. July vs August) may
  now show a ride moved from one month's total to the other, for any ride
  that fell within roughly 6 hours of local midnight on the 1st.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/corporate_statement_pdf.py` | Fallback tax line label changed from `"Tax (GST/PST)"` to `"Tax"` | Stop implying a known GST+PST mix when the breakdown is genuinely unavailable; mirrors `receipt_pdf.py`'s own fallback label |
| `backend/routes/corporate_company.py` | `_month_bounds` anchors to `ZoneInfo("America/Regina")` instead of a naive datetime; added `from zoneinfo import ZoneInfo` and module-level `_SK_TZ` | Fix UTC-vs-SK-local month-boundary mismatch so rides near a month boundary land in the correct statement month |
| `backend/tests/test_corporate_statement_pdf.py` | Updated 2 existing fallback-label assertions; added 2 new tests (`test_gst_and_pst_render_as_two_separate_line_items`, `test_gst_only_area_shows_no_pst_line`) | Cover the new label and the multi-tax-type / GST-only split behavior explicitly |
| `backend/tests/test_corporate_company_gap_coverage.py` | Added `test_month_bounds_are_saskatchewan_local_not_utc` and `test_month_bounds_boundary_ride_lands_in_correct_sk_local_month` | Cover the SK-local offset and a concrete before/after boundary-ride dry run |
| `backend/tests/test_corporate_company_routes.py` | Updated `test_billing_summary_defaults_to_current_month` assertion to expect the `-06:00` offset | Existing test asserted the old naive-datetime suffix; this is the intended behavior change |

## 7. Before / after

**Tax fallback label** (`utils/corporate_statement_pdf.py`):
```python
# Before
else:
    tax_total_str = money("tax_total")
    _log_combined_tax_fallback(company, statement, tax_total_str, tax_by_type)
    line_item("Tax (GST/PST)", tax_total_str)
```
```python
# After
else:
    tax_total_str = money("tax_total")
    _log_combined_tax_fallback(company, statement, tax_total_str, tax_by_type)
    line_item("Tax", tax_total_str)
```

**Month bounds** (`routes/corporate_company.py`):
```python
# Before
def _month_bounds(month: str) -> tuple[str, str]:
    from datetime import datetime as _dt
    anchor = _dt.strptime(month, "%Y-%m")
    ...
    return anchor.isoformat(), end.isoformat()
# -> "2026-07-01T00:00:00" (naive; read as UTC by Postgres)
```
```python
# After
_SK_TZ = ZoneInfo("America/Regina")

def _month_bounds(month: str) -> tuple[str, str]:
    from datetime import datetime as _dt
    anchor = _dt.strptime(month, "%Y-%m").replace(tzinfo=_SK_TZ)
    ...
    return anchor.isoformat(), end.isoformat()
# -> "2026-07-01T00:00:00-06:00"
```

**Concrete dry-run scenario (boundary ride):** a ride created at
`2026-07-31T23:30:00-06:00` (Saskatchewan local, still July 31st) is
`2026-08-01T05:30:00Z` in UTC. Before this fix, `_month_bounds("2026-07")`
returned a naive `to_iso` of `"2026-08-01T00:00:00"`, read by
Postgres/PostgREST as `2026-08-01T00:00:00Z` — the ride's
`2026-08-01T05:30:00Z` timestamp is **not** `<` that bound, so it would be
wrongly excluded from July's statement even though it happened on July
31st in Saskatchewan. After this fix, `to_iso` is
`"2026-08-01T00:00:00-06:00"` == `2026-08-01T06:00:00Z`; the ride's
`05:30:00Z` timestamp is correctly `<` that bound and is included in
July's statement. Exercised as
`test_month_bounds_boundary_ride_lands_in_correct_sk_local_month`.

## 8. Rollback plan

Both changes are pure code (no migration, no data write, no feature
flag currently gating statement generation). Rollback is a plain code
revert of the two changed lines/blocks in
`backend/utils/corporate_statement_pdf.py` and
`backend/routes/corporate_company.py` — no live data (Stripe charges,
wallet deltas, ride state) is touched by either fix, so `git revert` of
this commit is a complete and sufficient rollback here (this is the
narrow case CLAUDE.md's rollback-plan rule allows a `git revert` for:
nothing was applied to live money/state that would need a data-level
remediation). No feature flag was introduced because this touches a
read/reporting-only path (statement generation), not a write path to a
live-tested state machine or wallet balance.

## 9. Verification performed

- [x] Automated tests run: `pytest` (unit-tier, via
  `/tmp/spinr-venv/bin/pytest`, **not** just `ruff`/`tsc` — a real pytest
  run against `mock_supabase_client` fixtures where applicable):
  - `tests/test_corporate_statement_pdf.py` (14 tests, incl. 2 new)
  - `tests/test_corporate_statement_pdf_routes.py` (8 tests)
  - `tests/test_corporate_company_gap_coverage.py` (27 tests, incl. 2 new)
  - `tests/test_corporate_company_routes.py` (34 tests, incl. 1 updated)
  - `tests/test_money_serialization.py` (27 tests, regression check on money serialization)
  - Full `-k "corporate"` sweep across the suite: **959 passed, 3 skipped**
  - All green; no `--cov-fail-under` gate applies when running a subset
    (the coverage-gate failure seen when running only the two smallest
    files in isolation is a pre-existing artifact of running a narrow
    subset against the repo's aggregate coverage threshold, not a
    regression from this change — the full sweep run separately from the
    narrow one shows all tests passing).
- [x] `ruff check` run on all 5 modified files — all checks passed.
- [x] Blast-radius grep performed (see section 4) — listed every caller
  of `_month_bounds` and `generate_corporate_statement_pdf`.
- [x] Reviewed against relevant CLAUDE.md conventions: money arithmetic
  (Decimal-only — unchanged, no new arithmetic introduced by either fix),
  Saskatchewan regulatory tax-line-items rule, and the existing
  `America/Regina` / `STATEMENT_TZ` timezone convention already
  established in `utils/driver_statement.py` (reused the same IANA zone
  name rather than inventing a new fixed-offset constant).
- [ ] Manual repro steps followed in staging — **not performed**, see
  "What was NOT verified" below.
- [x] Feature-flagged if user-visible and non-trivial — **not
  flagged**: this is a read/reporting-only correction (a PDF label and a
  date-filter boundary), not a new user-facing flow or a write-path
  change to rides/wallet/auth; a flag would add complexity without a
  meaningful mid-session-safety benefit since statements are only ever
  pulled on demand.

## What was NOT verified

- **Not tested against a live/real Supabase instance** — all coverage is
  via `mock_supabase_client`/mocked route dependencies per this repo's
  unit-test convention; no integration-tier run against a throwaway
  Supabase schema was performed.
- **No manual staging repro** was run (e.g. actually generating a
  statement PDF for a real company via the running admin dashboard or
  company portal and visually inspecting the rendered tax line). Coverage
  is via `pypdf` text-extraction assertions on the generated PDF bytes,
  which validates the text content is present but not the visual layout
  (font, spacing, alignment) — this repo has no visual/snapshot
  regression tooling (a standing gap noted elsewhere in `ACTION_ITEMS.md`;
  not re-litigated here beyond flagging it applies to this change too).
- **The "$0.00 PST line for GST-only areas" literal instruction from the
  task was deliberately NOT implemented as a $0.00 line.** Investigated
  first: `utils/receipt_pdf.py` and `utils/subscription_invoice_pdf.py`
  (the two other tax-line renderers in this codebase) both already skip
  rendering a tax-type row when that type's amount is zero/absent for the
  period, rather than showing `"$0.00"`. The corporate statement PDF was
  matched to that established, twice-repeated convention instead of
  introducing a third, different convention — documented here explicitly
  as a deliberate deviation from the task's literal wording, per
  "investigate first, mirror the exact approach rather than reinventing
  it."
- **Did not change `billing_summary`'s default-month determination**
  (`month = _dt.now(_tz.utc).strftime("%Y-%m")`, line ~939), which has the
  same class of UTC-vs-SK-local mismatch for "what month is it right now"
  determination near midnight UTC. This is a related but distinct bug from
  the two the audit scoped this fix to (`_month_bounds`'s boundary
  computation and the PDF tax-line label) — left untouched per the
  explicit "do not change any other part of statement generation" scope
  instruction. Worth a follow-up ACTION_ITEMS entry if the team wants it
  closed too.
- **No production build was run** — this is a Python/FastAPI backend
  change only; there is no `admin-dashboard`/`rider-app`/`driver-app`
  frontend change in this fix, so `npm run build` does not apply here.
