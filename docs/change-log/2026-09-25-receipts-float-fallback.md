# Change Impact & Risk Log — Rider receipt float fallback → Decimal

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (session_01PPGd1wK6WRzbtxcGj3qNkX) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments / rides |
| PR / commit link | `07d52c6` on `claude/fix-receipts-float-fallback` (local only, not pushed) |
| Related issue or gap ID | Follow-up from the money audit of PR #5792 (N19) |

## 1. Issue / gap identified

`GET /api/v1/rides/{ride_id}/receipt` (`backend/routes/rides/receipts.py::get_ride_receipt`) did money math in float in three places:

| # | Site (pre-fix line) | Shape | Effect |
|---|---|---|---|
| 1 | L102–107, non-fare-locked branch | `grand_total or (total_fare + area_fees_total + tax_amount + tip_amount)` in raw float | Legacy rides with no persisted `grand_total` could return e.g. `0.30000000000000004` / `12.519999999999998` as the receipt total. If the numeric columns arrived as strings the `+` would concatenate or raise `TypeError` (500). |
| 2 | L142, `cancellation_fee` field | `cancellation_fee_admin + cancellation_fee_driver` in raw float | Same drift (`0.1 + 0.2`), and a `TypeError` 500 if either column is present but `NULL` (`.get(k, 0)` returns `None` for a present-null key). |
| 3 | L90–93, fare-lock branch | `float(_d(tip))` then `_f(_d(ride_tip))` | Float round-trip of the tip before it is appended as a line. Value-preserving in practice, but money passed through float between two Decimal conversions. |

No other float money arithmetic exists on the receipt build path: `utils/email_receipt.py`, `utils/receipt_pdf.py` and `utils/cancellation_receipt.py` accumulate in `Decimal` (their `float(...)` calls are boundary conversions of already-Decimal values), and `_sum_fare_breakdown` sums in Decimal.

## 2. Root cause

The receipt route predates the Decimal convention. It was moved verbatim out of `routes/rides.py` in the god-file split ("pure code motion — no behaviour changes"), so the float fallback was carried over. SR-03 did not catch it, for two separate reasons:

- **`backend/routes/rides/receipts.py` is not in SR-03's `paths.include` list** (`.semgrep/spinr-rules.yml`, rule `spinr-no-float-in-money`). The listed file for this package is `backend/routes/rides/payments.py`. `routes/rides/_shared.py` (home of `_build_fare_breakdown`) is not listed either. The rule was left unchanged in this branch, as instructed.
- **Adding the path would not have helped.** SR-03 matches `float($X)`, `* 1.0` and `/ 1.0`. It excludes `float(_d(...))`, and it does not match bare `+` on float values. I ran a copy of the rule with its `paths` filter removed (scratchpad copy, not committed) against both the old and new `receipts.py`: **0 findings on either**. The rule's own comment already notes this blind spot ("does not see raw float `sum()`/`+=`/`round()` over DB values").

## 3. Fix / remediation

All three sites now use the CLAUDE.md money helpers `_d` / `_round` / `_f` from `routes/rides/_shared.py`, imported the same way the module already imports `_d`/`_f`:

- Fallback total: each column goes through `_d(x or 0)`, the Decimal sum through `_round` (HALF_UP, 2dp), and the result out through `_f`. The components are the same as before.
- `cancellation_fee`: `_f(_round(_d(admin or 0) + _d(driver or 0)))` for cancelled rides; still the literal `0` otherwise.
- Fare-lock tip: stays a `Decimal` until the line is built, then `_f(ride_tip)`.

The response shape is unchanged: every field name is the same, and money stays a JSON number (float) at the wire. The persisted `grand_total` is still passed through untouched when it is present.

**Alternative considered and rejected:** make the fallback `receipt_grand_total = _sum_fare_breakdown(fare_lines)`, so the total equals the displayed lines by construction, the same way the fare-lock branch already works. Rejected for three reasons:
- **It changes the numbers, not just the arithmetic.** `_build_fare_breakdown` builds tax lines only from `tax_breakdown`. A legacy row with `tax_amount` but no `tax_breakdown` would have its total silently drop the tax actually charged. Area fees would also come from `area_fees_breakdown` instead of `area_fees_total`.
- **Promo rides would get a different total.** That is a visible change to what riders see on a live-tested surface, which is out of scope for a float-math fix.
- **The chosen fix is smaller and keeps behaviour.** Same components and output, with the drift removed.

