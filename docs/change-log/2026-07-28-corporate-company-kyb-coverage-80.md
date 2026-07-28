# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude Code (claude-sonnet-5), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1b — corporate module coverage gap; continuation of PR #2729 (`corporate_accounts.py` 77%→82%) |

## 1. Issue / gap identified

`backend/routes/corporate_company_kyb.py` (company-portal KYB verification endpoints: derived
state, signed upload URL, submit/confirm) was measured at ~32-33% coverage against the ≥80%
target CLAUDE.md sets for `routes/corporate_*.py` (money-adjacent tier, same as rides/dispatch).

## 2. Root cause

The existing `tests/test_corporate_company_kyb.py` only covered the happy paths and the two
guard-rail branches called out in the file's own docstring (tenancy/traversal check on `submit`,
staff-suspended 409). It did not exercise: the 404-missing-company branch on `GET /kyb`, the
upload-url happy path or its already-verified 409, or any of the three "DB call failed → 503"
branches (`create_kyb_upload_url` raising, `kyb_object_exists` raising, `set_kyb_document`
returning no row, `update_corporate_account_status` returning no row on resubmit).

## 3. Fix / remediation

Test-only change. Added 9 new unit tests to `backend/tests/test_corporate_company_kyb.py`
covering the branches above, using the same `mock_supabase_client`/`AsyncMock`/`patch(
"routes.corporate_company_kyb.<name>")` pattern already established in the file (which patches
the names imported into the route module, not `db_supabase` directly — matches this file's
existing convention). No application code was touched.

## 4. Risk & impact on existing functionality

- **Blast radius: none.** This PR only adds test cases to an existing test file; it does not
  modify `backend/routes/corporate_company_kyb.py` or any other application file.
- Grepped for other consumers of the functions under test
  (`create_kyb_upload_url`, `get_corporate_account_by_id`, `kyb_object_exists`,
  `set_kyb_document`, `update_corporate_account_status`, `log_admin_action`): all are also used
  by `backend/routes/corporate_accounts.py` (the staff-side KYB review/upload endpoints, covered
  separately by `test_corporate_kyb.py` / `test_corporate_kyb_upload.py` /
  `test_corporate_admin_routes.py`), but those call sites are untouched here and their own tests
  were re-run to confirm no interference (see §9).
- No shared component/hook/utility signature changed, so no other route file is affected by this
  PR's diff.

## 5. User-experience effect

None. Test-only change; no code path a rider, driver, corporate admin, or internal admin
exercises is modified.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_corporate_company_kyb.py` | Added 9 unit tests: `test_get_kyb_state_404_on_missing_company`, `test_upload_url_happy_path_returns_signed_url`, `test_upload_url_already_verified_409`, `test_upload_url_signing_failure_returns_503`, `test_submit_object_exists_check_failure_returns_503`, `test_submit_set_kyb_document_no_row_returns_503`, `test_submit_status_flip_failure_returns_503` | Close coverage gap on `routes/corporate_company_kyb.py` error/edge branches |
| `ACTION_ITEMS.md` | Updated A1b entry: `corporate_company_kyb.py` line changed from "32-33%" to "closed 2026-07-28: 32-33% → 98%" and moved out of the shared low-priority bullet | Keep the coverage backlog entry accurate |
| `docs/change-log/2026-07-28-corporate-company-kyb-coverage-80.md` | New file (this document) | Mandatory Change Impact Log for a fix/gap-closure PR touching a live-tested corporate surface |

## 7. Before / after

Pure additive test code — no behavior-changing diff, so no before/after snippet per the template's own exemption for additive-only changes. Representative addition:

```python
def test_upload_url_signing_failure_returns_503(test_client, rider_override):
    with (
        _admin_guard(),
        patch("routes.corporate_company_kyb.get_corporate_account_by_id", AsyncMock(return_value=_company())),
        patch("routes.corporate_company_kyb.create_kyb_upload_url", AsyncMock(side_effect=Exception("boom"))),
    ):
        resp = test_client.post("/company/c1/kyb/upload-url", json={"content_type": "application/pdf"})
    assert resp.status_code == 503
```

## 8. Rollback plan

`git revert` is sufficient and complete here: this PR adds test files and doc updates only, no
migrations, no live data, no feature flags, no application behavior change. Reverting the commit
fully undoes it with no data-level remediation needed.

## 9. Verification performed

- [x] Automated tests run: `cd backend && python -m pytest tests/test_corporate_company_kyb.py --cov=routes.corporate_company_kyb --cov-report=term-missing -q --no-cov-on-fail` → **17 passed**, coverage **98%** (82 statements, 2 missed — lines 42-43, the `ImportError` dual-import fallback branch, which is untestable under the project's dual-import convention and structurally unreachable under `pytest`'s package-relative import).
- [x] Also ran `tests/test_corporate_kyb.py` and `tests/test_corporate_kyb_upload.py` (the sibling staff-side KYB test files that share the same underlying `db_supabase` functions) alongside it to confirm no cross-file interference — all passed.
- [x] Blast-radius grep performed: `grep -rn "corporate_company_kyb\|create_kyb_upload_url\|kyb_object_exists\|set_kyb_document" backend/routes backend/tests` — confirmed no other route file imports from `corporate_company_kyb.py` itself; the shared `db_supabase` functions are consumed independently by `corporate_accounts.py` (staff-side), whose own tests were unaffected.
- [x] Reviewed against CLAUDE.md conventions: testing conventions (`mock_supabase_client`/`AsyncMock`, patch target matching the route module's own imported names), coverage-minimums table for `routes/corporate_*.py`.
- [ ] Feature-flagged: not applicable — no user-visible/behavior change, test-only PR.
- [ ] Manual repro / staging check: not applicable — no runtime behavior change; verification is the pytest run above.
- **No real production build (`npm run build`) was run** — not applicable, this PR touches only `backend/` Python test files and Markdown docs, no `admin-dashboard`/`rider-app`/`driver-app` frontend surface.

## 10. What was NOT verified

- Not run against a live/real Supabase instance — all DB interactions are mocked via `AsyncMock`/`patch`, per this repo's unit-test convention (`mock_supabase_client` fixture family). No integration-tier test was added.
- The two remaining uncovered lines (42-43, the `except ImportError:` fallback import block) were not exercised and are not expected to be — they're only reachable when the module is imported top-level outside the `backend` package, which is not how `pytest` loads it in this repo; no other file in `backend/tests/` covers this branch for any route module either.
- Did not audit `create_kyb_upload_url`, `kyb_object_exists`, or `set_kyb_document` themselves (the `db_supabase.py` implementations) — this PR is scoped to the route layer only, per the task's file-scoping instruction not to touch other `corporate_*.py` files.

## Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no live-data dependency)
- [x] Blast radius is stated, not assumed: isolated to this file's own test suite; sibling KYB test files re-run and confirmed unaffected
- [x] No silent behavior change — this is a test-only PR, no shipped flow changed, so the UX field is explicitly "none" rather than left blank
