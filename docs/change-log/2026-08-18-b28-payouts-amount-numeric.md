# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude Code (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (added on push) |
| Related issue or gap ID | ACTION_ITEMS.md B28 |

## 1. Issue / gap identified

`payouts.amount` has always been a legacy `double precision` (FLOAT8)
column. Every writer that builds a raw insert payload (bypassing the
generic `db_supabase.insert_one`, which already serializes `Decimal`
correctly) had to `float()` its `Decimal` amount to make the payload
JSON-serializable — CLAUDE.md's Decimal-only rule normally forbids this
outright, and it's a standing landmine: a future writer that ever sums
several small amounts *before* that final cast (rather than casting each
row individually, as every current writer already does) would silently
accumulate binary floating-point drift into a real money column.

## 2. Root cause

The column was created as `double precision` (predates the Decimal-only
convention becoming an enforced rule) and never migrated. Three read-side
SQL functions (159, 162, 303) already independently discovered and worked
around this by casting `amount::text::numeric` before summing — a strong
signal the column type itself was the actual defect, not any individual
caller's arithmetic.

## 3. Fix / remediation

- New migration 331: `ALTER TABLE payouts ALTER COLUMN amount TYPE
  NUMERIC(10, 2) USING amount::numeric(10, 2)`. Verified against the live
  production table (~222 rows) before writing the migration — a table this
  small takes a sub-second ACCESS EXCLUSIVE rewrite, well under any
  migration-window concern. `NUMERIC(10,2)` matches the existing precedent
  on this same table (`payouts.net_amount`, migration 138) and comfortably
  covers realistic CAD driver payout amounts (max representable:
  $99,999,999.99).
- Three real writers updated from `float(decimal_value)` to
  `str(decimal_value)` in their raw insert-payload dicts:
  `legacy_payout_correction_service.py`, `stripe_payout_sync_service.py`,
  `booking_import_service.py`. `str(Decimal)` round-trips exact into a
  NUMERIC column; `float()` would reintroduce binary rounding error before
  Postgres ever sees the value.
- `routes/drivers/payouts.py` needed **no change** — verified it already
  passes `Decimal` values through `db_supabase.insert_one`/`update_one`,
  whose `_serialize_for_api` helper (`repositories/_base.py`) already does
  `str(Decimal)` on every write. It never had this bug; ACTION_ITEMS.md's
  original filing listed it as a file to check, and this pass confirms it
  needed nothing.
- The three existing SQL functions (159, 162, 303) that already cast
  `amount::text::numeric` are **deliberately left unedited**, per this
  repo's append-only migration convention — that cast becomes a harmless
  no-op once the underlying column is already NUMERIC (numeric→text→numeric
  round-trips exactly).
- New regression test (`test_payouts_amount_no_float_cast.py`) statically
  scans the three writer files' actual `payouts_to_insert`/insert-list
  blocks for a `"amount": float(` pattern and fails if found. Verified the
  test actually catches the regression: reverted one fix temporarily,
  confirmed the test failed, then restored the fix.

## 4. Risk & impact on existing functionality

- **Blast radius: contained to the payouts write path.** Grepped the whole
  backend for every `.insert(...)`/`.update(...)` call against the
  `payouts` table (`supabase.table("payouts")`) to confirm all direct
  writers were found and fixed; the fourth (`routes/drivers/payouts.py`)
  goes through the generic serializer and needed nothing.
