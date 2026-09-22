# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | Codex |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | existing PR #5716 (payment review follow-up) |
| Related issue or gap ID | Review findings for PR #5716 |

## 1. Issue / gap identified

A succeeded PaymentIntent below the ride obligation could leave its webhook stuck returning 503 when the split-component manifest was missing or invalid, even though that amount mismatch is permanent. Separately, payment retry could mark a succeeded primary PI paid without proving the full fare and tip, capture an underfunded hold, or reset a paid ride after losing its claim; stale `processing` rides also lacked safe split-settlement recovery.

## 2. Root cause

The webhook treated every pending/failed/processing status as evidence that a split settlement was still being finalized, even when no exact ledger component proof existed. The retry loop treated `PaymentIntent.status == succeeded` as proof of full settlement and wrote `paid` directly; its `requires_capture` path trusted mutable ride fields and could capture less than the owed total. The existing `stripe_reconcile` healer checked only a primary PI against the current ride tip and did not require the durable aggregate ledger row or split PI evidence.

## 3. Fix / remediation

The webhook now terminally records permanent underpayment events when exact component proof is absent or invalid, without changing the ride's unpaid state. Matching booking-hold/completion in-flight events still unclaim and return 503; a valid component manifest on an unsettled ride also remains retryable until finalization/recovery.

The retry loop no longer marks a ride paid from a succeeded primary PI alone. It returns the ride to `processing` with a claim-scoped compare-and-swap, preserves the stale timestamp, and can invoke the existing auto-heal only after the 30-minute processing threshold. The auto-heal remains gated by `stripe_auto_heal_processing` (default off) and now requires a ride- and rider-owned `financial_events` aggregate row, a frozen-tip obligation matching its cents, and exact successful Stripe amounts for every PI in a split manifest. The repair writes `paid_at`, applies the frozen tip, and routes the rider receipt through the existing outbox-aware receipt helper. Missing or inconsistent proof stays unpaid and visible for manual review. Retry hold capture also refuses stale `processing` rows and refuses partial captures without a proven overflow settlement.

Chosen approach: reuse the existing durable ledger and flag-gated repair path. A new settlement protocol and schema field could persist an obligation before capture, but would widen this review into a migration and create a second recovery contract. Without ledger proof, manual review is safer than guessing from a stale ride tip or charging again.

## 4. Risk & impact on existing functionality

Blast radius: backend payments, single surface. The touched state is `rides.payment_status`; reads/writes include `backend/routes/rides/payments.py` (in-app settlement claim), `backend/routes/webhooks.py` (Stripe success/failure), `backend/utils/payment_retry.py` (5-minute retry loop), and `backend/utils/stripe_reconcile.py` (24-hour detector and flag-gated repair). The registry `docs/known-forks.md` contains no fork entry for these Python payment paths.

A rider with a permanently partial payment now receives a terminal Stripe-event outcome while the ride remains unpaid, instead of causing repeated Stripe deliveries. A valid aggregate split proof still waits for the ride finalizer; a lost finalizer without a durable ledger row remains unpaid for manual review. This trades automatic recovery for protection against false paid flips and duplicate charges when the frozen obligation cannot be proven.

The retry loop runs every five minutes and only considers `processing` rows after 30 minutes. With `stripe_auto_heal_processing` enabled, the exact-proof repair can happen on the next retry tick after that threshold (up to about five additional minutes). The separate Stripe reconciler runs daily; with the flag off, it detects but does not heal, and unresolved cases require manual review. The setting remains default off because the paid flip moves money-state and sends a receipt.

No wallet delta or new Stripe mutation is introduced by recovery. A failed hold with authorization below the current obligation is not partially captured by this retry path; it is exhausted and alerted for manual action. Receipt email is sent only after the repair CAS wins and uses the existing outbox-aware helper.

## 5. User-experience effect