## 4. Risk & impact on existing functionality

Blast-radius greps run:
- `grep -rn "/receipt" rider-app driver-app admin-dashboard shared` (ts/tsx, no node_modules): no client calls the JSON `GET /rides/{id}/receipt`. rider-app calls only `/receipt.pdf` and `/email-receipt`, which are separate handlers in the same file and were not touched. admin uses `/api/admin/rides/{id}/send-receipt`.
- `grep -rln "get_ride_receipt\|rides/receipts"` in `backend`: `backend/ai/tools_rides.py` has its own, separate `get_ride_receipt` that reads the snapshot directly and does not call this route. The only other in-repo callers are the tests `test_cancelled_ride_receipt.py`, `test_rides_extended.py` and `test_coverage_rides.py`, which all still pass.
- `_d`, `_round` and `_f` in `_shared.py` were only imported, not modified. No shared code changed.
- `docs/known-forks.md` has no receipt entry.

The only reader of the changed code is this one endpoint, so the **blast radius is isolated**. There are no DB writes, no state-machine change, no wallet or Stripe interaction, and no background-loop involvement. The endpoint is read-only.

Remaining regression surface:
- **Rounding.** The fallback total is now rounded to cents with HALF_UP. The source columns are 2dp numerics, so the only visible difference is that drift tails disappear.
- **All-zero fallback type.** A fallback where every component is zero or absent now returns `0.0` instead of int `0`. Both are the JSON number zero.
- **Present-but-NULL fee columns.** A cancelled ride whose fee columns are present but `NULL` now returns `0.0` instead of a 500. This is a crash turned into a value, not a masked DB, auth or payment error: NULL fee columns mean no fee, the same reading `utils/cancellation_receipt.py` already uses.

### Reconciliation check (task step 2): what I found

Paths that reconcile in Decimal (line items sum to `grand_total`), each covered by a test:
- **Fare-locked:** the total is `_sum_fare_breakdown` over the lines, including the appended tip.
- **Cancelled:** comes from `cancellation_charge`.
- **Non-locked with persisted `grand_total` and no tip.**
- **Non-locked legacy fallback with no promo.**

On every path the GST and PST tax lines stay separate, and in the fixtures they sum to `tax_amount`.

**Two pre-existing gaps found. They are not float math, so they were not fixed.** Each is recorded as a `strict=True` xfail test, so it will flip loudly when someone fixes it:
1. **The legacy fallback total ignores `discount_amount`.** `_build_fare_breakdown` adds a negative promo line, but the fallback sum `total_fare + fees + tax + tip` does not subtract it. On a legacy promo ride the JSON total is therefore higher than the sum of its lines. The email and PDF receipts already subtract the capped discount (C102), so the JSON endpoint is the odd one out.
2. **On a tipped ride the persisted `grand_total` does not match the lines.** The persisted `grand_total` excludes tip (see `utils/email_receipt.py`, "Persisted grand_total includes fees + tax but NOT tip"), but on the non-fare-locked path `_build_fare_breakdown` adds a Tip line. A tipped, non-locked ride therefore shows lines that sum to `grand_total + tip`.

Both need a product decision, and at least one more test for the legacy `tax_amount`-without-breakdown case, before anyone changes them. Neither has an in-repo client consumer today.

## 5. User-experience effect