- **Read-side impact: none.** PostgREST serializes a NUMERIC column as a
  JSON number, and supabase-py/Python's `json.loads` decodes a JSON number
  with a decimal point as a Python `float` either way (NUMERIC or FLOAT8
  source) — so every read path continues to receive the same `float` shape
  it always did. Verified the one place that reads `payouts.amount` back
  out for display (`routes/drivers/payouts.py`'s `_money_str` helper)
  already normalizes via `Decimal(str(v))` regardless of input type, so it
  is unaffected either way.
- No interaction with the ride state machine or any background loop.
- **spinr-migration-reviewer**: SAFE TO APPLY, no blockers. One WARNING
  (verify no downstream reader assumes float-typed `amount`) — investigated
  and confirmed a non-issue (see read-side impact above).
- **spinr-money-auditor**: SAFE TO MERGE, no blockers on this diff. One
  WARNING, an adjacent pre-existing finding (not introduced by this diff):
  several other `float()` calls in `booking_import_service.py` write into
  `rides` columns that are already NUMERIC/DECIMAL — spun off as a new
  ACTION_ITEMS.md entry (B29) rather than fixed here, since scoping this
  diff to only the `payouts.amount` column keeps the change reviewable and
  the two tables' write paths are otherwise independent.

## 5. User-experience effect

- **Backend-only, no UI surface.** No rider/driver/corporate-admin-facing
  change. Driver payout amounts displayed in the driver app / admin
  dashboard are computed identically before and after (verified via the
  `_money_str` read-path check above).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/331_payouts_amount_numeric.sql` | New — `ALTER COLUMN amount TYPE NUMERIC(10,2)` | Close the FLOAT-column landmine at its source |
| `backend/services/legacy_payout_correction_service.py` | `float(r.amount)` → `str(r.amount)` | Serialize Decimal exactly into the now-NUMERIC column |
| `backend/services/stripe_payout_sync_service.py` | `float(amount)` → `str(amount)` | Same |
| `backend/services/booking_import_service.py` | `float(amount)` → `str(amount)` (payouts write only — other `float()` calls in this file target `rides` columns, out of scope, see B29) | Same |
| `backend/tests/test_payouts_amount_no_float_cast.py` | New | Static regression test, verified it catches the mistake |
| `backend/tests/test_legacy_payout_correction_service.py`, `test_stripe_payout_sync_service.py`, `test_booking_import_service.py`, `test_admin_booking_import.py` | Updated assertions from float literals to string literals | Match the new serialization |

## 7. Before / after

```python
# Before
"amount": float(r.amount),
```

```python
# After
"amount": str(r.amount),
```

(Same pattern in all three writer files.)

## 8. Rollback plan

- **Code**: `git revert` — the three writer changes are safe to revert on
  their own even after the column migration lands, since `str(Decimal)`
  parses correctly into either a FLOAT8 or NUMERIC column; a revert would
  only reintroduce the (small, historically-unobserved) float-precision
  risk, not break anything outright.
- **Schema**: migration's own documented rollback —
  `ALTER TABLE payouts ALTER COLUMN amount TYPE FLOAT8 USING amount::float8`.
  Since no application code depends on the column being exactly one type or
  the other for correctness (only for precision), this is safe to run even
  after new rows have been written post-migration.

## 9. Verification performed

- [x] Automated tests: 352 tests pass across the full affected surface
  (`pytest -k "payout or booking_import or legacy_payout"`), including the
  5 new regression tests and all updated assertions
- [x] Verified the new regression test actually catches the regression it's
  meant to catch (temporarily reverted a fix, confirmed test failure,
  restored the fix)
- [x] `ruff check` / `ruff format --check` clean on every touched file
- [x] Live production query (read-only) confirmed `payouts.amount` was
  `double precision` and the table has ~222 rows before writing the
  migration, informing the "safe without CONCURRENTLY" judgment
- [x] `spinr-migration-reviewer` review: SAFE TO APPLY, no blockers
- [x] `spinr-money-auditor` review: SAFE TO MERGE, no blockers on this
  diff; one adjacent finding spun off as B29 rather than scope-crept into
  this PR
- [ ] Manual verification against a real staging environment with the
  migration actually applied — not performed; migration was reviewed and
  is believed safe based on the live read-only query + migration-reviewer
  sign-off, but has not itself been run against production or staging in
  this session (this session only ran read-only `execute_sql` queries, not
  `apply_migration`)

## 10. Sign-off

- [x] Rollback plan is concrete and testable: documented `ALTER COLUMN`
  reversal in the migration file itself
- [x] Blast radius is stated, not assumed: grepped every direct
  `payouts`-table writer, confirmed all four (three fixed, one already
  correct)
- [x] No silent behavior change to an already-shipped flow — read-side
  behavior verified unchanged; write-side behavior only becomes *more*
  precise, never less
