# Change Impact & Risk Log: float money quick fixes (ROADMAP N19)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (agent session), for ROADMAP N19 |
| Surface(s) | backend (plus a CI rule in `.semgrep/spinr-rules.yml`) |
| Domain (Sentry tag) | payments / drivers |
| PR / commit link | branch `claude/fix-float-money-writes`: `0596d10` (MONEY-006), `9b511d0` (MONEY-001), `43e940b` (SR-03 extension). No PR opened yet |
| Related issue or gap ID | ROADMAP N19. Cards MONEY-006, MONEY-001, MONEY-008 and MONEY-005 (`docs/audit/clean-sheet/02-findings/money-cra.md`, `rapid-baseline-2026-09-24/A5-money-ledger-cra.md`). Also `research/session-2-pricing-payments-cra.md` §3 "Now" item 4 |

## 1. Issue / gap identified

Four audit cards said that money was added up as binary floats outside the CI money rule (SR-03). I checked each one against the code at `origin/main` (`9e27915`):

| Card | Cited site | Result at HEAD |
|---|---|---|
| MONEY-006 | `features.py:900`, `grand_total = round(subtotal + fees_total + tax_amount, 2)` | **Confirmed** (now at line 915). Three floats are added and then rounded with Python's `round()`, which uses banker's rounding. |
| MONEY-001 | `earnings.py:490,964`, tip totals built with `sum()` over raw floats | **Confirmed.** The check also found **three more sites the card had marked as already wrapped**, and they were not: `/earnings/daily`, `/weekly` and `/monthly` (the rides-table paths) each did `tips += r["tip_amount"]` on raw floats. |
| MONEY-008 | `referral_payout.py:876,941`, `"amount": _f(amount)` writing a float into `driver_bonuses.amount NUMERIC(10,2)` | **Already fixed / not a bug. Dropped.** The module-local `_f` in `utils/referral_payout.py` (line 152) is `def _f(v: Decimal) -> str: return str(v)`, which returns a string, not a float. The audit took it for `fare_service._f`. `test_referral_payout_credit.py` already asserts `doc["amount"] == "50.00"` (a str with 2 dp), and that was the card's own condition for closing it. No code change. |
| MONEY-005 | SR-03 does not cover `earnings.py` (and, per N19, `features.py` and `referral_payout.py`) | **Confirmed.** Fixed by the SR-03 extension below. |

Money drift you could actually see before the fix:
- `/drivers/earnings/daily|weekly|monthly` returned `"tips": 0.9999999999999999` for ten $0.10 tips. The float `+=` loop drifts on every Python version.
- `/drivers/earnings/comparison` `tips` drifted the same way on Python < 3.12. On 3.12, which production (`backend/Dockerfile` `python:3.12.13`) and CI run, `sum()` uses compensated summation, so drift is rare but not proven impossible.
- `/drivers/earnings` `total_tips` never showed drift, because `_money_str()` quantizes it to 2 dp at the boundary.
- `compute_fare_estimate` `grand_total` never showed drift either. All three inputs are exact 2-dp values, so a float sum cannot land on a half-cent tie. I compared the old and new code on 2,000,000 random triples of up to $10,000 each and got 0 differences.

## 2. Root cause

- MONEY-006: `compute_fare_estimate` was written before the Decimal service-layer extraction. SR-03 did not cover `features.py`, and in any case it only matches `float(...)`. It does not match a raw `+` or `round()`.
- MONEY-001: helpers were used inconsistently within one file. Each `earnings` accumulator was moved to `_d()`, but the `tips` accumulator right below it was left as it was. SR-03 excluded the file, so nothing caught it.
- MONEY-005: SR-03 left `earnings.py` out on purpose. The reason given was that "wrapping the boundary `float()` calls changes rounding on live payout figures". That holds only if you wrap them in `_round()`. Wrapping in `_f()`, which is `float()` itself, changes nothing.

## 3. Fix / remediation

1. `features.py` `compute_fare_estimate`: `grand_total = _fare_f(_fare_round(fb.total_fare + _fare_d(fees_total) + _fare_d(tax_amount)))`. This is a Decimal sum with ROUND_HALF_UP, converted to float only at the boundary. `_fare_round` (fare_service `_round`) is newly imported, using the dual-import pattern.
2. `routes/drivers/earnings.py`: every tip total from ride rows now goes through `_d()` and is summed in Decimal (5 sites). It is converted with `fare_service._f()` at the response boundary. `total_tips` in `/earnings` stays a `_money_str` string.
3. SR-03: I ran the rule over the three files first and got 20 findings (earnings.py 7, features.py 13, referral_payout.py 0). Each finding was either fixed or annotated:
   - 14 Decimal→float boundary `float(x)` calls became `_f(x)` / `_fare_f(x)`. The value is identical.
   - 6 non-money or no-arithmetic reads got a `# nosemgrep: spinr-no-float-in-money` comment with the reason: GPS lat/lng, the hst/gst/pst *rate* percentages, the configured `airport_fee` (only compared with > 0 and passed on; `calculate_fare` re-`_d()`s it), and the `/area-config` fee echo.
   - The three files were then added to `include`.
   - Full-repo run: SR-03 has 0 findings.