- **Riders:** none visible through the shipped apps, because none of them calls this JSON endpoint. A direct API consumer would see an exact 2dp total instead of a float-drift tail on legacy rides with no `grand_total`, and a number instead of a 500 on the NULL-fee cancelled edge case.
- **Mid-session effect:** none. The endpoint is read-only and only works on terminal-status rides.
- **Copy and notifications:** no change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/receipts.py` | 3 float money sites switched to `_d`/`_round`/`_f`; `_round` added to the `_shared` import | Decimal-only money math (CLAUDE.md Critical Conventions) |
| `backend/tests/test_receipts_decimal_fallback.py` | New: 11 tests plus 2 strict xfails, run through the autouse-patched `mock_supabase_client` | Regression coverage for drift, GST/PST reconciliation and response types; records the two known gaps |
| `docs/change-log/2026-09-25-receipts-float-fallback.md` | This log | Mandatory Change Impact Log |

## 7. Before / after

```python
# Before
receipt_grand_total = ride.get("grand_total") or (
    (ride.get("total_fare", 0) or 0)
    + (ride.get("area_fees_total", 0) or 0)
    + (ride.get("tax_amount", 0) or 0)
    + (ride.get("tip_amount", 0) or 0)
)
...
"cancellation_fee": (
    (ride.get("cancellation_fee_admin", 0) + ride.get("cancellation_fee_driver", 0))
    if ride.get("status") == RideStatus.CANCELLED else 0
),
```

```python
# After
receipt_grand_total = ride.get("grand_total") or _f(
    _round(
        _d(ride.get("total_fare") or 0)
        + _d(ride.get("area_fees_total") or 0)
        + _d(ride.get("tax_amount") or 0)
        + _d(ride.get("tip_amount") or 0)
    )
)
...
"cancellation_fee": (
    _f(_round(_d(ride.get("cancellation_fee_admin") or 0) + _d(ride.get("cancellation_fee_driver") or 0)))
    if ride.get("status") == RideStatus.CANCELLED else 0
),
```

Concrete scenario: a legacy completed ride has `total_fare=10.10`, `area_fees_total=0.20`, `tax_amount=1.12` (GST 0.51 + PST 0.61), `tip_amount=1.10` and no `grand_total`.
- **Before:** `grand_total = 12.519999999999998`.
- **After:** `grand_total = 12.52`.

## 8. Rollback plan

- **No flag.** This is a read-only response computation with no data writes, so there is no live data to remediate.
- **Rollback is a redeploy** of a `git revert 07d52c6`. That is acceptable here because nothing is persisted, and the reverted code only brings back the float drift and the NULL-fee 500.
- **Wire contract is unchanged.** Field names and float wire types are the same, so no client needs coordinating in either direction.

## 9. Verification performed

- [x] **New test file:** `pytest tests/test_receipts_decimal_fallback.py` gives 11 passed and 2 xfailed. Against the pre-fix `receipts.py` (restored temporarily from `origin/main`), 4 of the new tests fail: the two drift tests, the string-column test and the cancellation-fee drift test. So the regression tests do exercise the change.
- [x] **Existing suites:** every `tests/*receipt*`, `*fare*`, `*payment*` and `*tip*` file, plus `test_rides_extended.py`, `test_coverage_rides.py` and `test_ai_tools_rides.py`, gives **1019 passed, 2 xfailed** (`--no-cov`).
- [x] **ruff:** `ruff check` and `ruff format --check` are clean on both Python files.
- [x] **SR-03:** `semgrep scan --config=.semgrep/spinr-rules.yml backend/routes/rides/receipts.py` gives 0 findings, but only because the file is outside the rule's `paths.include`. A path-less copy of SR-03 also gives 0 findings on both the old and new file (see §2).
- [x] **Blast-radius grep:** the searches are listed in §4.
- [x] **Checked against the CLAUDE.md money convention:** `_d`/`_round`/`_f` are used and no float arithmetic remains in the handler. The dual-import pattern is untouched, because the helpers come in through the existing package-relative `_shared` import.
- [ ] **No feature flag.** The change removes float drift from a read-only endpoint with no in-repo client, so there is no user-visible behaviour to flag.

## 10. What was NOT verified

- **Real data:** not run against real Supabase or staging. There is no count of how many production rides actually reach the no-`grand_total` fallback, or have NULL cancellation-fee columns.
- **Mocks only:** tests use `mock_supabase_client`. Whether production's numeric columns reach the handler as float, string or Decimal was not confirmed; the fix handles all three.
- **Reviewer agent:** no `spinr-money-auditor` or `/code-review` pass was run on the diff, because no subagent tool was available in this session. One should be run before merge.
- **Out-of-scope receipt paths:** `routes/rides/_shared.py::_build_fare_breakdown` uses `float()` in the promo-cap comparison and negation. That is a Decimal-computed value converted before `min`, not accumulation drift, but it is shared with `get_ride`, ride history and admin, so it was left alone and not re-audited here.
- **The two known gaps (§4)** were only documented, not fixed.
- **No production build:** none needed, since this is a backend-only change.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