Rider-visible behavior changes only when a payment is permanently underpaid or recovery lacks proof: the ride remains unpaid and can require support/manual review instead of remaining silently in a Stripe 503 retry loop or appearing paid after a partial capture. No customer-facing copy was changed. The change is not visible mid-ride; it applies to completed rides or payment settlement recovery. Drivers may wait for verified payment recovery before status is finalized.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/webhooks.py` | Permanent invalid/missing split proof is terminally acknowledged; valid proof and actual in-flight cases retain retry behavior. | Stop endless retry for amounts that cannot satisfy the ride obligation. |
| `backend/tests/test_webhooks_main.py` | Added invalid/missing component proof and pending/failed/processing regression cases. | Pin terminal refusal and retryable valid/in-flight paths. |
| `backend/utils/payment_retry.py` | Added CAS-protected status releases, no succeeded-primary paid shortcut, stale-processing capture guard, and refusal of underfunded hold capture. | Prevent downgrading paid rides and prevent partial capture from settling the ride. |
| `backend/tests/test_payment_retry.py` | Covers proofless succeeded PI, stale age preservation, CAS race, stale-tip hold, and underfunded hold refusal. | Verify no second charge or premature paid transition. |
| `backend/utils/stripe_reconcile.py` | Requires exact owner-bound aggregate ledger and Stripe component proof; heals with frozen tip, `paid_at`, and receipt routing. | Make the existing gated recovery safe for full and split obligations. |
| `backend/tests/test_stripe_reconcile.py` | Covers missing/misowned proof, stale ride tip, exact amount and split-component verification, paid timestamp, and receipt handoff. | Verify recovery only follows durable full-obligation evidence. |

## 7. Before / after

```python
# Before: succeeded primary PI was treated as full settlement
if intent.status == "succeeded":
    await db.update_one("rides", {"id": ride_id}, {"$set": {"payment_status": "paid"}})
```

```python
# After: release only this retry claim; exact ledger-backed recovery owns paid
released = await db.update_one(
    "rides",
    {"id": ride_id, "payment_status": "retrying", "payment_retry_count": retry_count},
    {"$set": {"payment_status": "processing", ...}},
)
if released is None:
    continue
# Existing auto-heal is separately gated and verifies full ledger + Stripe proof.
```

## 8. Rollback plan

The auto-heal path is controlled by the existing `stripe_auto_heal_processing` app setting; leave or set it to `false` to stop automatic paid-state repair without a deploy. If the webhook terminal-ack or retry-claim behavior proves incorrect, revert the backend commits and redeploy both backend targets; already processed Stripe event IDs remain terminal and must be reviewed through the Stripe event/financial ledger audit before any manual replay. No Stripe charge, refund, or wallet mutation is performed by the new recovery path; a paid ride repaired from exact proof is backed by money and must not be blindly changed back to unpaid.

## 9. Verification performed

- [x] Automated tests: `AWS_EC2_METADATA_DISABLED=true /workspace/scratch/cf0084aae97a/spinr5716-venv/bin/python -m pytest -o addopts='' backend/tests/test_payment_retry.py backend/tests/test_payment_retry_coverage.py backend/tests/test_stripe_reconcile.py backend/tests/test_webhooks_main.py -q` — 190 passed.
- [ ] Manual repro steps followed in staging — not run; no staging Stripe/Supabase calls were made.
- [x] Blast-radius grep performed: `_maybe_heal_stuck_processing`, `_heal_one_processing_ride`, `_expected_capture_cents`, payment status reads/writes in `routes/rides/payments.py`, `routes/webhooks.py`, `utils/payment_retry.py`, `utils/stripe_reconcile.py`, plus `backend/core/lifespan.py` loop registrations.
- [x] Reviewed against CLAUDE money/idempotency, state-machine, and impact-log conventions and `.claude/context/domain-payments.md`.
- [x] Feature flag retained and default off for the existing auto-heal path; webhook permanent-underpayment acknowledgement is a backend event-handling correction and is not independently flag-gated.
- Production build: not applicable; backend-only change.
- Visual regression tooling: not applicable; backend-only change.
- What was not verified: no live Stripe, Supabase, staging, or production run; test fixtures mock external payment/database calls.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed.
- [x] User-visible behavior and mid-session impact are documented.
