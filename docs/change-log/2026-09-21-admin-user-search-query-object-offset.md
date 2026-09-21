# Change Impact & Risk Log — Admin User Search Query Object Offset

| Field | Value |
|---|---|
| **Date** | 2026-09-21 |
| **PR** | (this commit) |
| **Sentry issue** | CRIMSON-SMOKE-7445-WV |

## Issue/gap identified

`POST /api/admin/users/search` crashes with `TypeError: unsupported operand type(s) for +: 'Query' and 'int'` — 18 occurrences since 2026-09-15, escalating.

## Root cause

`admin_search_users` (POST endpoint) calls `admin_get_users` (GET endpoint) directly as a Python function, passing `limit` but not `offset`. When called directly (not via HTTP), FastAPI does not resolve `Query(0, ge=0)` to `0` — the parameter receives the raw `Query` descriptor object. `get_rows` then calls `q.range(offset, offset + limit - 1)`, and `Query(0) + int` throws `TypeError`.

## Fix/remediation

Pass `offset=0` explicitly in the direct call from `admin_search_users` to `admin_get_users`.

## Risk & impact on existing functionality

- **Blast radius**: isolated — only `admin_search_users` calls `admin_get_users` directly; the GET `/admin/users` endpoint is unaffected (FastAPI resolves `Query()` defaults for HTTP requests).
- **Other callers of `admin_get_users`**: the GET route (HTTP, unaffected) and one test (`test_admin_users_search.py`, already passes `offset=0`).
- **No behavior change** for the GET endpoint or any other surface.

## User experience effect

**admins** — admin dashboard's user search typeahead was returning 502 errors. Now works correctly.

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/routes/admin/users.py` | Added `offset=0` to the `admin_get_users()` call in `admin_search_users` | Prevents `Query` object from reaching the DB layer |
| `backend/tests/test_admin_users_search.py` | Added `test_post_search_passes_int_offset` regression test | Asserts both `offset` and `limit` are `int` when called via the POST endpoint |
| `docs/change-log/2026-09-21-admin-user-search-query-object-offset.md` | This file | Change Impact Log |

## Before/after snippet

```python
# BEFORE (line 158)
return await admin_get_users(role=body.role, search=body.search, limit=body.limit)

# AFTER
return await admin_get_users(role=body.role, search=body.search, limit=body.limit, offset=0)
```

## Rollback plan

`git revert` — pure additive fix, no schema or state change.

## Verification performed

- New regression test `test_post_search_passes_int_offset` reproduces the bug (fails without the fix, passes with it)
- All 24 admin user tests pass (`test_admin_users_search.py` + `test_admin_users_management.py`)
- `ruff check` and `ruff format` clean

## What was NOT verified

- Not tested against live Supabase (mock-only)
- No visual regression tooling for admin dashboard user search UI
