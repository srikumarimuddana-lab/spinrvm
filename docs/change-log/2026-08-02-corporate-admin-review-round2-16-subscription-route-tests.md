# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend (tests only) |
| Domain (Sentry tag) | corporate, payments, admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — flat SaaS subscription billing — route test slice |

## 1. Issue / gap identified

The admin route added in round2-15 (`routes/corporate_subscriptions.py`)
had no test coverage at all — access control (module gate + the
ships-dark billing flag) and the error-status mapping were unverified.

## 2. Root cause

Test-writing was its own decomposed subtask, following this round's
per-item pattern (see items #61's `TestMfaResetHttp` for the same
"route needs a dedicated HTTP-level test class" shape).

## 3. Fix / remediation

New `backend/tests/test_corporate_subscriptions_route.py`, reusing the
existing `admin_override` fixture (role=admin, modules=["corporate_accounts"])
already established for `routes/corporate_wallet.py`'s HTTP tests — same
router prefix, same `require_module` gate, so the identical fixture
applies without modification. 13 tests:

- `GET /subscription-plans`, `GET /{id}/subscription` — happy path.
- Assign: blocked when the flag is unset (default) and when explicitly
  `false`; succeeds when `true`; every `CorporateSubscriptionError` reason
  maps to its documented HTTP status; extra request-body fields are
  rejected (`extra="forbid"`).
- Cancel: succeeds **even when the billing flag is unset** — proves
  cancellation is never gated, the one deliberate asymmetry in round2-15's
  design; `at_period_end` passes through correctly in both directions;
  `no_active_subscription` maps to 404.
- Module gate: an admin without the `corporate_accounts` module grant
  gets 403 at the router-include layer, before any endpoint code runs —
  confirms the same `require_module` wiring proven for the sibling wallet
  router also holds for this new one.

## 4. Risk & impact on existing functionality

- **Blast radius: one new test file. No production code touched.**
- Confirmed patch targets follow this repo's documented convention
  (patch the name in the module that imports it —
  `routes.corporate_subscriptions.assign_subscription`, not
  `services.corporate_subscription_service.assign_subscription`) rather
  than guessing, since the route imports these by name into its own
  namespace.
- Reused, not duplicated, the `admin_override` fixture — no new fixture
  or global test-state change that could affect other test files.

## 5. User-experience effect

None — test-only change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_corporate_subscriptions_route.py` | New file: 13 HTTP-level tests | Cover access control + every branch of the round2-15 route before it's exposed to real admin traffic |

## 7. Rollback plan

`git revert` the commit. Test-only, no data or runtime behavior involved.

## 8. Verification performed

- [x] `ast.parse` syntax check — clean.
- [x] Confirmed the patch-target convention against
      `backend/tests/CLAUDE.md`'s documented rule (module-that-imports-it,
      not module-that-defines-it) before writing every `patch(...)` call.
- [x] Manually traced each `CorporateSubscriptionError` reason string
      against the route's `_ERROR_STATUS` mapping (round2-15) to confirm
      the expected status code in each test.
- [x] Did **not** run `pytest` for this file — per this round's explicit
      "don't run tests until everything is developed" instruction;
      deferred to the single end-of-round pass, which now covers all
      five slices of the subscription-billing build together.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — test-only
- [x] No behavior change to a working flow — purely additive tests

## What was NOT verified

Did not run these tests — their correctness is reasoned from the
already-proven `admin_override`/`test_client` pattern in
`test_corporate_wallet_routes.py`, not confirmed by execution. This is
the last slice of the corporate subscription-billing feature planned for
this round; the admin dashboard UI (view/assign/cancel) remains a
follow-up commit, and actual verification against a running server or
real Stripe test-mode account remains out of scope for this session
entirely.
