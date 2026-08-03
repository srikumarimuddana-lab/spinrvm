# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend (tests only) |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "the MFA-reset endpoint's access control has never been exercised through a real HTTP call" |

## 1. Issue / gap identified

`POST /api/admin/staff/{staff_id}/mfa-reset` (super-admin-only MFA
lost-phone recovery) had test coverage only at the function-call level
(`test_admin_staff_mfa_reset.py` calling `reset_staff_mfa(...)` and
`require_role("super_admin")` directly). No test exercised the endpoint
through the app's actual routing + dependency-injection chain, so a
route-registration mistake, a missing/misplaced `require_module`/
`require_role` decorator, or a broken `Depends()` chain would not have
failed any existing test.

## 2. Root cause

The endpoint is double-gated — `require_module("staff")` at
`admin_router.include_router(staff_router, ...)` time, then
`require_role("super_admin")` inside the endpoint itself — both chained
through `Depends(get_admin_user)`. Unit tests that call the route
function directly bypass both gates entirely and can't catch a wiring
regression in either one.

## 3. Fix / remediation

Added a `TestMfaResetHttp` class to the existing
`test_admin_staff_mfa_reset.py`, using the same `TestClient` +
`app.dependency_overrides[get_admin_user]` pattern already established
in `test_admin_security.py::TestStaffRBAC`. Four new HTTP-level cases:

- super_admin (with `staff` module) resets another staff member's MFA
  over real HTTP → 200 `{"success": true}`
- non-super_admin role, but *with* the `staff` module (isolates the
  endpoint's own `require_role` gate from the router's module gate) →
  403, detail mentions `super_admin`
- admin missing the `staff` module entirely → 403 (caught by the
  router-level `require_module` gate, before the endpoint runs)
- super_admin attempting to reset their own MFA over HTTP → 400

This is additive test coverage only — no application code changed.

## 4. Risk & impact on existing functionality

- **Blast radius: one test file, new test class only.** No production
  code touched; the existing five function-level tests in this file are
  unchanged.
- Grepped for other consumers of `reset_staff_mfa`/the mfa-reset route:
  none — it's only reachable via the admin dashboard's staff screen.
- Each new test pops its `dependency_overrides` entry in a `finally`
  block so it can't leak into other tests sharing the same `app`
  singleton (`test_admin_security.py` uses the identical cleanup
  pattern).

## 5. User-experience effect

None — test-only change, no user-facing behavior touched.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_admin_staff_mfa_reset.py` | Added `TestMfaResetHttp` (4 new HTTP-level tests) + `TestClient`/`_override_admin` imports/helper | Close the "never exercised through a real HTTP call" coverage gap |

## 7. Rollback plan

`git revert` the commit. Test-only, no data or runtime behavior
involved.

## 8. Verification performed

- [x] `ast.parse` syntax check on the modified file — clean.
- [x] Confirmed the endpoint's actual gating chain by reading
      `routes/admin/__init__.py` (`require_module("staff")` at
      include-time) and `routes/admin/staff.py` (`require_role` at the
      endpoint) before writing the tests, rather than guessing.
- [x] Confirmed `require_module`/`require_role` both resolve through
      `Depends(get_admin_user)` (`dependencies/__init__.py`), so
      overriding only `get_admin_user` is sufficient to drive both
      gates in tests — matches the proven `TestStaffRBAC` pattern.
- [x] Did **not** run `pytest` for this test file — per this round's
      explicit "don't run tests until everything is developed"
      instruction; deferred to the single end-of-round pass.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed
- [x] No behavior change to a working flow — purely additive tests

## What was NOT verified

Did not actually run these tests (per this round's instruction) — their
correctness (assertions, status codes, dependency-override wiring) is
reasoned from the existing, already-passing `TestStaffRBAC` pattern in
`test_admin_security.py`, not confirmed by execution. Will be verified
in the single end-of-round full test-suite pass. Did not add a test for
the 404 (unknown staff) or 400 (MFA-not-enabled) branches at the HTTP
level — those are already exercised at the function level in this same
file and add little beyond what the four new HTTP cases already prove
about the routing/dependency wiring.
