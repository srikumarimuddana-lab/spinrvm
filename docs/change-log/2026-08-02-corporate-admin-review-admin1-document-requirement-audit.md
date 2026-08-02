# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin, drivers |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — Admin #1 |

## 1. Issue / gap identified

The 4 document-requirement CRUD endpoints in `routes/admin/documents.py`
(`GET/POST /documents/requirements`, `PUT/DELETE
/documents/requirements/{id}`) never resolved the calling admin's
identity (no `admin: dict = Depends(get_admin_user)` parameter) and
never called `log_admin_action`. Document requirements govern which
documents (license, insurance, background check, etc.) a driver must
upload before they can go online — a regulatory-relevant configuration
surface — yet creating, editing, or deleting one left zero audit trail:
no record of who changed it or when.

## 2. Root cause

Authentication/authorization for this router is correctly enforced at
the mount level (`admin_router` requires `get_admin_user`;
`documents_router` additionally requires `require_module("documents")`
— confirmed via `routes/admin/__init__.py`), so these endpoints were
never actually reachable without valid admin credentials. But because
neither `get_admin_user` nor `log_admin_action` were referenced *inside*
the handler functions themselves, there was no way for the handler to
know *which* admin made the change, and consequently no audit call was
ever wired in — unlike the driver-document review and upload endpoints
in this same file, which already call `log_admin_action` correctly.

## 3. Fix / remediation

- Added `admin: dict = Depends(get_admin_user)` to the create, update,
  and delete handlers. FastAPI's per-request dependency caching means
  this doesn't re-run `get_admin_user`'s DB/JWT verification — it's
  already been resolved once by the router-level dependency for this
  same request and is reused.
- Added `await log_admin_action(admin, action, "document_requirements",
  requirement_id, details)` calls after each mutation succeeds, matching
  this file's own existing pattern (bare call, no try/except — consistent
  with `log_admin_action`'s own documented never-raise contract) used by
  `admin_review_driver_document` and `admin_upload_driver_document` a few
  hundred lines below.
- `admin_update_document_requirement` only logs when there's an actual
  update to apply (mirrors the existing `if updates:` guard that already
  skips the DB write for an empty body) — an empty PATCH-style body
  doesn't spam the audit trail with a no-op.
- Left the `GET /documents/requirements` list endpoint unchanged — reads
  aren't audit-logged anywhere else in this codebase, only mutations.

## 4. Risk & impact on existing functionality

- **Blast radius: 3 handler signatures + 3 new log calls, one file.**
  Grepped every test file referencing these functions or the
  `/documents/requirements` route — `test_documents.py`'s existing
  `TestDocumentRequirements` class tests the underlying
  `db_supabase.insert_one`/`update_one`/`delete_one` helpers directly,
  never the route handlers, so it's entirely unaffected by this change.
  `test_get_document_requirements_endpoint` (the only existing HTTP-level
  test touching this router) only hits the unmodified GET.
- No change to request/response shape for any of the 3 endpoints — the
  new `admin` parameter is dependency-injected, not a request body field.
- Added a dedicated new test class
  (`TestDocumentRequirementAuditLogging`, 4 tests) with its own
  `get_admin_user` override granting the `documents` module (the shared
  `admin_override` fixture only grants `corporate_accounts`, which would
  403 against this module-gated router) — covering create, update
  (with and without an actual change), and delete.

## 5. User-experience effect

**Internal admin-facing only.** No change to what any admin can do or
see in the requirements management UI — every existing create/edit/
delete action still succeeds identically. The only difference is that
`audit_logs` now records who made each change and what changed, visible
via the admin dashboard's audit-logs page.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/documents.py` | Added `admin: Depends(get_admin_user)` + `log_admin_action` calls to the create/update/delete document-requirement handlers | Close the audit-trail gap on a regulatory-relevant configuration surface |
| `backend/tests/test_documents.py` | New `TestDocumentRequirementAuditLogging` class (4 tests) with a dedicated `documents`-module admin override | Cover the new audit calls, including the empty-body no-op case |

## 7. Before / after

```python
# Before — no admin identity, no audit trail
@router.delete("/documents/requirements/{requirement_id}")
async def admin_delete_document_requirement(requirement_id: str):
    await db_supabase.delete_one("document_requirements", {"id": requirement_id})
    return {"message": "Document requirement deleted"}
```

```python
# After
@router.delete("/documents/requirements/{requirement_id}")
async def admin_delete_document_requirement(requirement_id: str, admin: dict = Depends(get_admin_user)):
    await db_supabase.delete_one("document_requirements", {"id": requirement_id})
    await log_admin_action(admin, "document_requirement_deleted", "document_requirements", requirement_id, {})
    return {"message": "Document requirement deleted"}
```

## 8. Rollback plan

Plain code change, no migration, no data written beyond new rows in the
already-existing, append-only `audit_logs` table. `git revert` fully
restores the prior (silent) behavior. No feature flag — closing an
observability gap on an already-shipped, already-authenticated endpoint
has no meaningful dark-ship version.

## 9. Verification performed

- [x] Automated tests: `test_documents.py` (46, incl. 4 new) — run via
      the session's `/tmp/spinr_venv` venv from repo root.
- [x] `ruff check` on both touched files — clean.
- [x] Blast-radius grep performed (see §4): every test referencing the 4
      endpoints or the underlying db helpers.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Dry-run scenario: an admin deletes a document requirement via
      `DELETE /admin/documents/requirements/req_1`. Before this fix: the
      row is deleted with zero trace of who did it. After this fix: an
      `audit_logs` row (`document_requirement_deleted`, resource_id=
      `req_1`, actor=the deleting admin) is written in the same request.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — every dependent test grepped
      and run
- [x] No silent behavior change to a working flow — every existing
      create/update/delete action still succeeds identically; the only
      addition is a non-blocking audit-log write

## What was NOT verified

Not tested against a live/staging Supabase — only mocked
`db_supabase`/`log_admin_action` calls. Did not audit the rest of
`routes/admin/documents.py` (or other admin route files) for further
audit-logging gaps beyond the 4 named endpoints — the review finding
named these specifically; a broader sweep for the same pattern elsewhere
is a reasonable follow-up, not performed here.
