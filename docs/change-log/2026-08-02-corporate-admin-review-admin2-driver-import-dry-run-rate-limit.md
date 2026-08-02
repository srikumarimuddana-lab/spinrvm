# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin, drivers |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — Admin #2 |

## 1. Issue / gap identified

`POST /admin/drivers/import/commit` (bulk CSV driver import) had two
gaps: no rate limit at all, and no requirement that
`POST /admin/drivers/import/validate` (the read-only dry-run) had ever
been called first for the submitted CSV. An admin session (or a buggy
retry loop against either endpoint) could bulk-create driver + user rows
directly, unboundedly, without ever seeing the dry-run report of
warnings/errors — and nothing capped how many times it could happen.

## 2. Root cause

Every comparable bulk-import commit endpoint in this codebase
(`data_transfer_import_commit_limit`, `booking_import_commit_limit`) is
already rate-limited at `10/hour` per the exact reasoning this finding
names ("a compromised or scripted admin session should not be able to
mass-create/mutate accounts unbounded") — the driver-import endpoint was
simply never brought in line with that established pattern. Separately,
no mechanism existed to prove a `validate` call had happened before a
`commit` — the two endpoints were entirely independent, each re-parsing
the CSV on its own.

## 3. Fix / remediation

- **Rate limit**: added `driver_import_commit_limit =
  default_limiter.limit("10/hour")` to `utils/rate_limiter.py` (identical
  rate and reasoning to the two existing precedents) and applied it to
  `commit_driver_import`.
- **Dry-run enforcement**: new `utils/driver_import_token.py` module
  (mirrors the existing `utils/offer_card_token.py` HMAC-SHA256 pattern
  exactly — no new crypto). `validate_driver_import` now mints a
  30-minute token bound to `(batch, sha256(raw CSV bytes), admin.id)` and
  returns it as `validation_token` in the report. `commit_driver_import`
  now requires `validation_token` as a Form field and verifies it before
  doing any plan-building or writes — a missing, expired, or
  batch/CSV/admin-mismatched token is refused with 400.
- This mechanism doubles as a stronger version of the pre-existing
  "CSV changed since validate" check: since the token binds the exact
  byte content (not just row counts), a changed file simply fails to
  verify instead of silently re-validating and potentially clearing.
- **Frontend**: `admin-dashboard/src/lib/api/imports.ts` — added
  `validation_token` to `DriverImportReport` (optional, since the
  locally-reconstructed "commit was refused" report never has one — a
  refusal always requires re-validating anyway) and `validationToken` to
  `DriverImportOptions`, threaded into the commit `FormData`.
  `drivers/import/page.tsx`'s `handleCommit` now passes
  `batch: report.batch` and `validationToken: report.validation_token`
  from the just-completed validate call — previously `batch` was never
  threaded between the two calls at all, so committing with a bound
  token requires this fix too (otherwise commit would auto-generate a
  fresh, non-matching batch and always fail token verification).

## 4. Risk & impact on existing functionality

- **Blast radius: `commit_driver_import`/`validate_driver_import`, one
  new backend token module, one rate-limit constant, the driver-import
  API client + page component.** Grepped every caller of both endpoints
  — only the admin-dashboard's Bulk Import page
  (`drivers/import/page.tsx`); the file's own docstring notes the CLI
  script shares `driver_import_service.py`'s plan-building logic
  in-process, not this HTTP layer, so it's unaffected.
- **Behavior change: `commit` now requires a prior, matching `validate`
  call.** This is the entire point of the fix. Any external caller
  hitting `/commit` directly without first calling `/validate` for the
  same CSV will now get a 400 instead of proceeding. The one legitimate
  in-repo caller (the admin-dashboard page) was updated in the same
  commit to thread the token/batch correctly.
- Existing tests `test_commit_creates_rows`/`test_commit_refuses_on_errors`
  in `test_admin_driver_import.py` called `/commit` directly without a
  token — updated to validate-then-commit via a new
  `_validate_then_commit` helper. Added 4 new tests: missing-token → 422,
  wrong-CSV token → 400, and a dedicated rate-limiter mechanics test
  (against a throwaway `AsyncLimiter`/`MemoryStorage` pair at the same
  10/hour rate, following the same pattern used for the guest-booking
  rate-limit fix earlier in this review).
- Frontend: `tsc --noEmit` initially surfaced one new error (the
  locally-reconstructed refused-commit report object had no
  `validation_token`) — fixed by making the field optional on that
  interface, which is semantically correct (that reconstructed report
  genuinely has no valid token; the only path forward from it is
  re-validating). Full project `tsc --noEmit` re-run afterward: 27
  errors remain, all pre-existing and unrelated (confirmed identical to
  the baseline from an earlier fix in this same review session — missing
  Jest/Vitest type defs in unrelated test files, none referencing these
  two changed files). `vitest run` on the dashboard smoke-test suite (20
  tests, including the driver-import page's render-without-crashing
  test) and `eslint` on both changed files: all clean.

## 5. User-experience effect

**Internal admin-facing only.** No change to the golden path: an admin
who validates then commits (the only flow the UI's "Commit" button
exposes — it's disabled until `report.can_commit` is true, which only
happens after a validate call populates `report`) sees identical
behavior, since the token is threaded transparently. An admin (or script)
attempting to call `/commit` directly without validating first — not
possible through the UI, only via direct API access — now gets a clear
400 instead of silently committing. A legitimate re-validate-then-commit
cycle taking longer than 30 minutes (unlikely for a CSV review) would
need to re-validate; this is a new, narrow constraint, not a regression
of any workflow the UI currently supports.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/driver_import_token.py` (new) | Signed token sign/verify pair bound to (batch, CSV sha256, admin id) | Prove a matching validate call happened before commit |
| `backend/utils/rate_limiter.py` | New `driver_import_commit_limit = default_limiter.limit("10/hour")` | Match the existing bulk-import-commit rate-limit precedent |
| `backend/routes/admin/driver_import.py` | `_read_csv_rows` now returns `(rows, csv_sha256)`; `validate_driver_import` mints and returns a token; `commit_driver_import` requires and verifies it, gains the rate-limit decorator | Close both named gaps |
| `backend/tests/test_admin_driver_import.py` | Updated 2 existing commit tests to validate-then-commit; added 4 new tests (missing token, wrong-CSV token, rate-limiter mechanics) | Cover the new enforcement without breaking existing coverage |
| `admin-dashboard/src/lib/api/imports.ts` | `DriverImportReport.validation_token` (optional), `DriverImportOptions.validationToken`, threaded into `driverImportFormData` | Frontend must supply the token the backend now requires |
| `admin-dashboard/src/app/dashboard/drivers/import/page.tsx` | `handleCommit` now passes `batch` + `validationToken` from the validate response | Without this the commit call could never satisfy the new backend check |

## 7. Before / after

```python
# Before — commit accepted any CSV, no rate limit
@router.post("/drivers/import/commit")
async def commit_driver_import(
    drivers_csv: UploadFile = File(...),
    ...
    admin: dict = Depends(get_admin_user),
):
    rows = await _read_csv_rows(drivers_csv)
    plan = await _build_plan(rows, ...)
    ...
```

```python
# After
@router.post("/drivers/import/commit")
@driver_import_commit_limit
async def commit_driver_import(
    request: Request,
    drivers_csv: UploadFile = File(...),
    ...
    validation_token: str = Form(...),
    admin: dict = Depends(get_admin_user),
):
    rows, csv_sha256 = await _read_csv_rows(drivers_csv)
    try:
        verify_driver_import_token(validation_token, batch=batch, csv_sha256=csv_sha256, admin_id=admin["id"])
    except DriverImportTokenError as e:
        raise HTTPException(status_code=400, detail=f"Validate this CSV before committing...: {e}") from e
    plan = await _build_plan(rows, ...)
    ...
```

## 8. Rollback plan

Backend: plain code change, no migration, no data written differently —
`git revert` fully restores the prior (unenforced, unlimited) behavior.
Frontend: same, `git revert` restores the prior FormData shape. No
feature flag — both endpoints are internal ops tools with a single
in-repo caller that was updated in the same change; there's no external
integration to stage a rollout for.

## 9. Verification performed

- [x] Backend automated tests: `test_admin_driver_import.py` (9, incl. 5
      new/updated), `test_driver_import_service.py` (13),
      `test_import_saskatoon_drivers.py` (6) — 28 passed, run via the
      session's `/tmp/spinr_venv` venv from repo root.
- [x] `ruff check` on all touched backend files — clean.
- [x] Frontend: `tsc --noEmit -p tsconfig.json` — 27 pre-existing,
      unrelated errors only (confirmed via `grep` for the two touched
      file paths — zero matches); `vitest run` on the dashboard
      smoke-test suite — 20/20 passed; `eslint` on both touched TS/TSX
      files — clean.
- [ ] Did NOT run a real production build (`npm run build`) of
      `admin-dashboard` — only `tsc --noEmit` + `vitest` + `eslint`, per
      this review's established lighter-weight verification approach for
      admin-dashboard changes (see the H5 change log for the same
      caveat). No staging access; no live browser click-through
      performed.
- [x] Blast-radius grep performed (see §4): every caller of both
      endpoints; every test file referencing them.
- [x] Dry-run scenario: an admin (or script) calls `/commit` directly
      with a CSV, no `validation_token`. Before this fix: the import
      proceeds, creating driver + user rows with no proof a human ever
      reviewed a dry-run report. After this fix: 422 (missing required
      field) before any parsing happens.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — both endpoints' sole caller
      (the admin-dashboard page) identified and updated in the same
      change; every dependent test grepped and run
- [x] User-experience effect stated: no change to the UI's supported
      golden path (validate → commit via the disabled-until-ready
      button); a new constraint only affects direct API access that
      bypasses the UI, which is the exact gap this fix closes

## What was NOT verified

Not tested against a live/staging Supabase or a real browser
click-through of the Bulk Import page — the frontend fix was verified via
type-checking, lint, and the existing render-smoke test, not a manual
UI session (no browser access in this environment). Did not add a
distinct rate limit to `/validate` (the read-only dry-run) — the finding
named `/commit` specifically as the write path needing the limit,
matching the existing `booking_import_validate_limit`/
`booking_import_commit_limit` precedent where validate gets a looser
limit and commit the tighter one; validate here currently has none at
all, which is consistent with `data_transfer_import_validate_limit`
existing as a separate, distinctly-named limit rather than something
this fix was asked to add. Did not add single-use/replay-prevention to
the validation token (e.g., a server-side consumed-token registry) — the
same valid token can be presented to `/commit` more than once within its
30-minute window; this is acceptable because `commit_plan`'s own
idempotency (matching on `legacy_import_metadata.old_driver_id + source`,
per this file's existing docstring) already makes a repeat commit of the
same batch converge rather than duplicate, so token replay isn't a data-
integrity risk, only (already-documented, pre-existing) UI-level
double-submit risk that the frontend's disable-while-in-flight button
already mitigates.
