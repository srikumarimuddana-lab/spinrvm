# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments (driver earnings) |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | A28, `docs/audit/2026-08-11-driver-rider-migration-audit.md`'s P2 float-on-money finding |

## 1. Issue / gap identified

`routes/drivers/earnings.py`'s daily/weekly (fallback)/monthly (fallback)/
comparison earnings aggregation summed `base_fare + distance_fare +
time_fare + tip_amount` via raw Python `float()` accumulation across many
rides, instead of the file's own established `_d()`/Decimal pattern (used
correctly in `get_driver_balance`, the same file, a few lines away). A
genuine CLAUDE.md Decimal-discipline violation, flagged by the audit as an
"adjacent finding" while reviewing the same file line-by-line for a
different reason.

## 2. Root cause

These 4 endpoints (`get_driver_daily_earnings`, the rides-table fallback
paths in `get_driver_weekly_earnings`/`get_driver_monthly_earnings`, and
`get_driver_earnings_comparison`'s `summarize()`) were written before (or
without following) the Decimal-only convention `get_driver_balance`
already uses in this same file.

## 3. Fix / remediation

Replaced `float(r.get(...) or 0)` with `_d(r.get(...) or 0)` (the file's
existing 2dp-quantizing Decimal helper) at all 4 accumulation sites,
converting to `float` only at the response-serialization boundary (the API
contract is unchanged — these endpoints still return JSON floats).

## 4. Risk & impact on existing functionality

- Blast radius: isolated to these 4 read-only display endpoints in one
  file. Grepped for other `float(r.get("base_fare")` patterns across the
  backend — none remain.
- Display-path only — no money movement, no wallet/Stripe/payout write.
  `get_driver_balance` (the endpoint that actually bounds a Stripe payout
  Transfer) was already correct before this change and is untouched here.
- Output values are numerically identical for any single ride or any
  small ride count where float drift doesn't accumulate to a visible
  difference; they only diverge (by fractions of a cent) once enough rides
  are summed in one period for float's binary rounding error to surface —
  exactly the scenario this fix corrects.

## 5. User-experience effect

Driver-facing (daily/weekly/monthly earnings charts, period-comparison
screen). A driver with enough rides in a single period to have hit the
float-drift threshold may see their period total shift by a fraction of a
cent — a correction toward the exact value, not a new number appearing
from nowhere.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/earnings.py` | 4 accumulation sites switched from `float()` to `_d()`/Decimal, cast to `float` only at the response boundary | CLAUDE.md Decimal-only money-arithmetic rule |
| `backend/tests/test_earnings_coverage.py` | 4 new regression tests (one per site) using a classic float-imprecision input (10× $0.10 components) that sums to `3.9999999999999996` under raw float accumulation vs exactly `4.0` under Decimal | Prove the fix, not just assert output is unchanged |

## 7. Before / after

```python
# Before
daily_data[date_str]["earnings"] += (
    float(r.get("base_fare") or 0)
    + float(r.get("distance_fare") or 0)
    + float(r.get("time_fare") or 0)
    + float(r.get("tip_amount") or 0)
)
# ... later, returned directly as JSON (still float, still drift-prone)
```

```python
# After
daily_data[date_str]["earnings"] += (
    _d(r.get("base_fare") or 0)
    + _d(r.get("distance_fare") or 0)
    + _d(r.get("time_fare") or 0)
    + _d(r.get("tip_amount") or 0)
)
# ... cast to float only at the response boundary:
results = [
    {"date": date, **{**data, "earnings": float(data["earnings"])}}
    for date, data in sorted(daily_data.items())
]
```

## 8. Rollback plan

`git revert` — pure code change, no data mutation, no migration.

## 9. Verification performed

- [x] `pytest backend/tests/test_earnings_coverage.py -q --no-cov` → 40 passed (36 prior + 4 new)
- [x] Verified all 4 new tests genuinely fail pre-fix: checked out
  `origin/main`'s version of the file, re-ran the 4 new tests — all failed
  with the exact predicted drift value (`3.9999999999999996` vs. `4.0`);
  restored the fixed file, re-ran — all pass
- [x] Blast-radius grep: no other `float(r.get("base_fare")` pattern remains in the backend
- [x] Ran the broader affected-test sweep (`test_drivers_extended.py`,
  `test_admin_drivers_coverage.py`, `test_t4a_email.py`,
  `test_payouts_coverage.py`, `test_p1_security.py`,
  `test_driver_deletion_tombstone.py`, `test_p2_payout_t4a.py`,
  `test_instant_payout.py`, `test_previous_app_sunset.py`) → 358 passed

## What was NOT verified

- Not tested against a live driver account with a real multi-ride period
  window — verified at the unit level with a deterministic float-drift
  scenario instead.
- No visual regression tooling exists for driver-app; the earnings-chart
  values are backend-only in this change (no frontend file touched).
