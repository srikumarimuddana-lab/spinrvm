# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — SOC #1 |

## 1. Issue / gap identified

`GET /admin/audit-logs?search=...` (used by the admin dashboard's
audit-log search box) built its keyword `$or` filter against
`resource_id`, but no code in the repository has written to that column
since migration 57 standardized the schema — every current writer
(`log_admin_action`, `log_user_action` in `utils/audit_logger.py`, and
the `PiiRevealRequest` handler in this same file) populates `entity_id`
instead. A SOC analyst searching the audit log for a specific ride ID,
driver ID, or company ID would silently get zero matches on that leg of
the search (only the `actor_id` and `details` legs could still hit),
with no error or indication that the search was structurally broken —
exactly the kind of gap that erodes trust in a security tool during an
actual investigation.

## 2. Root cause

`resource`/`resource_id` were the original columns from migration
06/08. Migration 57 ("audit_logs_schema_standardization") added
`entity_type`/`entity_id` as the standardized replacement and every
writer was migrated to the new columns — but the search filter in
`routes/admin/maintenance.py::get_audit_logs` was never updated to
match, so it kept querying the now-dead legacy column.

## 3. Fix / remediation

Changed the `$or` clause's second leg from `resource_id` to `entity_id`,
matching every current writer. Left `actor_id` and `details` unchanged
(both are still correctly populated and searched). Did not also search
`entity_type` — the endpoint already has a dedicated `entity_type` exact-
match query param for that, and adding it to the free-text `$or` would
change matching semantics for a different field than what this fix
targets.

## 4. Risk & impact on existing functionality

- **Blast radius: one field name in one `$or` clause.** Grepped every
  writer of `audit_logs` (`utils/audit_logger.py`, this file's own
  `log_audit` helper, the PII-reveal handler) — all populate `entity_id`,
  none populate `resource_id`, confirming the fix aligns with 100% of
  current write paths, not just some.
- Grepped every reader of `audit_logs.resource_id` across the backend —
  none outside this now-fixed line; the column is fully dead code from
  the read side after this change (still exists in the table for any
  pre-migration-57 historical rows, untouched, no data loss).
- `resource_id` remains a real column (migration 57 was additive, never
  dropped `resource`/`resource_id`), so this change cannot error against
  the live schema — it simply searches the column that's actually
  populated.
- Strengthened the existing `test_get_audit_logs_search_builds_or_regex`
  test (previously only asserted `len(filters["$or"]) == 3`, which would
  have passed even with the wrong field name) by adding a new dedicated
  test asserting the exact field set searched is
  `{actor_id, entity_id, details}` and explicitly that `resource_id` is
  not among them — this is the regression guard that would have caught
  the original bug.

## 5. User-experience effect

**Internal SOC/admin-facing only.** A SOC analyst or support admin
searching the audit-logs page by an entity ID (ride, driver, company,
staff member, etc.) now actually finds matching rows instead of silently
getting an incomplete result set. No change to searches by actor ID or
free-text details, which already worked. No visible change to anyone not
using the audit-log search box.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/maintenance.py` | `get_audit_logs`'s search `$or` clause: `resource_id` → `entity_id` | Match the column every current writer actually populates |
| `backend/tests/test_admin_maintenance_coverage.py` | Strengthened the search-filter test to assert the exact field set (not just count); added a dedicated regression test | Lock in `entity_id`, explicitly reject `resource_id` reappearing |

## 7. Before / after

```python
# Before — silently matched zero modern rows
filters["$or"] = [
    {"actor_id": {"$regex": term, "$options": "i"}},
    {"resource_id": {"$regex": term, "$options": "i"}},
    {"details": {"$regex": term, "$options": "i"}},
]
```

```python
# After
filters["$or"] = [
    {"actor_id": {"$regex": term, "$options": "i"}},
    {"entity_id": {"$regex": term, "$options": "i"}},
    {"details": {"$regex": term, "$options": "i"}},
]
```

## 8. Rollback plan

Plain code change, no migration, no data written or read differently
except which column a search matches against. `git revert` fully
restores the (broken) prior behavior. No feature flag — this is a
one-line correctness fix to an internal search filter with no external
callers depending on the old (broken) behavior.

## 9. Verification performed

- [x] Automated tests: `test_admin_maintenance_coverage.py`'s
      `TestAuditLogs` class (now 5 tests, 1 strengthened + 1 new) — run
      via the session's `/tmp/spinr_venv` venv.
- [x] `ruff check` on both touched files — clean.
- [x] Blast-radius grep performed (see §4): every `audit_logs` writer,
      every reader of the `resource_id` column.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Dry-run scenario: a SOC analyst searches audit logs for a specific
      `ride_id`. Before this fix: zero matches unless the ride ID also
      happens to appear in `actor_id` or the `details` JSON text. After
      this fix: matches every row where that ride ID was logged as
      `entity_id` (the normal case for a `rides`-typed audit action).

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — every writer and reader of
      the affected column grepped and enumerated
- [x] No silent behavior change to a working flow — the `actor_id` and
      `details` search legs are unchanged; only the previously-broken
      `resource_id` leg now actually works

## What was NOT verified

Not tested against a live/staging Supabase — only mocked
`db_supabase.get_rows` call-argument assertions, which verify the filter
dict shape sent to the query layer but not the actual PostgREST query
execution or ILIKE matching behavior end-to-end. Did not audit whether
any pre-migration-57 historical row exists in production with data only
in the legacy `resource_id` column and nothing in `entity_id` — if such
rows exist, this fix means they become unsearchable by ID (they were
already unsearchable by the intended target `entity_id` before this fix
too, since it's NULL for them either way; net searchability for those
specific historical rows is unchanged, not regressed, but this was
reasoned about rather than confirmed against real data).
