# Change Impact & Risk Log — Data Transfer module: search endpoint (Phase 3.1)

## Issue/gap identified
Nothing in the module lets an admin find which users/drivers to export or
import against — Phase 1/2's routes require entity IDs the caller already
knows. The stated requirement was a unified fuzzy text + date-range search
with a "select all matching filter" affordance.

## Root cause
Deliberate phasing — search was always Phase 3, after both directions of
the entity pipeline existed to search *for*.

## Fix/remediation
- New `backend/routes/admin/data_transfer_search.py`: `GET /admin/data-transfer/search`.
  Combines fuzzy text (`$regex`/`$options:"i"` — the same ILIKE-based operator
  `db_supabase`'s filter DSL already exposes, no new extension or index
  dependency) across `full_name`/`email`/`phone` via `$or`, plus an optional
  `created_at` date range via `$gte`/`$lte`. Scoped to `users` (all),
  `users` filtered to `role=rider`, or `drivers`, via `entity_type`. Returns
  a `total_count` computed with `db_supabase.count_documents` (PostgREST
  `count="exact"` head-count) rather than fetching every matching row —
  critical for "select all N matching this filter" on tables that can hold
  thousands of rows; the first draft of this endpoint fetched full row sets
  just to `len()` them, caught and fixed before commit (see Verification).
- Modified `backend/routes/admin/__init__.py`: registers the router, gated
  by the same `bulk_operations` module as the rest of this admin module.
- New `backend/tests/test_data_transfer_search.py`: unit tests for
  `_text_filter`/`_build_filters` (pure filter-construction functions) —
  covers text-only, date-only, combined, and empty-criteria cases.

## Risk & impact on existing functionality
Blast radius: `data_transfer_search.py` is a new file, only reachable via
its own new route. `db_supabase.get_rows`/`count_documents` are read-only
calls against `users`/`drivers` using the same filter DSL every other
Supabase-backed admin search in this codebase already relies on ($regex,
$or, $gte/$lte are pre-existing operators in `_apply_filters`, not new
code) — this endpoint adds a new *caller* of that DSL, not new DSL behavior.
`routes/admin/__init__.py` again gets only additive lines. No write path
exists on this endpoint at all (GET-only), so the worst-case failure mode is
a slow/empty search result, not data corruption.

## User experience effect
None yet — no frontend calls this endpoint (Phase 3.2).

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/routes/admin/data_transfer_search.py` | New: unified search route | Search & Select tab backend |
| `backend/routes/admin/__init__.py` | +2 lines: import + `include_router` | Wire in, module-gated |
| `backend/tests/test_data_transfer_search.py` | New: unit tests for filter construction | Coverage for the one genuinely unit-testable piece of this route |

## Before/after snippet
```python
# caught during self-review, fixed before commit:
# before (would fetch every matching row just to count them):
total_rows = await db_supabase.get_rows("users", filters, columns="id")
"total_count": len(total_rows or [])

# after (head-count only, PostgREST count="exact"):
total_count = await db_supabase.count_documents(table, table_filters)
"total_count": total_count
```

## Rollback plan
Remove the two added lines in `routes/admin/__init__.py`, delete
`data_transfer_search.py` and its test file. No other code depends on this
route yet (grep-confirmed) — read-only endpoint, nothing to undo data-wise.

## Verification performed
- `python3 -m py_compile` on all three files — passes.
- Self-caught and fixed the full-row-fetch-for-counting anti-pattern
  (CLAUDE.md explicitly calls out "reading full ride list on dashboards
  without pagination" as an SLA-breaching anti-pattern) before committing,
  by finding and using the existing `count_documents` helper instead of
  writing a new one.
- Confirmed `$regex`/`$options`/`$or`/`$gte`/`$lte` are real, pre-existing
  operators in `repositories/_base.py::_apply_filters` (not assumed) by
  reading the operator dispatch directly.
- Attempted `python3 -m pytest backend/tests/test_data_transfer_search.py`
  — no pytest installed in this session's environment.

## What was NOT verified
- The new unit test file was NOT actually run — no test runner available in
  this environment (confirmed earlier this session: no venv, `pip install`
  was skipped at session start). The tests are straightforward pure-function
  assertions on dict shapes I traced by hand against `_text_filter`/
  `_build_filters`' logic, but "traced by hand" is not "executed and green."
- The route handler itself (DB round-trip, pagination, `count_documents`
  call) has no test — only the filter-construction helpers are covered.
  A route-level test would need a heavier fake-Supabase-client fixture
  (like `test_driver_import_service.py`'s `_FakeQuery`) that wasn't built
  in this subtask to keep it to 3 files.
- Not exercised against a real Supabase instance — the `$regex`-based ILIKE
  search's actual performance/correctness on production-sized `users`/
  `drivers` tables is unverified.
