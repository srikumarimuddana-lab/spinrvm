# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (session), reviewed with @vikas |
| Surface(s) | backend (tests only) |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/corporate-accounts-coverage` |
| Related issue or gap ID | `ACTION_ITEMS.md` A1b Track 1, item 1 — corporate coverage debt, `routes/corporate_accounts.py` |

## 1. Issue / gap identified

`routes/corporate_accounts.py` — the file every corporate lifecycle-audit fix (gaps #1-3, Findings 1/4/6/7 across PRs #2615/#2696) is wired into — sat below the newly-proposed 80% corporate coverage target (`CLAUDE.md`, added in PR #2713). Several small, independently-testable branches (validator no-ops, a query filter, an error fallback, an entire untested endpoint) had never been exercised by any test.

## 2. Root cause

Not a bug — a coverage gap. Several branches were simply never hit by existing tests: the `business_number`/`tax_region` field-validator early-returns on empty string (both create and update schemas), the `is_active` list-filter branch, the `X-Total-Count` header's exception fallback, and the entire `kyb_upload_url` endpoint (zero tests existed for it) plus two `kyb_document_confirm` error branches (account-not-found, audit-log-failure-is-best-effort).

## 3. Fix / remediation

Test-only change — no application code modified. Added 9 tests to `backend/tests/test_corporate_admin_routes.py`:

- `test_create_accepts_blank_business_number_and_tax_region` / `test_update_accepts_blank_business_number_and_tax_region` — empty-string validator no-ops on both create and update schemas
- `test_list_filters_by_is_active` — the `is_active` query-param filter branch
- `test_list_total_count_failure_does_not_break_response` — `count_documents` raising must not fail the list response, only skip the `X-Total-Count` header
- `test_kyb_upload_url_returns_signed_url` / `test_kyb_upload_url_rejects_unsupported_content_type` — the previously entirely-untested `kyb_upload_url` endpoint, happy path + content-type validation
- `test_kyb_document_confirm_account_not_found` — 404 when `set_kyb_document` finds no matching row
- `test_kyb_document_confirm_audit_log_failure_does_not_fail_request` — audit-log write is best-effort, same pattern as every other audit call in this codebase

`routes/corporate_accounts.py` coverage: **77% → 82%** (measured against the corporate-admin-route test set: `test_admin_business_logic.py`, `test_admin_rbac.py`, `test_corporate_admin_routes.py`, `test_corporate_b2b_schema.py`, `test_corporate_db_helpers.py`, `test_corporate_e2e_foundation.py`, `test_corporate_e2e_wallet.py`, `test_corporate_kyb.py`, `test_corporate_status.py`, `test_corporate_stripe_customer.py`, `test_corporate_wallet_bootstrap.py`, `test_corporate_wallet_freeze.py`, `test_db.py`, `test_deprecated_route_admin_exempt.py`, `test_error_response_sanitisation.py`, `test_features.py`, `test_p3_admin_jwt_modules.py`, `test_stripe_event_loop_offload.py`). Note: the ~39% figure quoted in earlier sessions' change-logs (PR #2696, #2713) was measured against a narrower corporate-only test subset and undercounted coverage already provided by these admin/KYB/wallet-bootstrap test files — 77% was the real starting baseline, not 39%. `ACTION_ITEMS.md`'s A1b entry corrected accordingly.

Remaining uncovered lines (60 statements, 18%) are concentrated in `change_company_status`'s deeper error/edge branches (lines 659-689, 748-749, 770-781, 797-798 — Stripe/wallet-winddown failure paths already exercised in `test_corporate_status.py`'s happy/primary paths but not every nested exception branch) and `kyb_review`'s email-failure branches (403-404, 429, 443-451) — lower priority, left for a future pass.

## 4. Risk & impact on existing functionality

- **Blast radius: zero application code changed.** This PR touches only `backend/tests/test_corporate_admin_routes.py` (new tests) and `ACTION_ITEMS.md`/`docs/change-log/` (documentation). No production code path is altered.
- No new mocks patch anything not already patched elsewhere in this file — same `db_supabase.*`/`routes.corporate_accounts.*` patch targets used throughout.
- No test removed or weakened — all 25 tests in the file (16 existing + 9 new) pass; full corporate test suite (392 tests across all `test_corporate_*.py` + `test_*corporate*.py` files) passes; full backend suite re-run with 0 regressions (see §9).

## 5. User-experience effect

None — test-only change, no behavior anywhere is different for any user.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_corporate_admin_routes.py` | +9 tests covering validator no-ops, list filter, count-failure fallback, kyb-upload-url (previously untested), kyb-document-confirm error branches | Coverage debt closure — Corporate module lifecycle audit backlog |
| `ACTION_ITEMS.md` | A1b Track 1 item 1 updated: `routes/corporate_accounts.py` marked done at 82%, corrected the earlier 39% baseline measurement error, remaining files in the priority list unchanged | Keep the backlog entry accurate as work closes it incrementally |

## 7. Before / after

Not applicable — no behavior-changing diff, additive test-only change.

## 8. Rollback plan

`git revert` — test-only change, nothing to roll back operationally.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_corporate_admin_routes.py -q` — 25 passed.
- [x] Broader regression: `pytest -k corporate -q` — 392 passed, 3 skipped, 0 failed.
- [x] Full backend suite: `pytest -q` — see run output in PR; 0 regressions vs. the pre-existing baseline.
- [x] `ruff check` and `ruff format --check` clean on the changed test file.
- [x] Coverage re-measured: `routes/corporate_accounts.py` 77% → 82%, confirmed via `--cov-report=term-missing` against the full corporate-admin-route test set.

## 10. Sign-off

- [x] Rollback plan is concrete (plain revert, test-only)
- [x] Blast radius is stated, not assumed (§4 — zero application code changed)
- [x] No silent behavior change — nothing behavior-affecting in this PR

## What was NOT verified

- No real Supabase call was exercised — same as every other test in this file, `AsyncMock`/`patch` throughout.
- Remaining 18% of `corporate_accounts.py` (mostly `change_company_status`'s deeper nested exception branches and `kyb_review`'s email-failure paths) is still uncovered — this PR closes the highest-value, most independently-testable gaps first, not the entire file. Follow-up tracked in `ACTION_ITEMS.md` A1b if further closure is wanted.
