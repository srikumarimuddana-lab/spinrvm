# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments (driver earnings/payouts), also touches admin, drivers |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | A26, `ACTION_ITEMS.md` (filed while fixing P0-B, `docs/audit/2026-08-11-driver-rider-migration-audit.md`) |

## 1. Issue / gap identified

`utils/legacy_rides.py`'s `EXCLUDE_LEGACY_RIDES = {"legacy_import_metadata":
None}` was merged directly into real `db_supabase.get_rows()` filter dicts
at 9+ call sites (`routes/drivers/earnings.py` ×7 — including the driver
balance/earnings endpoints — plus `utils/driver_statement.py`,
`utils/t4a_annual_job.py`, `routes/admin/drivers.py`).
`repositories/_base.py`'s `_apply_filters` compiles a `None` value to
PostgREST `is.null` → real SQL `column IS NULL`. But
`rides.legacy_import_metadata` (and the equivalent `users`/`drivers`
columns) is declared `NOT NULL DEFAULT '{}'::jsonb` — no row, imported or
not, can ever be SQL NULL there. `IS NULL` against a `NOT NULL` column
matches **zero rows, always** — not "zero legacy rows," every row the
filter touches.

**Confirmed live** against production (`soavhtdhefowwvforzwb`,
`ca-central-1`): found a driver with 1 real, non-legacy completed
$7.59 ride plus 28 legacy-imported rides. Ran the literal SQL the broken
filter compiles to — it returned zero rows. Their `GET /drivers/balance`
response is currently showing `total_rides: 0`, `total_earnings: $0.00`
despite having real, unpaid earnings sitting in the database. Only 3
drivers on the platform currently have any Spinr-native completed rides at
all (very early-stage rollout) — all 3 are affected right now.

## 2. Root cause

The `None`-means-`IS NULL` pattern is correct for an ordinary nullable
column, and is used correctly elsewhere in the codebase for genuinely
nullable columns. It was misapplied here to a column that is `NOT NULL`
with a non-null default — a column whose "no data" state is represented by
the default value (`'{}'::jsonb`), not by SQL NULL.

A secondary structural cause: `repositories/_base.py`'s filter-dict
mini-language treats **any dict value** as an operator-map (for
`$gte`/`$in`/etc.), so even a naive `{"legacy_import_metadata": {}}` fix
would not have worked — an empty dict reads as "an operator-map with zero
operators" and silently applies **no filter at all**, which would have
widened every affected query to include legacy rows again (a different,
quieter bug).

## 3. Fix / remediation

- Added an explicit `$eq` operator to `repositories/_base.py`'s
  `_apply_filters` (`_SUPPORTED_FILTER_OPS` + a new `if "$eq" in v: q =
  q.eq(...)` branch), so a caller can filter on an exact value — including a
  dict — without the `None`-means-`IS NULL` trap or the bare-dict-means-no-op
  trap.
- Changed `EXCLUDE_LEGACY_RIDES` to `{"legacy_import_metadata": {"$eq":
  {}}}`. This is a single-source-of-truth fix: every one of the 9+ call
  sites just merges this constant, so none of them needed individual code
  changes.
- Verified the fix directly against production data: re-ran the same
  literal-filter reproduction for the same driver — now correctly returns
  their 1 real ride.

## 4. Risk & impact on existing functionality

- Blast radius: `_apply_filters` is the shared filter compiler for every
  `get_rows`/`update_one`/`delete_many` call in the backend — grepped for
  all `EXCLUDE_LEGACY_RIDES` consumers (5 files: `driver_statement.py`,
  `t4a_annual_job.py`, `routes/admin/drivers.py`, `routes/drivers/earnings.py`
  ×7 call sites, plus the definition itself). The new `$eq` operator is
  strictly additive to `_apply_filters` — no existing operator's behavior
  changed, confirmed by the full backend suite passing unchanged (11020
  passed, 0 failed, same as pre-change baseline modulo one stale mock fixed
  below).
- One test (`test_drivers_extended.py::TestGetDriverBalance::
  test_balance_drops_legacy_import_rides_and_their_offset_together`)
  hardcoded the OLD (broken) filter shape in its mock's exclusion-detection
  logic (`filters.get("legacy_import_metadata") is None`). Updated to match
  the new `{"$eq": {}}` shape — this was a test correctly encoding the old
  bug, not a real behavior regression.
- `routes/admin/rides.py`'s `/earnings/rides` CSV export (fixed in the
  prior P0-B PR, #3683) deliberately avoided `EXCLUDE_LEGACY_RIDES` and used
  a widening loop-fetch + `drop_legacy_rides()` instead, specifically
  because this bug was suspected but not yet confirmed at the time. That
  loop-fetch is now technically redundant (the underlying filter would work
  correctly if used directly) but remains correct and harmless — not
  reverted here, out of scope for this fix, and it costs nothing extra in
  practice now that legacy rows are correctly excluded either way.
- Every driver whose earnings/balance/statement previously computed as
  `$0`/`0 rides` due to this bug will see their correct, non-zero numbers
  after this deploys — a **user-visible increase** in a driver's reported
  earnings and payable balance for anyone with at least one Spinr-native
  completed ride. This is a correction, not new money — `payable_balance`
  was always understated by exactly the amount their real (non-legacy)
  rides should have contributed; no ledger/wallet delta needed, this only
  changes what a read-only query returns.

## 5. User-experience effect

**Driver-facing.** Any driver with at least one Spinr-native completed ride
will see their `total_rides`/`total_earnings`/`payable_balance` figures
jump from `0`/`$0.00` to the correct, real values the moment this deploys —
mid-session if they have the app open. This is a **positive** correction
(a driver seeing more money they're owed, not less), but per CLAUDE.md's
guidance on "money changes need a dry run" and "no silent behavior change
to a live-tested flow," it should still be communicated: a driver who was
told (or assumed) they had $0 earnings and now sees a real balance may have
support questions about why the number "changed."

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/repositories/_base.py` | Added `$eq` to `_SUPPORTED_FILTER_OPS` + a new operator branch calling `q.eq(...)` | Give callers a way to filter on an exact value (including a dict) without the `None`→`IS NULL` or bare-dict→no-op traps |
| `backend/utils/legacy_rides.py` | `EXCLUDE_LEGACY_RIDES` changed from `{"legacy_import_metadata": None}` to `{"legacy_import_metadata": {"$eq": {}}}` | Fix the unsatisfiable predicate at its single source of truth — propagates to all 9+ consumers |
| `backend/tests/test_base_multi_operator_filters.py` | 3 new tests: `$eq` compiles correctly (including dict values), a scalar `$eq` case, and a test documenting the bare-empty-dict no-op trap | Regression coverage for the new operator and the trap it avoids |
| `backend/tests/test_drivers_extended.py` | Updated one test's mock to detect the new `{"$eq": {}}` filter shape instead of the old bare `None` | The old mock encoded the bug's filter shape; needed updating to match the fix |

