# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-03 |
| Author | Claude (backend test-coverage backlog, A1c Sub-tier C, Batch 13, single-file pick — closest file in the whole Sub-tier C list to the 80% line) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (this branch — `claude/a1c-subtier-c-batch-stripe-charge`) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1c Sub-tier C, Batch 13 |

## 1. Issue / gap identified

`backend/utils/stripe_charge.py` (227 stmts, payment-adjacent — wraps the
`stripe.PaymentIntent` lifecycle for both ride settlement and the
authorize/capture pre-auth flow) sat at 79.74% coverage measured across the
full set of test files that exercise it. The existing dedicated test file,
`tests/test_stripe_charge.py`, thoroughly covered `charge_ride()` (the
create/confirm charge path) and `cancel_authorization()`'s main branches, but
`charge_ancillary_fee()` (rider-initiated fee charges, e.g. cancellation fees)
had **zero direct test coverage** — every branch in that ~130-line function
was only reachable (if at all) incidentally through other test files —
and `authorize_ride()`, `verify_authorization()`, and `capture_ride()` (the
booking-time hold / SCA-verify / settlement-capture trio) had large gaps in
their guard-clause and non-happy-path branches (`stripe is None`,
`stripe_secret_key` missing, missing customer/payment-method, unhandled
PaymentIntent statuses, CardError/StripeError, and — for
`verify_authorization()` specifically — the two security-critical branches
that reject a mismatched-customer or too-small-amount authorization).

## 2. Root cause

No dedicated coverage pass had been made on the module's four less-central
functions since they were added; `test_stripe_charge.py`'s docstring itself
scopes the file to `charge_ride()` only. Coverage on the other four functions
came entirely as an incidental byproduct of other tests (e.g.
`test_ride_preauth.py`, `test_settle_card_capture.py`,
`test_cancellation_fee_card_charge.py`) that exercise them through their own
call sites without deliberately targeting every branch.

## 3. Fix / remediation

Test-only. Added `backend/tests/test_stripe_charge_coverage.py` (51 new
tests) covering:
- `charge_ancillary_fee()` — the entire function: `amount <= 0` no-op,
  `stripe is None` → unconfigured, missing `stripe_secret_key` →
  unconfigured, missing customer/payment-method → failed, success path
  (idempotency key format `{fee_type}-{ride_id}-{amount_cents}-{pm_id}`,
  metadata shape), a second fee_type producing a distinct idempotency key,
  `requires_action`/`requires_source_action`, `requires_payment_method`/
  `requires_confirmation` → declined, an unhandled status → failed,
  `CardError` → declined with `decline_code`, non-card `StripeError` → failed.
- `_resolve_stripe_secret()` — the `stripe is None` branch (only the
  "secret missing" branch had prior coverage), exercised indirectly via
  `authorize_ride`/`verify_authorization`/`capture_ride`/`cancel_authorization`
  with `stripe` patched to `None`.
- `authorize_ride()` — `amount <= 0` no-op, `stripe is None` → unconfigured,
  missing secret key → unconfigured, missing customer/payment-method →
  failed, `requires_capture` → authorized (asserting `capture_method=manual`,
  `off_session=False`, idempotency key), `requires_action`/
  `requires_source_action`, `requires_payment_method`/`requires_confirmation`
  → declined, an unhandled status → failed, `CardError` → declined,
  `StripeError` → failed.
- `verify_authorization()` — missing `payment_intent_id` → failed,
  `stripe is None`/missing secret → unconfigured, `StripeError` on retrieve →
  failed, **customer mismatch → declined** (the "don't let a rider attach
  someone else's hold" security check), **amount-too-small → declined** (the
  "don't let a rider replay a smaller hold from a cheaper booking" security
  check), `requires_capture` → authorized, the four
  requires_action/requires_payment_method/requires_confirmation/requires_source_action
  variants → declined, `succeeded` → authorized (idempotent-attach case),
  an unexpected status → failed.
- `capture_ride()` — `amount <= 0` no-op, missing `payment_intent_id` →
  failed, `stripe is None`/missing secret → unconfigured, success (asserting
  `amount_to_capture` cents + idempotency key), `CardError` → declined,
  `StripeError` → failed, an unhandled status → failed.
- `cancel_authorization()` — the `stripe is None or secret is None`
  short-circuit branch (previously only the no-`payment_intent_id` and
  Stripe-raises branches had coverage); split into a "secret missing but
  stripe installed" case (asserts `PaymentIntent.cancel` is never called)
  and a "stripe not installed" case.
- `charge_ride()` — its own `stripe is None` early-return specifically (this
  function doesn't route through `_resolve_stripe_secret` like the other
  four do, so it needed its own direct test).

No application code changed. **No bugs found** — every branch in
`stripe_charge.py` behaved exactly as its own docstrings and inline comments
describe, including the two security checks in `verify_authorization()` and
every idempotency-key namespace (`ride-charge-`, `ride-confirm-`,
`{fee_type}-`, `ride-auth-`, `ride-capture-`, `ride-cancelauth-`).

## 4. Risk & impact on existing functionality

- Test-only change — no production code paths were modified. Blast radius:
  isolated to `backend/tests/test_stripe_charge_coverage.py`, a new file;
  `backend/tests/test_stripe_charge.py` (the existing file) was not edited.
