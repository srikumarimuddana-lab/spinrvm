# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (this branch: `claude/spinr-ai-guardrail-reviewer-o2vups`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c, Sub-tier C (`utils/payment_retry.py`) |

## 1. Issue / gap identified

`backend/utils/payment_retry.py` (the background loop that retries failed/
stuck Stripe payments and driver payouts every 5 minutes) was at 72.54%
coverage per the Track 2 full-repo scoping snapshot. Picked ahead of
Sub-tier C's raw ranking — same reasoning as the earlier `reconciliation.py`
pick — because a silent bug here means a rider's failed payment or a
driver's stuck payout quietly never retries and never surfaces, not just an
untested line.

## 2. Root cause

`tests/test_payment_retry.py` already covered the core double-charge guard
(atomic claim race, invoice-skip, `requires_capture` happy/edge paths, the
unexpected-intent-state release branch) in detail, but had no coverage at
all for: the Meta Purchase-conversion side hook (`_fire_purchase_conversion`),
the invoice-claim staleness helper (`_invoice_claim_is_stale`), the
admin-alert and payout-notify error-swallow branches, the 24h-age and
30-minute-processing-window skip branches of the main scan, the
`admin_alerted_payment_exhausted` claim race, the guest-corporate
settlement sweep (`sweep_guest_corporate_settlements`), and the
`payment_retry_loop` background loop itself (lock contention, per-substep
exception isolation, heartbeat, jitter).

## 3. Fix / remediation

Test-only change. Added `backend/tests/test_payment_retry_coverage.py` (40
tests) as a new file alongside the existing `test_payment_retry.py` (same
pattern as `test_redis_client_coverage.py` sitting alongside pre-existing
Redis tests), covering:

- `_fire_purchase_conversion`: the `hasattr(db, "get_ride")`-vs-`get_rows`
  fallback, ride-not-found logging, and the outer `except Exception` never
  propagating (this runs inside a background loop that must keep sweeping).
- `_invoice_claim_is_stale`: legacy sentinel (no timestamp) → conservatively
  `False`, fresh vs. stale timestamped sentinels, and the malformed-timestamp
  `except (ValueError, OverflowError, OSError)` branch.
- `_alert_admins_payment_exhausted`: WS broadcast failure, admin-lookup
  failure, and one admin's push failure not stopping the loop over the rest.
- `update_payout_status`, `notify_driver_payout_failed` (happy path + push
  failure swallow).
- `retry_stuck_payouts`: fetch failure, below-max-retries no-op, claim-race
  lost skips the driver notify, claim-won marks failed and notifies.
- `retry_failed_payments`'s scan-level branches: fetch failure,
  exhausted-and-not-yet-alerted claim + alert, exhausted claim-race-lost,
  exhausted-and-already-alerted no-op, the 24h-age skip, the
  processing-status 30-minute window (both sides), missing
  `payment_intent_id`/`stripe_secret_key` skips, the `requires_capture`
  `total_fare` fallback when `grand_total` is `None`, the unexpected-state
  release-and-alert-on-exhaustion path, the Stripe-exception path (both
  below and at `MAX_RETRIES`, including the rider push-failure swallow), and
  the stale-pending-invoice-sentinel log-and-skip branch.
- `sweep_guest_corporate_settlements`: query failure, multi-row settle,
  one-row-failing-doesn't-stop-the-sweep, empty-result no-op.
- `payment_retry_loop`: lock-not-acquired skips work and re-enters the loop
  (`continue`) for a second tick, lock-acquired runs all three substeps with
  each substep's exception isolated from the others (none crash the loop),
  and the happy-path heartbeat + `±10%` jitter sleep bounds.
- `_pod_id`'s hostname:pid shape.

No application code changed, **no bugs found** — every exception branch
exercised behaves as documented (loud `logger.error` with `exc_info=True`
per CLAUDE.md's error-handling convention, no silent swallow of a
money-moving error; the two intentionally-quiet `logger.debug` swallows —
admin/rider push-notification failures — are documented as best-effort
side channels, not the source of truth for retry state).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** New test file only; zero production code
  touched. `utils/payment_retry.py` is called from exactly one place —
  `core/lifespan.py`'s `payment_retry_loop` background-task spawn (one of
  the 17 startup loops) — and from nowhere else; no other module imports
  its functions directly.
- **Money-adjacent but not money-moving in this diff**: the module calls
  `stripe.PaymentIntent.confirm`/`.capture` and
  `services.payment_service.record_payment_event` — every new test mocks
  these at the same seam the pre-existing test file already uses
  (`stripe.PaymentIntent.*`, `services.payment_service.record_payment_event`,
  `services.payment_service.auto_settle_guest_corporate`), so no test
  exercises a real Stripe or DB write.
- **Replay-safety contract unchanged**: the new tests assert the existing
  atomic-claim behavior (a `None` return from `db.update_one` on a claim
  race causes a skip, never a duplicate action) for both the payment-retry
  claim and the payout-status claim — confirming, not changing, the
  documented replay-safety guarantee.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_payment_retry_coverage.py` | New file — 40 tests | Close coverage gap on `utils/payment_retry.py` (72.54% → 99% combined with the existing `test_payment_retry.py`) |
| `docs/change-log/2026-08-02-a1c-payment-retry-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface (payments) |
| `ACTION_ITEMS.md` | Sub-tier C section | Track progress per the existing series format |

## 7. Before / after

Not applicable — purely additive test file; no existing behavior-changing diff.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_payment_retry_coverage.py -q --no-cov` — 40 passed.
- [x] Coverage measured: `pytest tests/test_payment_retry.py tests/test_payment_retry_coverage.py tests/test_cancellation_fee_card_charge.py tests/test_e4_d10_payment_3ds_quests.py tests/test_guest_auto_settle.py tests/test_replay_safety_payment_loops.py tests/test_stripe_charge.py --cov=utils.payment_retry --cov-report=term-missing` — **utils/payment_retry.py: 99%** (244 stmts, 2 missing — the `try/except ImportError` fallback for `utils.loop_monitor.record_heartbeat`, structurally unreachable in this test harness since both import spellings resolve to the same already-imported module, same documented pattern as prior Sub-tier B files). 106 passed, 0 failed, 0 collisions with the pre-existing payment test files run alongside it.
- [x] Full backend suite run: `pytest tests/ -q` — `8415 passed, 8 skipped, 1 xfailed, 0 failed` (was 8374 before this file existed), total coverage 86.46%. No regressions.
- [ ] Manual repro / staging check — not applicable, test-only change with no deployable behavior difference.
- [x] Blast-radius grep performed: `grep -rn "payment_retry" backend --include=*.py | grep -v tests/` confirms the only caller of this module's loop entrypoint is `core/lifespan.py`.
- [x] Reviewed against CLAUDE.md conventions: confirmed the "Do not silently swallow errors" convention holds for every DB/Stripe-facing branch (`logger.error(..., exc_info=True)`, never a bare `warning`-and-continue on a payment path).

## 10. What was NOT verified

- Not run against a real Stripe test-mode account or real Supabase — every
  Stripe/DB call is mocked at the same seam the pre-existing test file
  already uses, matching repo convention for this test tier.
- `payment_retry_loop`'s Redis-lock contention is tested via a mocked
  `redis_set_nx` return value, not against a real Redis instance — the
  in-process-fallback-vs-real-Redis distinction for `redis_set_nx` itself is
  already covered by `test_redis_client_coverage.py`, not re-tested here.
