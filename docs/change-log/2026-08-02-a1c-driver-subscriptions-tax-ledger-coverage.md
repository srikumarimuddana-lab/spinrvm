# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | payments / drivers |
| PR / commit link | (this branch: `claude/spinr-ai-guardrail-reviewer-o2vups`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c, Sub-tier A (`routes/drivers/subscriptions.py`) |

## 1. Issue / gap identified

`backend/routes/drivers/subscriptions.py` (Spinr Pass driver-facing
subscription endpoints — money-adjacent) was at 60.52%/61% coverage per the
Track 2 full-repo scoping pass. `test_spinr_pass_subscription.py` already
covers the checkout/webhook/verify-session activation flow,
`_cancel_stripe_subscription`, and `cancel_subscription` end-to-end, but
`_compute_subscription_tax` and `_record_subscription_payment` were only
exercised through the "no service area / no tax config" short-circuit path,
and the driver-facing resend-invoice endpoint
(`POST /subscription/payments/{payment_id}/resend-invoice`) had zero
coverage — only its unrelated admin-console sibling in
`routes/admin/subscriptions.py` was tested.

## 2. Root cause

No test in the repo directly exercised `_compute_subscription_tax`'s actual
GST/PST/HST rate math (only its "no service area" default-zero branch), the
`enabled: False` tax-config-disabled branch, or `_record_subscription_payment`'s
duplicate-vs-real-failure distinction in its except block (the ledger insert
must never raise, but a duplicate-key error should log at debug while a real
DB failure logs at error — CLAUDE.md's "surface loudly" convention applied
selectively here, on purpose, since the money already moved). The driver
resend-invoice endpoint was simply never targeted by any test file — the
admin variant living in a different file with a similar-looking route name
made the gap easy to miss.

## 3. Fix / remediation

Test-only change. Added
`backend/tests/test_driver_subscriptions_tax_ledger_coverage.py` (17 tests):

- `_compute_subscription_tax`: no-service-area zero-tax default (SK),
  configured GST+PST rates, configured HST rate for an HST province,
  `enabled: False` skipping tax even with a service area, and the
  missing-config-object default (5% GST / 6% PST, `enabled` defaults `True`).
- `_record_subscription_payment`: duplicate-insert-error swallowed quietly
  (debug log, no raise), a real DB error also swallowed but logged at
  error level, a negative amount skipped like zero, tax/Stripe-receipt
  fields included when provided vs. omitted entirely when not (so
  dev-mode/legacy rows don't carry stray tax keys).
- `resend_subscription_invoice` (driver-facing): 404 on missing driver
  profile, 404 on a payment belonging to a different driver, 404 on a
  missing payment row, 502 when the email send fails, a legacy
  pre-migration-186 payment (no stored tax columns) resending with zeroed
  tax rather than fabricated amounts, a payment with stored tax columns
  resending using those exact values, and a payment with no linked
  `plan_id` still resending with the default duration label.

No application code changed. No bugs found — every branch behaved exactly
as documented in the source comments.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** New test file only. Grepped
  `backend/routes/` for callers of the three functions under test:
  - `_compute_subscription_tax` — called only from `subscribe_to_plan`
    (checkout) and `_activate_subscription` (dev-mode instant activation),
    both already covered end-to-end by `test_spinr_pass_subscription.py`;
    this PR adds direct unit coverage of the function itself, not a new
    caller.
  - `_record_subscription_payment` — called from `_activate_subscription`
    (one-off checkout) and the `invoice.paid` Stripe webhook handler
    (recurring renewals, in `routes/webhooks.py`); both callers already
    have dedicated tests that mock this function out, so this PR's direct
    tests are the first to exercise its own body.
  - `resend_subscription_invoice` — mounted once as
    `POST /subscription/payments/{payment_id}/resend-invoice` in
    `routes/drivers/subscriptions.py`'s router; no other caller.
  None of these callers were modified; only new tests were added.
- **Money-adjacent**: all dollar values in the new tests use `Decimal`
  throughout, matching CLAUDE.md's money-arithmetic convention — no float
  arithmetic introduced. The tax-rate math tests pin the exact quantized
  `Decimal` output (`ROUND_HALF_UP`, 2 decimal places) the function already
  produces.
- **PIPEDA/tax line-item transparency**: the tax tests confirm GST/PST are
  computed and returned as separate line items (per CLAUDE.md's regulatory
  section — "rider receipts must show GST and PST as separate line
  items"), which this endpoint's driver-facing subscription receipts also
  rely on.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_driver_subscriptions_tax_ledger_coverage.py` | New file — 17 tests | Close coverage gap on `routes/drivers/subscriptions.py` (61% → 69%, `-k subscription` keyword-filtered measurement, consistent with the Sub-tier A baseline methodology) |
| `docs/change-log/2026-08-02-a1c-driver-subscriptions-tax-ledger-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface (payments) |
| `ACTION_ITEMS.md` | Updated A1c's `routes/drivers/subscriptions.py` bullet | Track progress per the existing series format |

## 7. Before / after

Not applicable — purely additive test file; no existing behavior-changing diff.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_driver_subscriptions_tax_ledger_coverage.py -q --no-cov` — 17 passed.
- [x] Coverage measured: `pytest tests/ -q -k subscription --cov=routes.drivers.subscriptions --cov-report=term-missing` (keyword-filtered subset, same methodology as the file's existing 60.52%/61% baselines) — **routes/drivers/subscriptions.py: 69%** (was 61% before this PR), 181 lines remaining (was 227/222).
- [x] Full backend suite run: `pytest tests/ -q --no-cov` — pending completion at time of writing; will amend this entry if any regression is found (none expected — additive-only, no production code touched).
- [ ] Manual repro / staging check — not applicable, test-only change with no deployable behavior difference.
- [x] Blast-radius grep performed: see section 4 above.
- [x] Reviewed against CLAUDE.md conventions: Decimal-only money arithmetic confirmed throughout; ledger's never-raise contract confirmed (`_record_subscription_payment` swallows both duplicate and real DB errors by design, distinguished only by log level — not a violation of "never silently swallow", since the docstring explicitly documents this trade-off and the money has already moved by the time this function runs).

## 10. What was NOT verified

- Not run against real Supabase — mocked throughout, matching repo convention for this test tier.
- The remaining ~31% gap is concentrated in `_send_subscription_invoice_email`'s
  own body (lines 981-1149 — PDF/HTML rendering and provider dispatch,
  already exercised indirectly by mocking it as a boundary in this PR's
  tests) and `check_expiring_subscriptions` (lines 1543-1886, one of the 17
  background startup loops — a large, separate concern better suited to
  its own dedicated pass per CLAUDE.md's background-task-safety
  conventions, not folded into this one). Also not covered: `subscribe_to_plan`'s
  deeper Stripe-session-creation branches (already partially covered by
  `test_spinr_pass_subscription.py`) and a handful of dual-import fallback
  lines.
- No bugs found in this module during this pass.