- Grepped every other consumer of `stripe_charge.py`'s public functions to
  confirm nothing else needed touching:
  - `charge_ride` / `charge_ancillary_fee`: `backend/routes/rides.py`
    (`process_payment`), `backend/utils/payment_retry.py` (async retry
    loop), `backend/routes/rides/cancellation.py`-equivalent cancellation-fee
    flow (via `charge_ancillary_fee`).
  - `authorize_ride` / `verify_authorization` / `capture_ride` /
    `cancel_authorization`: the booking-time pre-auth flow (ride creation)
    and settlement capture path, plus the "Change Card" escape.
  - None of these call sites were modified — only their existing behavior
    (already asserted by `test_ride_preauth.py`, `test_settle_card_capture.py`,
    `test_cancellation_fee_card_charge.py`, etc.) is now also independently
    verified at the `stripe_charge.py` unit level, with the Stripe SDK
    mocked (`unittest.mock.patch` on the module's `stripe` binding) exactly
    per the existing test file's pattern — no real Stripe network calls in
    either the old or new file.
  - `ChargeOutcome` (the shared dataclass) was read, not modified.
- Every money value constructed in the new tests is a `Decimal` — never a
  `float` — per CLAUDE.md's money-arithmetic convention (e.g.
  `Decimal("15.00")`, `Decimal("40.00")`), and idempotency-key cent
  conversions are asserted against the module's own `dollars_to_cents`
  output, not re-derived independently.

## 5. User-experience effect

None. Backend-only, test-only change; no rider/driver/corporate-admin/
internal-admin facing behavior changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_stripe_charge_coverage.py` | New file, 51 tests | Close coverage gap on `charge_ancillary_fee`, `_resolve_stripe_secret`, `authorize_ride`, `verify_authorization`, `capture_ride`, plus remaining `cancel_authorization`/`charge_ride` guard branches |
| `ACTION_ITEMS.md` | Batch entry added/marked closed with before/after coverage numbers | Backlog tracking |

## 7. Before / after

Pure additive test code — no behavior-changing diff to skip/show here.

## 8. Rollback plan

Revert the commit (test-only; no data, migration, or flag involved — nothing
in this change touches a live code path, a DB table, or `app_settings`).

## 9. Verification performed

- [x] Automated tests run:
  - New file standalone: `pytest tests/test_stripe_charge_coverage.py -o addopts="" -q` — **51 passed**.
  - New file + existing dedicated file: `pytest tests/test_stripe_charge.py tests/test_stripe_charge_coverage.py --cov=utils.stripe_charge --cov-report=term-missing -o addopts="" -q` — **70 passed**, coverage **99%** (1 line missing: the dual-import `ImportError` fallback at line 64, structurally near-impossible to reach once the module is cached in `sys.modules` — same documented pattern as prior Sub-tier B/C files in this backlog).
  - Wider payment-adjacent sweep (every test file that references `stripe_charge` anywhere in the repo, per `grep -rl stripe_charge tests/`): `pytest tests/test_admin_rides_read_endpoints_coverage.py tests/test_cancellation_fee_card_charge.py tests/test_coverage_rides.py tests/test_dispute_refund_cents.py tests/test_disputes_admin_coverage.py tests/test_e2e_payment_guard.py tests/test_money_decimal.py tests/test_orphan_refund.py tests/test_p0_ship_blockers.py tests/test_payment_unconfigured_guard.py tests/test_process_payment_card.py tests/test_reconciliation.py tests/test_ride_preauth.py tests/test_ride_preauth_booking.py tests/test_settle_card_capture.py tests/test_stripe_card_payment.py tests/test_stripe_charge.py tests/test_stripe_charge_coverage.py --cov=utils.stripe_charge --cov-report=term-missing -o addopts="" -q` — **484 passed, 0 collisions**, coverage **99%** (same single line 64 missing).
- [ ] Full repo-wide test suite — **deferred** to a later consolidated run across all in-flight A1c Sub-tier C batches, per this task's explicit instruction to conserve tokens. Only the standalone new-file run and the two targeted combinations above were executed.
- [x] Blast-radius grep performed: `grep -rl "stripe_charge" backend/tests/` to enumerate every test file already exercising this module (17 files); `grep` for every production import of `stripe_charge`'s public functions across `backend/routes/` and `backend/utils/` to confirm the caller list in §4 is complete.
- [x] Reviewed against relevant CLAUDE.md conventions: money arithmetic (all new test fixtures use `Decimal`, verified no float literals introduced for money values), Stripe idempotency (every idempotency-key format asserted matches the module's documented scheme), "never hit real Stripe" (all Stripe calls mocked via `unittest.mock.patch` on the module's `stripe` binding, same pattern as the existing test file — confirmed no `stripe.api_key`/live-network calls anywhere in the new file).
- [ ] Feature-flagged — not applicable, test-only.

**What was NOT verified:**
- The full backend test suite was not run for this batch (deferred, see
  above) — only the standalone new-file run and the two targeted
  combinations (2-file and 18-file) shown above.
- No real Stripe API calls were exercised anywhere in this batch — every
  Stripe interaction in both the existing and new test files is mocked via
  `unittest.mock.patch`/`MagicMock`/`AsyncMock`. This gives no signal on
  actual Stripe API contract drift (e.g. a future `stripe-python` version
  renaming a status string or restructuring `CardError.error`) — that class
  of risk is out of scope for a coverage-only pass and would need a
  contract/integration test against Stripe's test-mode API to catch.
- `ruff check` / `ruff format --check` were not run on the new file in this
  session (a project-wide formatter hook did run automatically on save per
  the harness's `PostToolUse:Write` hook, but no explicit lint pass was
  invoked).
- CI/lint gates were not run, per this task's explicit "do NOT run any CI/lint
  gates" instruction — deferred to the later consolidated pass.

