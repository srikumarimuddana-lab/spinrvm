# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend (tests only) |
| Domain (Sentry tag) | corporate, payments |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — self-serve wallet funding — test slice |

## 1. Issue / gap identified

The endpoint added in round2-18 (`POST /company/{company_id}/wallet/topup`)
had no test coverage — access control (own-company-only, admin-role-only),
the payment-method fallback logic, and every precondition-rejection branch
were unverified.

## 2. Root cause

Test-writing was its own decomposed subtask, following this round's
per-item pattern.

## 3. Fix / remediation

New `backend/tests/test_corporate_self_serve_topup.py`, reusing the exact
`rider_override` + `_as_admin()`/`_as_member()` +
`dependencies.company_guard.list_active_memberships_for_user` patching
convention already established in `test_corporate_company_gap_coverage.py`
(not a new pattern). 13 tests:

- Happy path with a client-supplied `payment_method_id` and with the
  `get_default_payment_method` fallback — confirms the fallback is
  skipped entirely when a payment method is supplied
  (`m_default_pm.assert_not_awaited()`).
- Confirms the created PaymentIntent's `metadata` dict matches exactly
  what the existing webhook handler expects (`scope`, `company_id`,
  `wallet_id`), with `initiated_by` set to the **company admin's own**
  user id — the specific claim round2-18's Change Impact Log made about
  zero webhook changes being needed.
- No payment method anywhere (neither supplied nor default) → 422.
- Company inactive / no Stripe customer / unknown company / wallet not
  provisioned → 409/409/404/500 respectively.
- **Access control, the highest-value coverage here**: a plain `member`
  role is rejected (403); an admin of a *different* company cannot top up
  `c1`'s wallet (403) — proves `require_company_admin` scopes strictly to
  the path's `company_id`, not just "any admin of any company."
- Amount below/above the $100–$10,000 bounds and an extra request-body
  field are all rejected with 422 (Pydantic `extra="forbid"`).

## 4. Risk & impact on existing functionality

- **Blast radius: one new test file. No production code touched.**
- Reused, not duplicated, the `rider_override`/`_as_admin`/`_as_member`
  fixtures already proven in `test_corporate_company_gap_coverage.py` —
  no new global test fixture or state.
- Confirmed patch targets follow this repo's convention (patch the name
  in the module that imports it: `routes.corporate_company.stripe`,
  `routes.corporate_company.get_default_payment_method`, etc.) matching
  every other test in that sibling file.

## 5. User-experience effect

None — test-only change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_corporate_self_serve_topup.py` | New file: 13 tests | Cover access control + every branch of the round2-18 endpoint before real company-admin traffic reaches it |

## 7. Rollback plan

`git revert` the commit. Test-only, no data or runtime behavior involved.

## 8. Verification performed

- [x] `ast.parse` syntax check — clean.
- [x] Manually traced each precondition branch in the round2-18 endpoint
      against its corresponding test.
- [x] Confirmed the cross-company access-control test's premise by
      re-reading `dependencies/company_guard.py::require_company_admin`
      (from this round's earlier research) — it filters memberships by
      `m.get("company_id") == company_id` from the URL path, so an admin
      membership at a different company must 403, not silently succeed.
- [x] Did **not** run `pytest` for this file — per this round's explicit
      "don't run tests until everything is developed" instruction;
      deferred to the single end-of-round pass.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — test-only
- [x] No behavior change to a working flow — purely additive tests

## What was NOT verified

Did not run these tests — their correctness is reasoned from the
already-proven `rider_override`/`_as_admin` pattern in the sibling gap-
coverage file, not confirmed by execution. The company-portal UI to
actually drive this endpoint remains a follow-up commit (round2-20);
verification against a live server or real Stripe test-mode account
remains out of scope for this session entirely.
