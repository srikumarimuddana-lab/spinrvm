# Change Impact & Risk Log — `admin_update_area_tax` 404 fix

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code (agent-assisted) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/a29-tax-endpoint-404-fix` |
| Related issue or gap ID | ACTION_ITEMS.md A29 (addendum), found while reviewing a duplicate/superseded PR #3773 |

## 1. Issue / gap identified

`PUT /api/admin/areas/{area_id}/tax` (`admin_update_area_tax`, `routes/admin/service_areas.py`) returns an unhandled 500 (`AttributeError: 'NoneType' object has no attribute 'get'`) instead of a clean 404 when `area_id` does not exist.

## 2. Root cause

The function fetched the area row and immediately did `{k: area.get(k) for k in _TAX_FIELDS}` with no existence check. `db_supabase.get_rows(...)` on a nonexistent id returns `[]`, so `area` resolves to `None`, and `None.get(...)` crashes. Worse: when the request body *did* include tax fields, `db_supabase.update_one(...)` (a silent no-op — it matches zero rows) and `log_admin_action(...)` (writing an audit entry claiming a tax change on a nonexistent area) both ran *before* that crash.

This endpoint's sibling in `features.py` (`update_area_tax`) already had the correct 404 guard; this one didn't.

## 3. Fix / remediation

Added an up-front existence check: fetch the area first, `raise HTTPException(404)` immediately if not found, before any write or audit-log call. The row is re-fetched after a successful update (unchanged from before) so the response still reflects the new values.

## 4. Risk & impact on existing functionality

**Blast radius: isolated.** This endpoint is confirmed unreachable from any current frontend (`ACTION_ITEMS.md` A29's own finding — grepped every `.tsx` file across all four surfaces, zero callers). Grepped for other backend callers of `admin_update_area_tax`: none — it's only reached via the FastAPI route itself. No other code path, background loop, or table is affected.

## 5. User-experience effect

None today (no live caller). If this endpoint is ever wired up to a UI, a request for a nonexistent area now gets a clean 404 instead of a 500 with no audit-log side effect for a change that never happened.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/service_areas.py` | `admin_update_area_tax` now checks the area exists before any write/audit call, raising 404 if not | Fixes the unhandled-500 + phantom-audit-entry bug |
| `backend/tests/test_admin_service_areas_coverage.py` | New test for the 404 path (no write, no audit call); fixed an existing test's mock (`{}` → `{"id": "area-1"}`) that was exploiting the missing existence check to pass an empty dict as a "found" row | Regression coverage; the old mock no longer represents a real Supabase row now that existence is actually checked |

## 7. Before / after

```python
# Before
    updates = tax.model_dump(exclude_none=True, exclude={"tax_justification"})
    if updates:
        ...
        await db_supabase.update_one("service_areas", {"id": area_id}, updates)
        await log_admin_action(...)
    area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1))
    return {k: area.get(k) for k in _TAX_FIELDS}
```

```python
# After
    area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1))
    if not area:
        raise HTTPException(status_code=404, detail="Service area not found")

    updates = tax.model_dump(exclude_none=True, exclude={"tax_justification"})
    if updates:
        ...
        await db_supabase.update_one("service_areas", {"id": area_id}, updates)
        await log_admin_action(...)
        area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1))
    return {k: area.get(k) for k in _TAX_FIELDS}
```

## 8. Rollback plan

`git-revert-safe` — pure code change, no migration, no data written or moved.

## 9. Verification performed

- [x] Automated tests: `tests/test_admin_service_areas_coverage.py -k TestAreaTax` (7/7, including the new 404 test) and the full file + `tests/test_features.py` (65/65) run for real via `/tmp/spinr-venv/bin/python -m pytest`, 0 failed.
- [x] Blast-radius grep performed: no other backend caller of `admin_update_area_tax`; endpoint confirmed unreachable from any frontend (per A29's own prior finding).
- [x] Reviewed against CLAUDE.md's "do not silently swallow errors" convention — this fix specifically prevents a phantom audit-log entry for a change that never happened.

## 10. What was NOT verified

- Not run against a real Supabase instance — verified via mocked `db_supabase` unit tests only.
- Not exercised through a live HTTP request (no `TestClient` integration test for this route) — verified at the function-call layer, consistent with the rest of this test file's existing style.