## 7. Before / after

```python
# Before — utils/legacy_rides.py
EXCLUDE_LEGACY_RIDES: dict[str, Any] = {"legacy_import_metadata": None}
# compiles to: WHERE legacy_import_metadata IS NULL
# — unsatisfiable against a NOT NULL DEFAULT '{}'::jsonb column; matches
#   ZERO rows, always, for every query that merges this in.
```

```python
# After
EXCLUDE_LEGACY_RIDES: dict[str, Any] = {"legacy_import_metadata": {"$eq": {}}}
# compiles to: WHERE legacy_import_metadata = '{}'::jsonb
# — correctly matches only non-legacy rows.
```

```sql
-- Live verification (production, ca-central-1), same driver, before/after:

-- Before (old predicate) — driver has 1 real ride, query returns:
SELECT id FROM rides WHERE driver_id = '<id>' AND status='completed'
  AND legacy_import_metadata IS NULL;
-- []  (0 rows — wrong; the driver has 1 real ride)

-- After (new predicate):
SELECT id FROM rides WHERE driver_id = '<id>' AND status='completed'
  AND legacy_import_metadata = '{}'::jsonb;
-- [{"id": "496486a2-..."}]  (1 row — correct)
```

## 8. Rollback plan

Pure code change, no migration, no data mutation. `git revert` restores the
prior (broken) behavior — no data cleanup needed since this fix only
changes what a read-only query returns, never anything written. If reverted,
affected drivers' balance/earnings figures would return to reading
$0/0-rides again (the pre-existing state), not become newly wrong in a
different way.

## 9. Verification performed

- [x] Full backend suite: `pytest backend/tests/ -m "not slow" -q --no-cov`
  → **11020 passed, 8 skipped, 1 xfailed, 0 failed** (one pre-existing test
  updated to match the fixed filter shape; no other regressions)
- [x] New unit tests pin the `$eq` operator's compiled output and the
  bare-dict no-op trap it avoids (`test_base_multi_operator_filters.py`)
- [x] **Live verification against production Supabase** (`soavhtdhefowwvforzwb`,
  `ca-central-1`, via authorized MCP connector): reproduced the bug with
  the literal old predicate (0 rows for a driver with 1 real ride), then
  reproduced the fix with the literal new predicate (correct 1 row) —
  confirmed on real data, not just unit-test mocks
- [x] Blast-radius grep for every `EXCLUDE_LEGACY_RIDES` consumer (5 files,
  9+ call sites)

## What was NOT verified

- Did not exercise the actual HTTP endpoints (`GET /drivers/balance` etc.)
  against a live server with a real JWT for the affected driver — verified
  via direct SQL reproduction of the exact compiled filter instead, which is
  the layer the bug and fix both live in.
- Did not check whether any monitoring/alerting/support-ticket history
  already reflects drivers reporting "my earnings show $0" — that would be
  further corroborating evidence but wasn't checked.
- The separate `grand_total`-inconsistency observation made while
  inspecting Alexander Gavu's migrated rides (a billing field appearing
  lower than `total_fare`/`driver_earnings` on several rows) is unrelated to
  this fix and was not investigated further here.
