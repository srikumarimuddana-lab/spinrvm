# Change Impact & Risk Log — `update_one` / `delete_many` refuse an empty filter

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code session (2026-09-20 full-repo review, Phase 0 item C3) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides / payments / auth / corporate (shared DB layer — every domain) |
| PR / commit link | branch `claude/loving-thompson-amjk8b` |
| Related issue or gap ID | 2026-09-20 review, 🚨 C3 |

## 1. Issue / gap identified

`repositories/_base.py::_apply_filters` returns the unfiltered query builder for an empty/`None` filter dict. For reads that is a full-table scan; for `update_one` (non-upsert) and `delete_many` it is a table-wide `UPDATE`/`DELETE`. The module already raises on a degenerate `$or` for exactly this reason (`_base.py` — "applying no filter would instead match the ENTIRE table, and on an update/delete would write it") but the top-level `{}` case was unguarded. No caller passes a literal `{}` today (grepped), but any conditionally-built filter that collapses to `{}` would silently truncate a table.

## 2. Root cause

The `$or` guard was added for a specific incident shape; the general case was never closed.

## 3. Fix / remediation

`_require_write_filters(op, table, filters)` raises `ValueError` from `update_one` (unless `upsert=True`, whose empty filter is a plain insert-or-update keyed by the payload) and from `delete_many` (`delete_one` delegates). The guard runs *before* the `if not supabase` short-circuit so the bug surfaces in dev/test too rather than degrading to the silent `_write_skipped` path.

**Alternative considered:** raising inside `_apply_filters` itself. Rejected — reads legitimately pass `{}` (`utils/auto_payout.py::_fetch_candidate_drivers` pages the whole `drivers` table), so the guard belongs on the write helpers.

## 4. Risk & impact on existing functionality

- Blast radius: every caller of `update_one` / `delete_many` / `delete_one` (hundreds). A caller that today issues an empty-filter write would now get a `ValueError` (500 through the route) instead of silently rewriting a table. Grep found **zero** literal `{}`/`None` filter writes in `routes/`, `services/`, `utils/`, and zero in `tests/`; `upsert=True` callers (9) are exempt by design.
- The exception type is `ValueError`, matching the existing `$or` guard, so any caller already catching that for the `$or` case behaves the same.
- No behaviour change for any non-empty filter. No DB, state-machine, or money effect.

## 5. User-experience effect

None. Backend-only; a triggered guard means a bug that would otherwise have destroyed data.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/repositories/_base.py` | `_require_write_filters` + calls at the top of `update_one` (non-upsert) and `delete_many` | refuse table-wide writes |
| `backend/tests/test_write_helpers_refuse_empty_filters.py` | new: guard on `{}`/`None` for all three helpers, with and without a client; upsert exemption; positive anchor | regression pin |
| `docs/change-log/2026-09-20-write-helpers-refuse-empty-filters.md` | this file | |

## 7. Before / after

```python
# Before
async def update_one(table, filters, update, upsert=False):
    if not supabase: ...
    await _pre_invalidate_for_table(table, filters)
    ...q = _apply_filters(q, filters)   # {} → no WHERE clause
```

```python
# After
async def update_one(table, filters, update, upsert=False):
    if not upsert:
        _require_write_filters("update_one", table, filters)   # {} / None → ValueError
    if not supabase: ...
```

## 8. Rollback plan

Code-only; `git revert` + deploy. No data touched.

## 9. Verification performed

- `ruff check` / `ruff format` / `py_compile` clean.
- Grep for empty-filter write callers (none).

## 10. What was NOT verified

- **pytest not run in this session** (sandbox cannot reach PyPI). The full suite in CI is the real check here — it is the only thing that would reveal a caller relying on `{}` semantics that grep missed.
