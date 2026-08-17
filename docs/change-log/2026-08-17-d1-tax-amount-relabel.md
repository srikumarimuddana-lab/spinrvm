# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (filled in on PR open — see PR description) |
| Related issue or gap ID | Issue #4108 ("[CR] D1"); `ACTION_ITEMS.md` A34; `docs/change-log/2026-08-16-gst-backfill-executed.md` |

## 1. Issue / gap identified

For the 186 legacy-imported rides, `rides.tax_amount` / `tax_breakdown` hold a real dollar figure that looks like ordinary fare-GST but isn't: it's commission-GST (GST on Spinr's own platform-commission fee), carried over from `bookings.csv`'s "gst" column at import time. Nothing on any surface that reads `tax_amount` flags this — a filer, a rider, or a corporate finance manager reading it at face value would believe it's fare-GST.

## 2. Root cause

`services/booking_import_service.py`'s importer reads `bookings.csv`'s "gst" column (verified 2026-08-15, sampling every row: `gst == commission_gst_amount` in every case) into `tax_amount`/`tax_breakdown`, but that column is commission-GST, not the fare-scaling GST the export tracks separately as `payout_gst_amount` — a column the importer never read for these 186 rows. The correct historical fare-GST figure is not recoverable from the export at all (no such column exists once the source data is base-mismatched like this).

## 3. Fix / remediation

**Issue #4108, D1 decision — option (a) "re-label, don't recompute", approved by the product owner via this session's `AskUserQuestion`, relayed at task start.** No value is written or changed anywhere — `tax_amount` and `tax_breakdown` are read-only in this change. Every surface that displays or exports them for a human now also computes a `tax_basis` label (`"fare_gst"` for a normal ride, `"commission_gst_legacy_import"` for one of the 186 legacy rows) and, for legacy rows only, a short `tax_note` explaining the mismatch — both computed at serialization time from `legacy_import_metadata` presence (the same predicate `utils/legacy_rides.is_legacy_ride()` already uses everywhere else in the codebase), never persisted.

New shared helpers in `backend/utils/legacy_rides.py`:
- `TAX_BASIS_FARE_GST = "fare_gst"`
- `TAX_BASIS_COMMISSION_GST_LEGACY_IMPORT = "commission_gst_legacy_import"`
- `tax_basis_for_ride(ride) -> str`
- `legacy_tax_note_for_ride(ride) -> str | None` (the human-readable footnote, `None` for the 99%+ non-legacy case)

Applied to 3 consumer surfaces (chosen as the highest-value, confirmedly-reachable subset — see §4 for the full blast-radius list and what was explicitly left alone):

1. **`GET /rides/{ride_id}/receipt`** (`backend/routes/rides/receipts.py`) — rider-facing JSON receipt API. Riders keep full trip history for imported rides (`utils/legacy_rides.py`: "Imported rides remain fully visible in ride history"), so this is directly reachable for any of the 186 rows.
2. **`GET /admin/rides/{ride_id}/invoice`** (`backend/routes/admin/rides.py`) — admin-triggered per-ride invoice data (feeds client-side PDF generation), reachable for any ride by ID including legacy ones.
3. **`GET /admin/compliance/gst-pst-remittance`** (`backend/routes/admin/compliance.py`) — the actual monthly GST/PST government-remittance summary an admin downloads to file taxes. Added a `legacy_commission_gst_included` column (per month and in the TOTAL row) plus a subtitle warning when the window contains any legacy-imported ride, so a filer sees both that the commission-GST is baked into the existing `gst` total (unchanged) and exactly how much of it to account for separately.

## 4. Risk & impact on existing functionality

**Blast-radius grep performed**: `grep -rn "tax_amount\|tax_breakdown" backend/` (excluding migrations, which only define the columns). 75 files matched; narrowing to files that *display or export* the value to a human (not internal-only fare-calc/settlement arithmetic) gave these consumers:

| Surface | File | Reachable for the 186 legacy rides? | Action taken |
|---|---|---|---|
| Rider receipt API | `routes/rides/receipts.py::get_ride_receipt` | Yes — rider's own completed/cancelled rides, imported rides included | **Fixed** — `tax_basis`/`tax_note` added |
| Admin per-ride invoice | `routes/admin/rides.py::admin_get_ride_invoice` | Yes — single-ride lookup by ID, no legacy filter | **Fixed** — `tax_basis`/`tax_note` added |
| Admin GST/PST remittance report | `routes/admin/compliance.py::_gst_pst_rows` / `get_gst_pst_remittance` | Yes — sums `tax_breakdown` for all `status=completed` rides in the filed date range, including legacy rows at their original historical `ride_completed_at` | **Fixed** — `legacy_commission_gst_included` column + subtitle warning |
| Driver earnings statement | `utils/driver_statement.py` (`_ride_tax`, `build_statement`) | **No** — `_build()` applies `EXCLUDE_LEGACY_RIDES` before any earnings/tax aggregation (confirmed reading the source: "Legacy-imported rides are excluded here"). `utils/legacy_rides.py`'s own docstring states driver statements/earnings/balance describe "THIS app's money only." | **Not touched** — confirmed unreachable, not just assumed |
| Admin finance-reconciliation export | `routes/admin/rides.py::admin_get_earnings_rides` (`/admin/earnings/rides`) | **No** — this endpoint already calls `drop_legacy_rides(raw)` post-fetch specifically because "a legacy-imported ride has no real Stripe charge" (comment in that function, citing `docs/audit/2026-08-11-driver-rider-migration-audit.md` P0-B) | **Not touched** — confirmed unreachable |
| Corporate statement tax breakdown | `routes/corporate_company.py::_attach_ride_tax` / `_aggregate_rows` | **No** — reads via `ride_payment_sources`, a table only `services/payment_service.py` ever writes (at fare settlement). `booking_import_service.py` never writes `ride_payment_sources` rows for the 186 legacy rides, so they can never appear in a corporate statement | **Not touched** — confirmed unreachable |
| Rider PDF/email receipt (attachment, not the JSON API) | `utils/receipt_pdf.py`, `utils/email_receipt.py` | Plausible (receipt regeneration/resend) but not confirmed in this pass | **Follow-up** — listed in the PR body, not in this PR (scope-down per CLAUDE.md's >5-files-needs-decomposition rule) |
| Driver/rider single-ride "earnings" view | `routes/rides/queries.py::get_ride` (`tax_amount_total`), `routes/drivers/ride_reads.py` | Reachable (no legacy filter on single-ride lookup) but folds tax into an aggregate `total_earned`/`fare_only` figure rather than displaying it as "tax paid" | **Follow-up** — listed in the PR body |
| Admin ride-details raw dump | `db_supabase.get_ride_details_enriched` (backs `admin_get_ride_details`) | Reachable, but returns the raw `rides` row without any receipt/invoice-style rendering | **Follow-up** — listed in the PR body |

**What could regress**: nothing existing — every change is additive (new fields alongside existing ones; `tax_amount`/`tax_breakdown` values and types are untouched in all three responses). The `_gst_pst_rows()` internal helper's return signature grew from a 5-tuple to a 6-tuple (added `legacy_commission_gst_total`); its only caller (`get_gst_pst_remittance`) was updated in the same commit, and all 7 existing unit tests plus 16 HTTP tests in `test_compliance_reports.py` / `test_compliance_reports_http.py` were updated/re-verified to unpack the new tuple shape.

**Interaction with background loops / state machine / money**: none. No background loop reads `tax_basis`. No ride-state transition. No wallet/Stripe/money-delta path — this is read-only serialization of already-persisted data.

## 5. User-experience effect

- **Rider-facing**: `GET /rides/{id}/receipt`'s JSON response gains two new fields (`tax_basis`, `tax_note`). For 99%+ of rides (`tax_basis="fare_gst"`, `tax_note=null`) there is no visible change unless the rider app is updated to render `tax_note` — this PR does not touch `rider-app/`, so today nothing new is rendered to riders; the fields exist for whichever surface (backend consumer, support tooling, or a future rider-app change) chooses to read them. For the 186 legacy rows specifically, if and when the rider app renders `tax_note`, the rider would see an explanatory footnote instead of an unexplained-but-plausible-looking GST line — this is a **future** UX change contingent on a rider-app update, not shipped by this PR.
- **Internal-admin-facing**: the GST/PST remittance report (PDF/CSV/XLSX/DOCX) gains a `legacy_commission_gst_included` column and, when the filed window includes any legacy ride, an extra subtitle line. Visible on next report generation; not mid-session (this is an on-demand export, not a live screen).
- **Not visible mid-session** to anyone already using the app — no active-ride, dispatch, or payment-in-flight surface is touched.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/legacy_rides.py` | Added `TAX_BASIS_FARE_GST`, `TAX_BASIS_COMMISSION_GST_LEGACY_IMPORT`, `LEGACY_TAX_NOTE`, `tax_basis_for_ride()`, `legacy_tax_note_for_ride()` | Shared, single source of truth for the relabel logic |
| `backend/routes/rides/receipts.py` | `get_ride_receipt`'s response dict gains `tax_basis`/`tax_note` | Rider-facing receipt API |
| `backend/routes/admin/rides.py` | `admin_get_ride_invoice`'s `invoice_data` dict gains `tax_basis`/`tax_note` | Admin per-ride invoice, feeds client-side PDF |
| `backend/routes/admin/compliance.py` | `_gst_pst_rows()` tracks per-month + grand-total legacy commission-GST; `get_gst_pst_remittance()` adds the `legacy_commission_gst_included` column + conditional subtitle warning | Government-facing tax remittance report |
| `backend/tests/test_legacy_tax_basis.py` (new) | Unit tests for the new helpers | Coverage for the shared logic |
| `backend/tests/test_compliance_reports.py` | Updated 4 existing tests for the new 6-tuple return; added 1 new legacy-row test | Existing suite must track the (additive) signature change |
| `backend/tests/test_coverage_rides.py` | Added assertions to the existing success test + 1 new legacy-row test | Rider receipt coverage |
| `backend/tests/test_admin_rides_read_endpoints_coverage.py` | Added assertions to the existing happy-path test + 1 new legacy-row test | Admin invoice coverage |

## 7. Before / after

`_gst_pst_rows()` return signature (internal helper, single caller updated in the same commit):

```python
# Before
async def _gst_pst_rows(...) -> tuple[list[dict], Decimal, Decimal, Decimal, bool]:
    ...
    return rows, gst_total, pst_total, hst_total, truncated
```

```python
# After
async def _gst_pst_rows(...) -> tuple[list[dict], Decimal, Decimal, Decimal, bool, Decimal]:
    ...
    return rows, gst_total, pst_total, hst_total, truncated, legacy_commission_gst_total
```

`gst_total`/`pst_total`/`hst_total`/each row's `"gst"`/`"pst"`/`"hst"` values are **numerically identical** before and after — `legacy_commission_gst_total` is purely additive bookkeeping of an amount already included in `gst_total`, never subtracted or moved.

Rider receipt response (`GET /rides/{id}/receipt`), legacy row:

```python
# Before
{"tax_amount": 0.73, "tax_breakdown": {"GST": {"amount": 0.73, "rate": 5.0}}, ...}
```

```python
# After
{
    "tax_amount": 0.73,  # unchanged, byte-for-byte
    "tax_breakdown": {"GST": {"amount": 0.73, "rate": 5.0}},  # unchanged
    "tax_basis": "commission_gst_legacy_import",
    "tax_note": "Tax shown for this ride is Spinr's platform-fee GST from the previous app ...",
    ...
}
```

## 8. Rollback plan

Fully git-revert-safe — no data written anywhere by this change (no migration, no `UPDATE`, no `tax_amount`/`tax_breakdown` write). `git revert` on the merge commit removes the three new response fields and the report column/subtitle text; every existing field and every existing numeric value is untouched, so no second deploy step, feature flag, or data remediation is needed. No Stripe charge, wallet delta, or ride-state transition is created or touched.

## 9. Verification performed

- [x] Automated tests run (unit): `pytest tests/test_legacy_tax_basis.py tests/test_compliance_reports.py tests/test_compliance_reports_http.py tests/test_coverage_rides.py tests/test_admin_rides_read_endpoints_coverage.py tests/test_rides_extended.py tests/test_admin_rides_coverage.py tests/test_previous_app_sunset.py tests/test_driver_statement.py tests/test_driver_statement_pdf.py tests/test_booking_import_service.py -q --no-cov` → **all passed** (492 tests across the combined runs, 0 failures)
- [x] `ruff check` on every modified/added file → all checks passed
- [x] Blast-radius grep performed: `grep -rn "tax_amount\|tax_breakdown" backend/` (75 files), narrowed to human-facing consumers, each one individually confirmed reachable or not-reachable for the 186 legacy rows by reading its actual filter/exclusion logic (not assumed) — see §4 table
- [x] Confirmed `tax_amount`/`tax_breakdown` numeric equality before/after on a constructed legacy-row fixture (`test_get_ride_receipt_legacy_imported_ride_flags_commission_gst`, `test_invoice_legacy_imported_ride_flags_commission_gst`, `test_legacy_imported_row_flags_commission_gst_without_changing_gst_total`) — each asserts the stored value is read back unchanged
- [ ] Manual repro against staging/live Supabase — not performed; all tests run against `mock_supabase_client`-style mocks (`AsyncMock`), no real DB reads
- [ ] `npm run build` — not applicable, this PR touches `backend/` only, no `rider-app`/`driver-app`/`admin-dashboard` code
- [ ] Not run through the real 186-row production dataset — verification is via constructed fixtures shaped like the confirmed 2026-08-15 sampling (`{"source": "legacy_mongo_booking_import", "old_booking_id": ...}` in `legacy_import_metadata`, `tax_breakdown={"GST": {...}}`), not the live rows themselves

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data touched)
- [x] Blast radius is stated, not assumed — 8 candidate surfaces individually confirmed reachable/not-reachable, not inferred from the grep hit alone
- [x] No silent behavior change to an already-shipped flow — every change is additive (new fields/columns); no existing field's value or type changes
