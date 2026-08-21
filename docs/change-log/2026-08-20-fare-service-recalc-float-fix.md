# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | vikas@ngitservices.com (via Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (local worktree — not yet pushed/PR'd) |
| Related issue or gap ID | ACTION_ITEMS.md B36 (adjacent finding surfaced by B35, PR #4312) |

## 1. Issue / gap identified

`backend/services/fare_service.py`'s `recalculate_fare_for_distance` — the fare recompute
that runs on ride completion when actual trip distance differs meaningfully (>0.1 km) from
the booking-time estimate — wrote `grand_total` into the `rides` table as a Python `float`
(`_f(new_grand_total)`) instead of this repo's mandated Decimal-safe string. `grand_total` is
`NUMERIC(10,2)` on `rides`, so a raw float write bypasses the Decimal-only money convention
(CLAUDE.md § Critical Conventions) and risks IEEE-754 precision loss reaching Postgres.

## 2. Root cause

`recalculate_fare_for_distance` predates this repo's `_money_str`/NUMERIC-vs-FLOAT8 audit
work (B28–B35). It uses this file's own `_f()` helper — a plain `float(Decimal)` cast — for
every field in its return dict, without differentiating which of the five `rides` columns it
writes are actually `NUMERIC` vs genuinely `FLOAT8`. Same bug class/root cause as B28–B30/B35:
a Decimal value computed correctly in-memory, then serialized incorrectly at the DB-write
boundary.

## 3. Fix / remediation

Verified the real column types live (see §4/§9), then changed only the `grand_total` line:
`_f(new_grand_total)` → `_money_str(new_grand_total)`, using a new local `_money_str` helper
added to `fare_service.py` (mirrors `routes/rides/_shared.py`'s helper of the same name —
not imported directly, since `_shared.py` imports `_deps`, which imports back into
`fare_service.py`, so a direct import would be circular). The other four fields the function
writes (`distance_km`, `distance_fare`, `total_fare`, `driver_earnings`) were verified to be
genuinely `FLOAT8` and were left untouched — `_f()` is correct for them.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one write site.** Only `recalculate_fare_for_distance`'s
  `grand_total` key changed; every other field/function in `fare_service.py` is untouched.
- **Caller:** `backend/routes/drivers/ride_complete.py` (`recalculate_fare_for_distance(ride,
  actual_distance_km)` at line 547) merges the returned dict directly into `update_fields`,
  which is written via `db_supabase.update_one("rides", _complete_filters, update_fields)` at
  ride completion. This is the only production call site.
- **Every reader of `rides.grand_total`** was grepped (36 call sites across
  `backend/ai/`, `backend/services/`, `backend/utils/`, `backend/routes/`). All of them read it
  via `_d(...)`/`Decimal(str(...))`-style safe parsing (e.g. `payment_service.py`,
  `stripe_reconcile.py`, `payment_retry.py`, `email_receipt.py`, `receipt_pdf.py`,
  `scheduled_rides.py`, `webhooks.py`, `routes/rides/booking.py`, `routes/rides/payments.py`,
  `routes/admin/rides.py`), which already handles both a Python `float` and a `str` input
  identically — this matches the pattern B28–B35 already relied on for the same reasoning.
  No caller was found that assumes `grand_total` is specifically a `float` in a way this
  change would break.
- One unrelated pre-existing float-write of `grand_total` was found in
  `routes/rides/_shared.py` (`result["fare_breakdown_snapshot"]["grand_total"] = float(...)`,
  line ~436) — this writes into the `fare_breakdown_snapshot` JSONB sub-field, not the scalar
  `rides.grand_total` NUMERIC column, so it is out of scope for this fix (same JSONB carve-out
  B30 already documented) and was left untouched.

## 5. User-experience effect

None visible to rider/driver/admin. This is a DB-write serialization fix only; the numeric
value written is unchanged (same Decimal math, same rounding), only its wire representation
changes from a float to an equivalent Decimal-safe string.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/fare_service.py` | Added `_money_str()` helper; changed `recalculate_fare_for_distance`'s `"grand_total": _f(new_grand_total)` to `"grand_total": _money_str(new_grand_total)` | `grand_total` is `NUMERIC(10,2)` on `rides`; `_f()` writes a float, bypassing the Decimal-only money convention |
| `backend/tests/test_fare_service_recalc_rides_numeric_no_float_cast.py` (new) | Static-scan + runtime tests pinning `grand_total` to `_money_str()`/`str` and the other four fields to `_f()`/`float` | Regression coverage, same pattern as B28–B30/B35's `test_*_numeric_no_float_cast.py` suites |
| `backend/tests/services/test_fare_service.py` | Updated `test_area_fees_breakdown_fallback_sums_when_total_not_set`'s assertion from `out["grand_total"] == 11.50` to `== "11.50"` | Pre-existing test asserted the old (buggy) float type; updated to match the corrected string convention |
| `ACTION_ITEMS.md` | Filed B36, closed with this fix's summary | Tracking |

## 7. Before / after

```python
# Before
return {
    "distance_km": round(actual_distance_km, 2),
    "distance_fare": _f(new_distance_fare),
    "total_fare": _f(new_total_fare),
    "driver_earnings": _f(new_driver_earnings),
    "grand_total": _f(new_grand_total),
}
```

```python
# After
return {
    "distance_km": round(actual_distance_km, 2),
    "distance_fare": _f(new_distance_fare),
    "total_fare": _f(new_total_fare),
    "driver_earnings": _f(new_driver_earnings),
    "grand_total": _money_str(new_grand_total),
}
```

## 8. Rollback plan

Pure code revert — no data migration involved. `git revert` this commit (or restore the
single line) and redeploy; no Stripe charges, wallet deltas, or ride-state rows are touched
by this change, so no data-level remediation is needed either way. If reverted, existing rows
already written with the corrected string value remain valid (Postgres accepts a numeric
string as NUMERIC input regardless of which code path wrote it).

## 9. Verification performed

- [x] Automated tests run (unit): `pytest backend/tests/services/test_fare_service.py
  backend/tests/test_fare_service_recalc_rides_numeric_no_float_cast.py -q` → 50 passed.
  Also ran the sibling B28–B35 suites (`test_ride_complete_coverage.py`,
  `test_booking_rides_numeric_no_float_cast.py`,
  `test_booking_import_rides_numeric_no_float_cast.py`,
  `test_shared_rides_numeric_no_float_cast.py`) → 97 passed. Also ran the
  payments/webhooks/receipt/preauth surface (`-k "grand_total or payment_service or webhooks
  or ride_preauth"`) → 292 passed, 1 skipped.
- [x] Live schema check: `information_schema.columns` on `rides` (Supabase project
  `soavhtdhefowwvforzwb`, `ca-central-1`) for every field this function writes —
  `distance_km`/`distance_fare`/`total_fare`/`driver_earnings` = `double precision`;
  `grand_total` = `numeric(10,2)`. Matches B35's independently-verified finding for the same
  table's sibling fields.
- [x] Regression test verified to actually catch the bug: reverted the fix locally
  (`grand_total": _f(...)`), confirmed 3 of the new tests fail with the expected assertion
  message, restored the fix, confirmed all pass again.
- [x] Dry run against a concrete before/after scenario: `recalculate_fare_for_distance` with
  `distance_fare=15.00, time_fare=5.00, distance_km=10, total_fare=25.50,
  area_fees_total=Decimal("0.03")`, `actual_distance_km=8` → `grand_total` now returns the
  string `"22.53"` (`isinstance(..., str)` asserted) instead of the float `22.53`; the three
  genuinely-FLOAT8 fields (`distance_fare`, `total_fare`, `driver_earnings`) still return
  `float` as asserted by `test_float8_fields_still_returned_as_float`.
- [x] Blast-radius grep performed (list what was searched): every `.get("grand_total"...)` /
  `["grand_total"]` read across `backend/` (36 call sites, see §4).
- [x] `ruff check` / `ruff format --check` on the three changed/added files: clean.
- [ ] Full `pytest` suite (entire `backend/tests/`) — run in background during this session;
  not confirmed complete before this doc was written (see report for final status).
- [x] Reviewed against relevant CLAUDE.md convention(s): Money arithmetic (Decimal-only),
  "Do not silently swallow errors" (not applicable — no error handling changed), Change
  Impact Log itself.
- [ ] Feature-flagged: not applicable — this is a backend-only serialization correctness fix
  with no behavior/UX change, isolated to one write of one column.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain code revert, no data migration).
- [x] Blast radius is stated, not assumed (36 reader call sites grepped and characterized).
- [x] No silent behavior change to an already-shipped flow — the numeric value written is
  unchanged; only its wire type (float → Decimal-safe string) changes, and every reader
  already parses either representation identically.

## What was NOT verified

- Not exercised against a real Supabase write (staging or otherwise) — verified via
  `mock_supabase_client`-based unit tests and a direct `information_schema.columns` read
  against the live production schema, not an actual end-to-end completed-ride write.
- Did not run the full `backend/tests/` suite to completion before writing this report (see
  final session report for whether it completed and its result).
- Did not investigate or fix the unrelated `fare_breakdown_snapshot` JSONB
  `"grand_total": float(...)` write in `routes/rides/_shared.py` (line ~436) — out of scope
  per the JSONB carve-out B30 already established; flagged here for visibility only, not
  filed as a new item.
