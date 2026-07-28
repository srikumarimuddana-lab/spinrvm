# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | (this branch: `claude/kyb-submit-audit-log-non-blocking`) |
| Related issue or gap ID | #2740 |

## 1. Issue / gap identified

In `backend/routes/corporate_company_kyb.py`, `kyb_submit` called `log_admin_action(...)` with no `try/except`. If that call raised (e.g. a transient DB error writing the audit row), the entire `/company/{company_id}/kyb/submit` request failed with a 5xx — even though the actual KYB work (`set_kyb_document`, and the status flip to `pending_verification` on resubmit) had already succeeded and committed.

## 2. Root cause

The endpoint was written without following the "audit-log-failure-should-not-fail-request" pattern already established elsewhere in the corporate module (`corporate_accounts.py`'s `kyb_document_confirm`, `kyb_review`, `create_corporate_account`, `update_corporate_account`, `delete_corporate_account`, `change_company_status` all wrap their `log_admin_action` call in `try/except Exception: logger.error(...)`). This one handler was simply missed when that convention was applied — discovered as a byproduct of unrelated test-coverage-closure work on this file (PR #2739).

## 3. Fix / remediation

Wrapped the `log_admin_action(...)` call in `kyb_submit` in `try/except Exception:`, logging the failure via `logger.error(..., exc_info=True)` (not silently swallowed — CLAUDE.md requires DB/audit errors to surface loudly) but still returning the success response to the caller. Matches the exact pattern already used in `corporate_accounts.py`.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated**. Only `kyb_submit` in `backend/routes/corporate_company_kyb.py` was touched. Grepped for `corporate_kyb_submitted` and `kyb_submit` repo-wide — the only other references are: `admin-dashboard/src/app/dashboard/corporate-accounts/kyb-queue/page.tsx` and `[id]/page.tsx` (frontend UI, calls the endpoint, unaffected by this change since the response shape/status code are unchanged), `admin-dashboard/e2e/corporate.spec.ts` (e2e test, unaffected), `admin-dashboard/src/lib/api.ts` (API client wrapper, unaffected), `repositories/corporate_repo.py` and `migrations/225_corporate_kyb_v1_columns.sql` (unrelated schema/repo helpers, not the audit-log call site), and this file's own test file.
- No other code path calls `log_admin_action` with `action="corporate_kyb_submitted"`.
- Does not touch the ride state machine, wallet deltas, or any background loop.
- Could this regress a currently-working flow? No — the response shape, status codes, and all pre-existing branches (invalid path, 409 states, storage-check failure, `set_kyb_document` failure, status-flip failure) are unchanged. Only the audit-log call's failure mode changes: previously a 5xx after already-successful work, now a 200 with the failure logged server-side instead.

## 5. User-experience effect

- Corporate admin (company-side KYB submitter) facing: previously, a rare transient audit-log DB error would show the submitter a failure page/toast even though their document was actually recorded and their company had already re-entered the verification queue — a false-negative error that could prompt a confusing retry. After this fix, they see success, matching the actual server-side state.
- Not visible mid-session in the sense of an active ride/session state change — this is a one-shot form submission (KYB document upload confirmation), not a live/streaming flow.
- No copy/notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/corporate_company_kyb.py` | Wrapped `log_admin_action(...)` in `kyb_submit` with `try/except Exception: logger.error(..., exc_info=True)` | Prevent a non-fatal audit-log write failure from turning an already-successful KYB submission into a client-visible 5xx |
| `backend/tests/test_corporate_company_kyb.py` | Added `test_submit_audit_log_failure_does_not_fail_request` | Regression test: asserts 200 + correct response body when `log_admin_action` raises |

## 7. Before / after

```python
# Before
    await log_admin_action(
        {"id": guard["user"]["id"], "role": "user"},
        action="corporate_kyb_submitted",
        resource="corporate_accounts",
        resource_id=company_id,
        details={"resubmitted_after_rejection": resubmitted},
    )
```

```python
# After
    try:
        await log_admin_action(
            {"id": guard["user"]["id"], "role": "user"},
            action="corporate_kyb_submitted",
            resource="corporate_accounts",
            resource_id=company_id,
            details={"resubmitted_after_rejection": resubmitted},
        )
    except Exception:
        logger.error("Audit log failed for corporate_kyb_submitted %s", company_id, exc_info=True)
```

## 8. Rollback plan

`git-revert-safe` — this is a pure error-handling change with no schema, flag, or data-state involved. A plain `git revert` fully restores the prior (buggy) behavior with no follow-up action needed. No feature flag was used since this only tightens an existing non-blocking-audit-log convention already applied everywhere else in the corporate module — it is a bug fix restoring consistency, not new user-visible behavior requiring dark-ship.

## 9. Verification performed

- [x] Automated tests run — unit: `pytest backend/tests/test_corporate_company_kyb.py -q` → 18 passed (17 pre-existing + 1 new regression test). Verified the new test fails without the fix (confirmed by construction: the `try/except` is the only change between the failing-5xx and passing-200 behavior; the assertion `resp.status_code == 200` would fail against the pre-fix code since `log_admin_action`'s `RuntimeError` would propagate to a FastAPI 500).
- [ ] Manual repro steps followed in staging — not done; no real Supabase/staging environment available in this session.
- [x] Blast-radius grep performed — see §4 above; searched `corporate_kyb_submitted` and `kyb_submit` repo-wide.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — matches the "Do not silently swallow errors" section's spirit: the audit-log error is still logged via `logger.error(..., exc_info=True)`, just no longer propagated as a request failure, consistent with how identical audit-log calls are already handled in `corporate_accounts.py`.
- [ ] Feature-flagged — not applicable; this restores an existing convention already unflagged elsewhere in the same module, not new behavior.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed — isolated to one handler, grepped and listed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — §5 above states the exact UX difference (false-negative error removed)

## What was NOT verified

- Not tested against a real Supabase instance — `log_admin_action` and all DB calls are mocked in the unit test, consistent with this file's existing test convention (no integration tier exists for this module).
- Did not re-run the full backend suite end-to-end for this change (scoped run against the one affected test file only, per CLAUDE.md's context-discipline guidance for a single-file isolated fix); the corporate-domain subset (`pytest -k corporate`) was exercised extensively earlier in this session for related PRs and is not expected to be affected by this isolated try/except addition.
