# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend (tests only) |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — invoicing — route test slice |

## 1. Issue / gap identified

The two routes added in round2-23 (company-portal and internal-admin PDF
downloads) had no test coverage — access control for both audiences, and
the product decision's explicit requirement that both audiences see
identical documents, were unverified.

## 2. Root cause

Test-writing was its own decomposed subtask, following this round's
per-item pattern.

## 3. Fix / remediation

New `backend/tests/test_corporate_statement_pdf_routes.py`, 9 tests:

- Company-portal: happy path (status 200, PDF bytes, correct
  content-type/content-disposition, audit called), unknown company 404,
  non-admin member rejected (403), cross-company admin rejected (403) —
  reusing the exact `rider_override`/`_as_admin()` fixtures already
  proven in `test_corporate_company_gap_coverage.py`.
- Internal admin: happy path, unknown company 404, missing
  `corporate_accounts` module rejected (403) — reusing the
  `admin_override`-style fixture pattern from
  `test_corporate_wallet_routes.py`.
- **`test_both_endpoints_call_the_same_shared_aggregation`** — the
  highest-value test here: patches `routes.corporate_company.build_full_month_statement`
  (the single shared function both routes call, per round2-23's design)
  with a capturing side effect, hits both endpoints, and asserts the
  exact same `(company_id, month)` call was made from both — a direct
  check on the product decision's "byte-identical documents" claim,
  which round2-23 could only state by code inspection.
- Confirmed the patch target for the internal-admin route's lazily-
  imported `build_full_month_statement` is the **defining** module
  (`routes.corporate_company.build_full_month_statement`), not the
  importing one — matching this codebase's established convention for
  testing lazy cross-route-file imports (`routes.drivers.subscriptions.*`
  in `test_webhooks_main.py`), verified by reading that precedent before
  writing the patch.

## 4. Risk & impact on existing functionality

- **Blast radius: one new test file. No production code touched.**
- Reused, not duplicated, three existing fixture patterns
  (`rider_override`, `_as_admin`, and the `admin_override`-style module
  grant) — no new global test state.

## 5. User-experience effect

None — test-only change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_corporate_statement_pdf_routes.py` | New file: 9 tests | Cover access control for both audiences + the shared-aggregation design claim from round2-23 |

## 7. Rollback plan

`git revert` the commit. Test-only, no data or runtime behavior involved.

## 8. Verification performed

- [x] `ast.parse` syntax check — clean.
- [x] Confirmed the lazy-import patch-target convention against a real
      precedent in this codebase (`test_webhooks_main.py`) before writing
      it, rather than guessing.
- [x] Manually traced each access-control branch against the round2-23
      route bodies (module gate, `require_company_admin`'s own-company
      scoping, unknown-company 404s in both routes).
- [x] Did **not** run `pytest` for this file — per this round's explicit
      "don't run tests until everything is developed" instruction;
      deferred to the single end-of-round pass, which now covers all
      seven slices of the invoicing feature build together.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — test-only
- [x] No behavior change to a working flow — purely additive tests

## What was NOT verified

Did not run these tests — their correctness (especially the lazy-import
patch target, the one genuinely non-obvious piece of this test file) is
reasoned from an existing precedent in this codebase, not confirmed by
execution. The company-portal "Download PDF" UI button remains the final
follow-up commit (round2-25); the internal-admin route still has no
dashboard UI planned this round (stated explicitly in round2-23).
