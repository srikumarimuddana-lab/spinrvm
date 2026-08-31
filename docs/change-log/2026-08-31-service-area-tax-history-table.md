# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude (agent session, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments (tax config), admin |
| PR / commit link | (local commit — not yet pushed/PR'd) |
| Related issue or gap ID | `ACTION_ITEMS.md` A29, final open sub-item: "No dedicated `service_area_tax_history` (or equivalent) audit table." |

## 1. Issue / gap identified

`admin_update_service_area` (the live path the admin-dashboard's service-areas
page actually calls) writes a generic `tax_config_updated` row to `audit_logs`
whenever GST/PST/HST fields change, but there is no dedicated, queryable,
append-only table capturing tax-rate transitions over time — the way
`driver_insurance_periods` exists for insurance periods. `audit_logs.details`
is an unstructured JSON blob shared with every other admin action type,
making "show me every tax change for area X, with before/after values" an
ad-hoc JSON query today.

## 2. Root cause

A29 previously deferred this specific sub-item as low priority ("audit_logs
already covers the what-changed-and-why need... revisit if tax-rate changes
become more frequent") rather than building it at the time of the original
A29 fix (2026-08-12). The user has now explicitly asked to build it now,
overriding that prior deferral — this change does not reflect a change in
tax-change frequency, it reflects an explicit ask.

## 3. Fix / remediation

1. New migration `backend/migrations/376_service_area_tax_history.sql` —
   append-only `service_area_tax_history` table (old/new gst/pst/hst
   enabled+rate, `changed_by`, `changed_by_role`, `justification`,
   `audit_log_id` cross-reference, `changed_at`). UPDATE/DELETE blocked by
   a `BEFORE UPDATE OR DELETE` trigger (stricter than
   `driver_insurance_periods`, which allows one specific close-transition
   UPDATE — this table has no "open row" concept at all, every row is
   already a finished record). RLS: admin-only SELECT, no client
   INSERT/UPDATE/DELETE grants (service role bypasses by design).
2. `admin_update_service_area` (`backend/routes/admin/service_areas.py`)
   now calls a new `_record_tax_history()` helper immediately after its
   existing `tax_config_updated` `log_admin_action` call, whenever any
   GST/PST/HST field is actually touched. `_record_tax_history` re-reads
   the current `service_areas` row for old values (must run before the
   caller's own `update_one` — the function docstring notes this), then
   inserts one `service_area_tax_history` row per change. Mirrors the
   existing `_record_manual_surge_history` pattern in the same file
   exactly: best-effort, `logger.error(..., exc_info=True)` and swallow on
   failure so a history-write failure never fails the operator's actual
   tax-rate change.
3. New read-only endpoint `GET /service-areas/{area_id}/tax-history`
   (admin-only, `limit` clamped to 1–500) for viewing the history —
   read-only, no separate write path, matching the table's
   append-only-from-one-place contract.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `admin_update_service_area` is the only
  in-repo caller changed. Grepped for other callers of the function itself
  (none — it's an HTTP handler, called only via the router) and for other
  consumers of the `service_areas` table's write path — the two dead
  endpoints (`features.py::update_area_tax`,
  `routes/admin/service_areas.py::admin_update_area_tax`) are NOT touched
  by this change (per A29's prior finding, confirmed still true by a fresh
  grep of `.tsx` files under `admin-dashboard`/`rider-app`/`driver-app` for
  either path: zero callers), so they still only write the pre-existing
  `tax_config_updated` audit_logs row and do not get a
  `service_area_tax_history` row. This is a known, intentional gap — flagged
  as future follow-up (see ACTION_ITEMS.md addition below) rather than
  silently left unstated, since building it out for two unreachable
  endpoints would violate the simplicity-first principle for a path that
  carries no live traffic.
- **The existing `tax_config_updated` audit_logs write is unchanged** —
  additive change, no removal, no altered fields on that call.
- **New table, no existing reader/writer.** Nothing else in the codebase
  references `service_area_tax_history` before this change (grepped —
  zero prior hits).
- **Failure isolation:** a `service_area_tax_history` insert failure (e.g.
  RLS misconfiguration, transient DB issue) is caught and logged, never
  raised — the admin's tax-rate change and its `tax_config_updated`
  audit_logs row still succeed. Verified with a dedicated test
  (`test_history_write_failure_does_not_fail_the_tax_change`).
- **No interaction with background loops, ride state machine, or wallet
  deltas.** Pure admin-config audit trail.

## 5. User-experience effect

Internal-admin-facing only. No rider/driver/corporate-admin visible change.
The one new surface (`GET .../tax-history`) is a read-only endpoint with no
admin-dashboard UI wired to it yet (kept minimal per the task scope — a full
history-viewer UI was explicitly out of scope). Not visible mid-session to
any rider/driver.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/376_service_area_tax_history.sql` | New append-only audit table + indexes + RLS + immutability trigger | Dedicated, queryable tax-change history (A29) |
| `backend/routes/admin/service_areas.py` | `admin_update_service_area` now calls new `_record_tax_history()` helper after its existing `tax_config_updated` audit_logs write; new `GET /service-areas/{area_id}/tax-history` read endpoint | Wire the write path; minimal read surface |
| `backend/tests/test_admin_service_areas_coverage.py` | New tests: history row written with correct old/new values, write-failure swallowed without failing the tax change, non-tax updates don't write a row, and 2 tests for the new GET endpoint | Coverage for the new write + read paths |
| `ACTION_ITEMS.md` | A29's tax-history sub-item marked done, linked to this log | Close the tracked backlog item |

## 7. Before / after

```python
# Before (backend/routes/admin/service_areas.py, admin_update_service_area)
        await log_admin_action(
            admin,
            "tax_config_updated",
            "service_areas",
            area_id,
            {"updated_fields": _tax_fields_touched, "justification": tax_justification},
        )
```

```python
# After
        _tax_audit_id = await log_admin_action(
            admin,
            "tax_config_updated",
            "service_areas",
            area_id,
            {"updated_fields": _tax_fields_touched, "justification": tax_justification},
        )
        # A29 (ACTION_ITEMS.md): additive, dedicated append-only history row —
        # complements (does not replace) the tax_config_updated audit_logs
        # write above. See migration 376 / service_area_tax_history.
        await _record_tax_history(area_id, area, _tax_fields_touched, tax_justification, admin, _tax_audit_id)
```

The `audit_logs` write itself is byte-for-byte unchanged; only a new
statement was appended after it.

## 8. Rollback plan

- **Code**: `git revert` is sufficient for the route change — it is purely
  additive (a new function call + a new endpoint); reverting drops both
  with no data-consistency concern, since `service_area_tax_history` is a
  brand-new table nothing else reads.
- **Migration**: rollback SQL is in the migration's own top-of-file
  comment:
  ```sql
  DROP TRIGGER IF EXISTS service_area_tax_history_no_mutate ON service_area_tax_history;
  DROP FUNCTION IF EXISTS _service_area_tax_history_immutable();
  DROP TABLE IF EXISTS service_area_tax_history;
  ```
  Safe to run at any time — no other table has a foreign key into
  `service_area_tax_history`, and no background loop reads it.

## 9. Verification performed

- [x] Automated tests run: `python3 -m pytest backend/tests/test_admin_service_areas_coverage.py -q --no-cov` — **69/69 passed** (5 new: 3 for `_record_tax_history`'s write path, 2 for the new GET endpoint; the other 64 are the file's pre-existing suite, re-run to confirm no regression).
- [x] `ruff check` and `ruff format --check` on both touched Python files — clean.
- [x] Migration applied against a scratch local Postgres 16 instance (`sudo -u postgres psql -d spinr_migration_scratch -f backend/migrations/376_service_area_tax_history.sql`, with stub `users`/`service_areas`/`auth.uid()` — table, indexes, RLS policy, and trigger all created without error. Manually verified: an INSERT with old/new tax values succeeds and is readable; a subsequent UPDATE is correctly rejected by the immutability trigger (`service_area_tax_history rows are append-only and cannot be updated`). Scratch database dropped after verification.
- [x] Manual checklist review against `spinr-migration-reviewer`'s convention list (Agent/Task tool unavailable in this session, so the checklist in `.claude/agents/spinr-migration-reviewer.md` was applied manually): numbering OK (376, next free after 375), append-only OK (new file, no edits to merged migrations), RLS OK (admin-only SELECT, no client write grants), reversibility OK (rollback comment present), forward-compat OK (new table only, no ALTER/lock on hot tables), indexes OK (match the two query patterns the new GET endpoint and an admin-by-actor lookup would use), money safety N/A, retention OK (no CASCADE; stricter-than-`driver_insurance_periods` full-immutability trigger). No blockers or warnings found.
- [x] Blast-radius grep performed: `admin_update_service_area` has no other in-repo callers; `service_areas` table write paths outside this function (the two dead `/areas/{id}/tax` endpoints) confirmed still unreachable from any `.tsx` file, so intentionally left untouched.
- [x] Reviewed against relevant CLAUDE.md conventions: append-only migration rule, RLS-first, "do not silently swallow errors" (write failures are `logger.error(..., exc_info=True)`, not swallowed silently — only the *outcome* of a failed history write doesn't propagate, matching the existing `_record_manual_surge_history` precedent CLAUDE.md's own codebase already established for this exact trade-off).
- [ ] Feature-flagged: not applicable — backend-only additive audit table + admin-only endpoint, no user-visible or non-trivial behavior change to gate.

## 10. What was NOT verified

- **Not run against a real production/staging Supabase.** `run_migrations.py --dry-run` was attempted first and failed immediately on a missing `DATABASE_URL` (no live DB access in this environment) — verification instead used a local scratch Postgres 16 database (created and dropped within this session, never touched production) to confirm the migration's SQL is syntactically valid and its RLS/trigger behavior is correct. The real Supabase RLS policies (role lookup via the actual `users` table, `auth.uid()` behavior under real Supabase Auth) were not exercised — only stubbed locally.
- **No admin-dashboard UI was built** for the new `GET /service-areas/{area_id}/tax-history` endpoint — deliberately out of scope per the task ("keep this minimal... not a full UI feature"). The endpoint exists and is tested at the Python-function level only; it was not called through a real HTTP request against a running server, and no `npm run build` was run (no frontend files were touched).
- **The two dead `/areas/{id}/tax` endpoints were not wired to write history rows.** They remain unreachable from any frontend as of this writing (re-confirmed by grep), so this is believed to be zero live risk, but it is a known gap if either endpoint is ever wired up in the future without also being updated.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (migration rollback SQL provided; code change is a clean additive revert)
- [x] Blast radius is stated, not assumed (isolated to `admin_update_service_area`; other `service_areas`-tax-write paths explicitly checked and found still dead)
- [x] No silent behavior change to an already-shipped flow — the existing `tax_config_updated` audit_logs write, its required-justification gate, and the update's success/failure shape are all unchanged; the only user-visible addition is a new admin-only read endpoint nothing currently calls
