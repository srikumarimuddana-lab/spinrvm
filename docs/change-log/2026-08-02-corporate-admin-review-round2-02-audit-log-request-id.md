# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "no correlation ID" finding |

## 1. Issue / gap identified

An admin action recorded in `audit_logs` had no way to be correlated back
to the Sentry error it may have caused, or the log lines from the same
request — a SOC investigation starting from "admin X changed Y" or from a
Sentry event had no shared key to jump between them.

## 2. Root cause

The correlation ID itself already existed everywhere except the one place
that needed it. `core/middleware.py`'s `RequestIDMiddleware` mints/reads
`X-Request-ID` on every request and binds it into loguru's context (so
every log line in that request already carries it), and
`utils/sentry_scrub.py::tags_from_log_extra` already promotes a
`request_id` log-extra key onto Sentry event tags. There was even an
unused, fully-built `utils/log_context.py` (a `ContextVar`-backed
`get_request_id()`/`set_request_context()` pair) that nothing ever called
— dead infrastructure from an earlier, incomplete pass. The only actual
gap was `utils/audit_logger.py::log_admin_action` never reading it, and
`audit_logs` having no column to hold it.

## 3. Fix / remediation

- New migration `278_audit_logs_request_id.sql` — additive, nullable
  `request_id TEXT` column on `audit_logs` + a partial index (`WHERE
  request_id IS NOT NULL`) for the SOC lookup pattern ("find the admin
  action behind this request_id").
- `core/middleware.py::RequestIDMiddleware` now calls
  `set_request_context(request_id, user_id)` (both values it already
  computes) right alongside its existing `logger.contextualize()` call —
  wiring up the previously-dead `log_context` module rather than building
  a new one.
- `utils/audit_logger.py::log_admin_action` now reads `get_request_id()`
  and writes it to the new column, stored as `NULL` (not `""`) when
  called outside a request (e.g. from a background loop), so the partial
  index stays meaningful.
- `log_user_action` gets this for free — it's a thin wrapper around
  `log_admin_action`.

## 4. Risk & impact on existing functionality

- **Blast radius: one column, one middleware, one function.** Grepped
  every caller of `log_admin_action`/`log_user_action` (~50+ call sites
  across `routes/admin/**`, `routes/corporate_*`, `routes/rides/**`) —
  none pass `request_id` today, none need to change; the new field is
  populated entirely from context, transparently.
- Grepped every reader of `audit_logs` (`routes/admin/maintenance.py`'s
  `get_audit_logs`, `docs/runbooks/data-breach.md`'s scoping SQL, the two
  fixed earlier this session) — none select an explicit column list that
  would need updating; all use `select("*")` or would simply gain a new
  field.
- **Deployment ordering**: this repeats the exact shape of migration 84
  (`84_audit_logs_missing_columns.sql`, same table, same "insert now
  references a column PostgREST doesn't know about yet" risk). Per that
  migration's own precedent, this codebase's deploy pipeline runs
  migrations before the new backend code goes live, so the column exists
  before any code path tries to write it. If the ordering were ever
  inverted, `log_admin_action`'s existing try/except still catches the
  `PGRST204` and logs-but-doesn't-raise — no admin action would be
  blocked, only its audit row would silently fail to write (identical
  fallback behavior to any other audit-log write failure today, not a new
  failure mode this change introduces).
- `RequestIDMiddleware`'s existing behavior (X-Request-ID/X-Trace-ID
  headers, loguru context) is unchanged — this only adds one more line
  setting the ContextVar already defined for exactly this purpose.
- ContextVar is per-asyncio-Task; each request runs in its own Task, so
  concurrent requests do not see each other's `request_id`/`user_id` —
  verified by reasoning through `set_request_context`'s existing
  (unmodified) implementation, which never claimed otherwise.

## 5. User-experience effect

None. This is a purely internal SOC/observability improvement — no
request, response, or admin-facing UI changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/278_audit_logs_request_id.sql` (new) | Additive `request_id` column + partial index on `audit_logs` | Give the correlation ID somewhere to land |
| `backend/core/middleware.py` | `RequestIDMiddleware` now also calls `set_request_context(request_id, user_id)` | Wire the already-built-but-dead `log_context` module into the request lifecycle |
| `backend/utils/audit_logger.py` | `log_admin_action` reads `get_request_id()` and writes it (as `NULL` when empty) | Close the actual gap — the one place the correlation ID was never recorded |
| `backend/tests/test_utils_extended.py` | 3 new tests: request_id recorded from context, NULL (not `""`) outside a request, middleware-to-context wiring end-to-end | Lock in both halves of the fix |

## 7. Before / after

```python
# Before — utils/audit_logger.py
await db_supabase.insert_one(
    "audit_logs",
    {
        "id": audit_id,
        "action": action,
        "entity_type": resource,
        "entity_id": resource_id,
        "actor_id": admin["id"],
        "details": {...},
        "created_at": ...,
    },
)
```

```python
# After
request_id = get_request_id() or None
await db_supabase.insert_one(
    "audit_logs",
    {
        "id": audit_id,
        "action": action,
        "entity_type": resource,
        "entity_id": resource_id,
        "actor_id": admin["id"],
        "request_id": request_id,
        "details": {...},
        "created_at": ...,
    },
)
```

## 8. Rollback plan

`git revert` the code changes. For the migration: `DROP INDEX IF EXISTS
idx_audit_logs_request_id; ALTER TABLE audit_logs DROP COLUMN IF EXISTS
request_id;` (also stated in the migration's own top comment). No data
migration in either direction — the column is purely additive and no
existing behavior reads it yet.

## 9. Verification performed

- [x] 3 new tests added covering: `request_id` correctly recorded from an
      active context, `NULL` (not empty string) when no context is set,
      and the middleware-to-`get_request_id()` wiring end-to-end via a
      throwaway FastAPI app + `TestClient`.
- [x] `python3 -c "import ast; ast.parse(...)"` on all 3 touched Python
      files — clean.
- [x] Blast-radius grep performed (see §4): every `log_admin_action`/
      `log_user_action` call site and every `audit_logs` reader.
- [x] Traced the exact precedent (migration 84) for the
      insert-references-new-column deployment-ordering risk and confirmed
      this fix's failure mode (silent audit-write failure, not a blocked
      action) matches the codebase's existing accepted posture for that
      risk shape.
- [ ] Did not run the test suite or any CI gate for this individual fix —
      per explicit instruction, tests/CI run once at the end of this
      round, not per item.
- [ ] Did not run against a live Postgres instance — the migration was
      read and structurally verified (mirrors migration 84's exact
      `ADD COLUMN IF NOT EXISTS` shape) but not executed.

## 10. Sign-off

- [x] Rollback plan is concrete — `git revert` + the migration's own
      documented down-SQL
- [x] Blast radius is stated, not assumed — every caller and every reader
      of the touched table grepped and confirmed unaffected
- [x] No silent behavior change to a working flow — every existing
      `log_admin_action` call site behaves identically; the only change is
      one new, previously-absent field on the row it already wrote

## What was NOT verified

Not run against a live Postgres/Supabase instance — migration correctness
was verified by structural comparison to the established
`84_audit_logs_missing_columns.sql` precedent, not by actually applying
it. Did not audit whether any *other* dead-code path besides
`RequestIDMiddleware` should also call `set_request_context` (e.g.
WebSocket connections, background loops) — those don't go through this
middleware today and were out of scope for this fix, which closes the gap
named in the finding (HTTP admin actions) rather than extending
correlation IDs to every surface in the codebase.
