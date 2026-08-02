# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | docs (runbook) |
| Domain (Sentry tag) | admin, safety |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — SOC #2 |

## 1. Issue / gap identified

`docs/runbooks/data-breach.md` §1b ("Estimate affected user count") gives
a copy-paste SQL snippet that selects `COUNT(DISTINCT user_id) FROM
audit_logs` — but `audit_logs` has no `user_id` column (see migration
06/57: the real columns are `actor_id` and `entity_id`). Run verbatim
during an actual breach — the exact moment this runbook exists for — the
query would fail outright (`column "user_id" does not exist`), costing
time during the mandated 72-hour Privacy Commissioner notification
window while someone has to debug the runbook itself instead of scoping
the breach.

## 2. Root cause

Same underlying drift as SOC #1 (`maintenance.py`'s audit-log search): the
runbook was written against an assumed/older audit_logs shape and never
updated when migration 57 standardized the schema to
`actor_id`/`entity_type`/`entity_id`. Because runbook SQL isn't run in CI
or covered by any test, this kind of drift has no automated way to
surface — it's only caught by inspection or, worse, during a live
incident.

## 3. Fix / remediation

- Changed `COUNT(DISTINCT user_id)` to `COUNT(DISTINCT entity_id)` —
  `entity_id` is the ID of the record the audited action touched, which
  is what "affected user count" is scoping for.
- Added `AND entity_type IN ('users', 'drivers')` — without it,
  `entity_id` would also count non-user entities (rides, corporate
  accounts, staff records, …) that happen to match the same `action LIKE
  '%<endpoint>%'` filter, overcounting or miscounting the affected
  individual total.
- Added an inline comment explaining the `actor_id` vs `entity_id`
  distinction directly in the runbook, so a responder under time pressure
  during a real incident doesn't have to cross-reference the schema to
  understand which column means what.

## 4. Risk & impact on existing functionality

- **Blast radius: one SQL snippet in one runbook, prose-only.** Grepped
  every other `audit_logs` reference in this file — only this one query
  touches the table; no other runbook query needed the same fix.
- No application code, migration, or test touched — this is a
  documentation correctness fix for a manual, human-run incident-response
  procedure. Nothing reads this file programmatically.

## 5. User-experience effect

None for riders/drivers/corporate admins. Internal-only: an
incident responder following this runbook during a future breach now
gets a working, correctly-scoped query on first try instead of hitting a
Postgres error mid-incident.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/runbooks/data-breach.md` | §1b's audit-log scoping query: `user_id` → `entity_id` (with an `entity_type` filter and explanatory comment) | The referenced column doesn't exist in `audit_logs`; the query would fail if run as-is during a real incident |

## 7. Before / after

```sql
-- Before — column does not exist, query fails
SELECT COUNT(DISTINCT user_id)
FROM audit_logs
WHERE created_at BETWEEN '<breach_start>'::timestamptz AND '<breach_end>'::timestamptz
  AND action LIKE '%<endpoint>%';
```

```sql
-- After
SELECT COUNT(DISTINCT entity_id)
FROM audit_logs
WHERE created_at BETWEEN '<breach_start>'::timestamptz AND '<breach_end>'::timestamptz
  AND action LIKE '%<endpoint>%'
  AND entity_type IN ('users', 'drivers');
```

## 8. Rollback plan

Plain markdown edit, no code, no data, no migration. `git revert` fully
restores the prior (broken) text with no side effects.

## 9. Verification performed

- [x] Confirmed `audit_logs`'s real columns against migration 06
      (`entity_type`, `entity_id`) and migration 57
      (`actor_id` added as a proper column) — no `user_id` column exists
      at any point in the table's migration history.
- [x] Confirmed no other query in this runbook references `audit_logs`
      or the same non-existent column.
- [ ] Did not execute the corrected query against a live/staging
      Supabase instance — no staging access; correctness verified by
      schema inspection (migrations 06 + 57) rather than execution.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — only query in the file that
      touches this table
- [x] No silent behavior change to a working flow — the original query
      did not work (nonexistent column); this is a correctness fix to a
      procedure, not a behavior change to shipped software

## What was NOT verified

Did not execute the corrected SQL against a real or staging Supabase
instance to confirm it runs and returns a sensible count — verified by
schema inspection only (cross-referencing migrations 06 and 57's `ALTER
TABLE`/`CREATE TABLE` statements for `audit_logs`). This is a
docs-only, non-code change, so no automated test coverage applies here
and none was added.