**Alternative considered and rejected:** return `Decimal`/`_money_str` strings from these endpoints (end-to-end Decimal, which is the money auditor's "str() the Decimal" preference). It would change the JSON types that shipped driver-app builds and corporate booking inserts already consume. It is also out of scope while the columns are FLOAT8. Keeping float at the boundary through `_f()` fixes the arithmetic and changes nothing on the wire.

## 4. Risk & impact on existing functionality

Blast radius: **single surface (backend). No DB schema, Stripe or state-machine change.**

- `compute_fare_estimate.grand_total` is read by:
  - `GET /rides/fare-estimate` (`features.py:957`) → the rider-app estimate display
  - `services/company_booking_service.py:101`, which re-`_d(str(...))`s it for the allowance/master-wallet check and persists it into `rides.grand_total` at line 210
  - `routes/corporate_company_bookings.py:340`, the corporate estimate endpoint

  The output is the same float for every 2-dp input (brute-force result above), so none of these readers sees a different value.
- `calculate_all_fees` `fees[].amount/calculated_value`, `fees_total`, `tax_amount`, `tax_breakdown` changed only from `float()` to `_fare_f()`, which is the identical function body. Consumers (`routes/rides/booking.py`, `_shared.py`, `estimates.py`, `compute_fare_estimate`) are unaffected.
- Earnings tips are read by `driver-app/store/driverStore.ts` (lines 1114-1155), which fetches all five endpoints. Only `total_tips` (a string, unchanged) is rendered (`ActivityView.tsx:188`). grep found no UI reader of `daily/weekly/monthly/comparison .tips`. One pre-existing mismatch remains: `driverStore.ts` types these as `MoneyString` while the backend returns numbers. I did not change it.
- Type edge: a day/week/month bucket or comparison period with rides but all tips null now returns `0.0` (float) where it used to return `0` (int). The value is the same in JSON/JS. No test or consumer depends on the int.
- `routes/drivers/earnings.py` now imports `_f` from `services.fare_service`. `routes/drivers/_deps.py` already imports from that module, so this adds no new import cycle.
- A ruff 0.15.12 reformat of one pre-existing line (`get_driver_balance` clawback generator) was needed for `ruff format --check` to pass on this file. It already failed on `main`. Formatting only.
- `.semgrep/spinr-rules.yml`: SR-03 is merge-blocking (`security-gates.yml` "Money-safety gate"). From now on, any new `float(x)` in these three files will block merges. That is intended. `scripts/security/test_generate_security_summary.py` passes.

## 5. User-experience effect

- **Driver:** the daily/weekly/monthly/comparison `tips` numbers in the API are now exact, e.g. `1.0` instead of `0.9999999999999999`. No current driver-app screen renders those fields, so no visible change is expected. The Activity "Tips" figure (`total_tips`) is unchanged.
- **Rider / corporate admin:** none. The estimate `grand_total` value is identical.
- Not visible mid-session. No copy or notification changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/features.py` | `grand_total` computed in Decimal with `_round`, then `_f`; boundary `float()` → `_fare_f()`; 6 `nosemgrep` annotations with reasons | MONEY-006; SR-03 coverage |
| `backend/routes/drivers/earnings.py` | 5 tip totals via `_d()` and Decimal; boundary `float()` → `_f()`; import `_f`; one ruff reformat | MONEY-001; SR-03 coverage |
| `.semgrep/spinr-rules.yml` | SR-03 `include` adds `earnings.py`, `features.py`, `referral_payout.py`; exclusion note updated | MONEY-005 / N19 |
| `backend/tests/test_float_money_quick_fixes.py` | New: 8 regression tests through `mock_supabase_client` | Gate 4 dry run |
| `docs/change-log/2026-09-25-float-money-writes.md` | This entry | CLAUDE.md gate |

## 7. Before / after

```python
# Before (features.py)
grand_total = round(subtotal + fees_result["fees_total"] + fees_result["tax_amount"], 2)
# After
grand_total = _fare_f(
    _fare_round(fb.total_fare + _fare_d(fees_result["fees_total"]) + _fare_d(fees_result["tax_amount"]))
)
```

```python
# Before (earnings.py, daily; weekly/monthly identical)
daily_data[date_str]["tips"] += r.get("tip_amount", 0) or 0
# After
daily_data[date_str]["tips"] += _d(r.get("tip_amount") or 0)
...  "tips": _f(data["tips"])   # boundary

# Before (comparison)
"tips": sum(r.get("tip_amount", 0) or 0 for r in rides),
# After
"tips": _f(sum((_d(r.get("tip_amount") or 0) for r in rides), Decimal("0"))),
```

**Concrete dry-run scenarios** (as exercised by the tests against `mock_supabase_client`, which runs the real `get_rows` → `repositories._base` path):

- A driver has 10 completed rides yesterday, each with a $0.10 tip. `GET /drivers/earnings/daily?days=7`:
  - before: `[{"tips": 0.9999999999999999, ...}]`
  - after: `[{"tips": 1.0, ...}]`

  `/weekly` and `/monthly` behave the same way. `/comparison` `current.tips` gives the same before/after on Python 3.11; on 3.12 the old code already returned 1.0 here.
- A fare estimate for 7.3 km / 13 min at base 3.10, 1.10/km, 0.20/min, booking 2.20 (fare 15.93), with two flat area fees of 0.10 and 0.20 and 5% GST (0.8115, rounded to 0.81):
  - before: `grand_total` 17.04
  - after: `grand_total` 17.04

  The value is unchanged. The fix changes how the number is computed, not the number.
- A `/drivers/earnings` tip set of 1.10, 2.20, 3.30 and ten 0.10 tips: `total_tips` is `"7.60"` both before and after. With the tip values as strings: before, a `TypeError` inside `try` led to HTTP 503; after, `"6.60"`. That case is not a production symptom (PostgREST returns NUMERIC as JSON numbers), but it shows the sum now goes through `_d()`.

## 8. Rollback plan

- Nothing is persisted differently. The response values are identical except the drifted tip floats, which are now exact. There is no flag. A code revert of the three commits is a complete rollback, and it touches no live data.
- Values already persisted, e.g. corporate `rides.grand_total` written by `company_booking_service`, are **not retro-edited**. The brute-force check shows they were already correct to the cent, so nothing needs remediation.
- If the extended SR-03 gate blocks an urgent unrelated PR, the fallback is to remove the three `include` lines. That is config-only, but it still needs a merge. Better: add a justified `# nosemgrep` to the offending line.

## 9. Verification performed

- [x] Automated tests (Python 3.11.15 locally; CI runs 3.12):
  - The new `tests/test_float_money_quick_fixes.py` gives 8 passed.
  - Against pre-fix code, 5 of the earnings tests fail and all 3 of the others pass. The failures are daily, weekly, monthly, comparison (the comparison one only on 3.11) and the str-tips case. The passes are the two MONEY-006 tests and `total_tips` "7.60". Those three pass because their output never differed, as described above.
  - Related suites, 563 passed and 1 skipped: `test_calculate_all_fees_tax`, `test_company_guest_booking`, `test_corporate_company_bookings_{coverage,routes}`, `test_earnings_{coverage,snapshot}`, `test_drivers_extended`, `test_features`, `test_fares`, `test_fares_coverage`, `test_routes_fares_coverage`, `test_surge_override_clamp`, `test_scheduled_pickup_fees`, `test_stop_fare_integrity`, `test_fare_display_labels`, `test_charge_late_{tip,corporate_tip,wallet_tip}`, `test_corporate_tip_reporting`, `test_min_tip_policy`, `test_rate_tip_abuse`, all 10 `test_referral_payout_*` / `test_referral_recredit_failed_claim` / `test_referrals_coverage`, and `test_admin_stats`.
  - `scripts/security/test_generate_security_summary.py`: 17 passed.
- [x] `ruff check` and `ruff format --check` pass with CI's pinned ruff 0.15.12 on all changed Python files.
- [x] `semgrep scan --config=.semgrep/spinr-rules.yml --metrics=off` (semgrep 1.178.0): `spinr-no-float-in-money` has 0 findings. The other rules' 9 findings are unchanged and are all in files this change does not touch.
- [x] Blast-radius grep: `compute_fare_estimate`, `grand_total` in the corporate booking service/routes, and `tip_amount`/`tips` across `backend/` and `driver-app/`/`shared/`. The grep also found `routes/rides/receipts.py:103-107`, where the receipt fallback adds `total_fare + area_fees_total + tax_amount + tip_amount` as raw floats when `grand_total` is null. It is out of scope and not fixed (see below).
- [x] Reviewed against CLAUDE.md money convention and the `spinr-money-auditor` checklist (self-review; the Agent tool was not available in this session).
- [ ] Manual staging repro: not done.
- [ ] Feature flag: not used. There is no user-visible change, and response values are identical or more exact.

## 10. What was NOT verified

- No staging or production check. Tests use `mock_supabase_client` only. No live Supabase, and no driver-app build or run: the backend JSON types are unchanged, and the app has no visual regression tooling.
- Tests ran on Python 3.11 locally. Production and CI are 3.12, where `sum()` is compensated. The comparison-tips test's before/after difference is shown only on 3.11.
- The semgrep version is 1.178.0 locally vs 1.177.0 pinned in CI. I did not run the CI container.
- Not fixed, found while checking (follow-ups):
  1. `earnings.py` `/weekly` and `/monthly` `driver_daily_stats` paths (lines 731-732 and 842-843) still add both `earnings` and `tips` from the pre-aggregated table as raw floats. This is the same drift class, on a different source table.
  2. `routes/rides/receipts.py:103-107`: the raw-float fallback `receipt_grand_total`.
  3. SR-03 still only matches `float(...)`. It cannot see raw `sum()`, `+=` or `round()` over DB floats (the shapes of MONEY-001 and MONEY-006), so these fixes are guarded by the new tests, not by the rule.
- The `spinr-money-auditor` agent itself was not run. The review was a self-review against its checklist.
