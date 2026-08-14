# Change Impact & Risk Log — Month-end reconciliation ValueError

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-07 |
| Author | Claude Code (agent), branch `claude/stripe-rider-payment-arch-o5vyes` |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| Related issue or gap ID | Adjacent finding §11.2 in `docs/change-log/2026-08-06-ledger-durability-double-entry.md` |

## 1. Issue / gap identified

`_sum_financial_events` builds its query window upper bound as `datetime(date.year, date.month, date.day + 1)`, which raises `ValueError: day is out of range for month` on the last day of every month — so the daily Stripe-vs-ledger reconciliation tick crashes ~12 nights a year, silently skipping the very control that detects lost ledger rows. Next occurrence would have been 2026-08-31.

## 2. Root cause

Naive day arithmetic on the `datetime` constructor instead of `timedelta`. The sibling helper `_sum_stripe_intents` already does it correctly (epoch + 86400 at `reconciliation.py:168-169`); this helper predates that pattern.

## 3. Fix / remediation

`day_end = (day_start_dt + timedelta(days=1)).isoformat()` — rolls into the next month/year correctly. No other logic touched.

## 4. Risk & impact on existing functionality

**Blast radius: isolated.** `_sum_financial_events` has exactly one caller, `_run_reconciliation` (`reconciliation.py:85`), which already wraps it in `try/except` — meaning the crash was being caught and logged as "failed to query financial_events", making the skip look like a transient DB error. The produced bounds are identical for days 1–27/29/30 of any month; only the previously-crashing dates now produce a (correct) result. No table writes change; the reconciliation only reads and, on discrepancy, writes `reconciliation_discrepancies` exactly as before.

## 5. User-experience effect

Nobody — backend-only, internal control. Finance/ops gain 12 reconciliation runs a year they were silently losing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/reconciliation.py` | `day + 1` → `+ timedelta(days=1)` in `_sum_financial_events` | Month-end crash |
| `backend/tests/test_reconciliation.py` | Parametrized boundary regression test (Jan 31, Feb 28, leap Feb 29, Dec 31, Apr 30) asserting the exact `.gte/.lt` bounds | Prevent reintroduction |

## 7. Before / after

```python
# Before — raises ValueError on the last day of every month
day_end = datetime(date.year, date.month, date.day + 1, tzinfo=timezone.utc).isoformat()
```

```python
# After — rolls into the next month/year
day_start_dt = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
day_end = (day_start_dt + timedelta(days=1)).isoformat()
```

## 8. Rollback plan

`git revert` is sufficient: the change touches no data, no schema, no flags — it only changes an in-memory query bound. Reverting restores the (broken) status quo with no data-level remediation needed.

## 9. Verification performed

- `backend/tests/test_reconciliation.py` — 24 passed (19 existing + 5 new parametrized boundary cases).
- `ruff check` / `ruff format --check` clean.
- Full backend suite run before push (result recorded at push time).

## 10. What was NOT verified

- Not run against a real Supabase; the query-bound assertion is against a mocked client (consistent with every other test in this file — the module has no integration tier).
