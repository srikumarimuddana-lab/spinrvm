# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | Claude Code (session_01A5bKwpjGcssXLxRNbXG4nX) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (added in this branch's PR) |
| Related issue or gap ID | Third and final gap from the same test-ride finding as `2026-09-21-payment-retry-requires-capture-pre-trip-guard.md` and `2026-09-21-cancellation-refund-already-captured-hold.md`. Found 2026-09-22 while tracing a live-testing report on ride `SPR-XY55VL` |

## 1. Issue / gap identified

`backend/routes/webhooks.py`'s `payment_intent.payment_failed` branch stamps
`payment_status='failed'` + `payment_failure_reason` on a ride, and pushes
"Payment Failed ❌" to the rider (and the driver), based on a PaymentIntent that
is **not** the ride's real hold and whose failure means nothing about
collectability.

`utils/stripe_charge.authorize_ride` asks Stripe for an incremental
authorization on its first attempt. This Stripe account is not enrolled for it,
so Stripe refuses the whole request — **but still mints a real, FAILED
PaymentIntent** (documented at `stripe_charge.py`'s
`_account_incremental_auth_ineligible`) — and `authorize_ride` then retries
without the request and places the hold successfully. The refused PI's
`payment_failed` webhook then mislabels a ride whose hold is live and fine.

Two real scheduled rides lost money to the resulting chain:

| Ride | Date | Charged | Cancellation fee | Refunded |
|---|---|---|---|---|
| `SPR-XY55VL` | 2026-09-15 | $2.54 (full fare) | $0.00 | $0.00 |
| `SPR-RKYCJM` | 2026-09-16 | $2.10 (full fare) | $0.00 | $0.00 |

Both: `is_scheduled = true`, no driver ever assigned, `auth_status='captured'`,
`payment_status='paid'`, `payment_retry_count=1`, and
`payment_failure_reason = "This account is not eligible for the requested card
features."` — the exact string `_is_incremental_auth_ineligible` matches on.

## 2. Root cause

Three independent gaps chained. The first two were fixed 2026-09-21; **this
entry fixes the trigger**, which was left open by both.

1. **Trigger (this fix).** The refused pre-auth PI's failure event marks the
   ride `payment_status='failed'`. `payment_retry.py`'s scan filter is
   `payment_status in ("failed", "requires_action", "processing")`, so the
   mislabel is what put a healthy pre-trip ride in the retry loop's sights.
2. **Fixed 2026-09-21** (`payment-retry-requires-capture-pre-trip-guard`): that
   loop's `requires_capture` branch then captured the full `grand_total + tip`
   with no ride-status guard.
3. **Fixed 2026-09-21** (`cancellation-refund-already-captured-hold`): the
   cancellation flow had no refund path for an already-captured hold, so the
   rider's correctly-computed **$0** fee still left them paying full fare.

Why the existing orphan-ack did not cover it: the handler already detects a
pre-auth-sourced failure (`_preauth_orphan`), but gates it on `current is None`
— deliberately, because `metadata.source` is stamped once at PaymentIntent
creation and never updated, so a capture declined at settlement carries the
*same* source on the *same* PI and must still be recorded. `current is None`
only ever holds for the booking-time insert race. **A scheduled ride's row is
inserted at booking and its hold is placed minutes-to-days later at dispatch**
(`utils/scheduled_rides.py:507`, `block_on_decline=False`), so the row is
*always* present when the doomed PI's failure lands — the orphan ack could never
fire for the scheduled path.

Exact event ordering for these two rows is **not** log-confirmed (Fly.io
production logs were not reachable from this session). It is reconstructed from
the rows themselves, and the code-level gap is confirmed directly from source
independent of ordering. Corroborating evidence that the retry loop — not
`process_payment` — moved the money, despite the `financial_events` row reading
`source: "process_payment"`:

- `"process_payment"` is the **default** parameter value at
  `services/payment_service.py:346`; the retry loop calls
  `_finalize_card_settlement` without overriding `source`, so the label is a
  mislabel, not evidence.
- `routes/rides/payments.py:394` hard-409s `process_payment` unless
  `status == completed`. Both rides were `searching`.
- The ledger metadata carries `tax_amount: "0.00"` and `tax_breakdown: {}` while
  the ride rows carry `0.12` / `{"GST": …}`. That is the fingerprint of the
  retry loop's narrow `SELECT` (which omits both columns); `process_payment`
  reads the full ride row via `get_ride`.

## 3. Fix / remediation

New `_preauth_stage_failure` early return in the `payment_intent.payment_failed`
branch. When the failing PI carries `metadata.source ==
_PREAUTH_METADATA_SOURCE` **and** the ride's `status != RideStatus.COMPLETED`,
the handler acks the event (`mark_stripe_event_processed`) and returns
`{"received": True, "preauth_stage": True, …}` — the same shape the existing
orphan ack returns — instead of falling through to the CAS write and the
rider/driver pushes.

`status != completed` is the discriminator that makes this safe, and is
deliberately **the same one** the 2026-09-21 retry-loop fix uses. A capture
declined at settlement (`payment_service._settle_against_hold`) only ever
happens on a `completed` ride, so it still records and still pushes, exactly as
before.

Gated by the **existing** `webhook_preauth_failure_ack_enabled` app setting
(currently `true`), reused rather than adding a second flag: it is already the
switch for "do not treat a pre-auth failure as a settlement failure", and both
behaviours it now covers are the same decision. Setting it `false` restores the
previous behaviour for both, with no deploy.

**Adversarial alternative considered:** persist the account-level
incremental-authorization ineligibility (currently a process-local cache in
`stripe_charge.py`) so the doomed PaymentIntent is never minted at all. Rejected
as *the* fix, on two grounds: (a) it is narrower — it would not help the other
legitimate pre-trip pre-auth failure, a genuine card decline at scheduled
dispatch, which also must not mark a not-yet-settled ride `failed` (that ride
correctly degrades to post-trip settlement); and (b) the process-local scope is
a documented deliberate choice (a cache, not a setting, so a re-enrolled account
re-probes after a deploy), and persisting it needs a settings migration plus an
admin surface. Still worth doing as a follow-up — it would save a pointless
Stripe round-trip and one orphaned webhook per process start — but it is an
optimization, not this bug's fix. Recorded as a follow-up below.

## 4. Risk & impact on existing functionality

- **Blast radius: one new early return in one webhook branch.** Grepped every
  writer of `payment_failure_reason` across `backend/` — `webhooks.py:1113` is
  the **only** one (plus migration 414 which creates the column). No reader
  anywhere in `backend/routes`, `backend/utils`, `backend/services`, or
  `admin-dashboard/src`.
- **What else reads `payment_status`:** `utils/payment_retry.py`'s scan filter
  (`failed`/`requires_action`/`processing`), `routes/rides/payments.py`'s
  settlement guards, and `utils/payment_collection.py`'s
  `SETTLED_PAYMENT_STATUSES`. This change makes a pre-trip ride stay at its
  prior status (typically `pending`) instead of moving to `failed` — which
  *removes* it from the retry loop's scan. That is the intent: a ride with a
  live hold and no completed trip has nothing for that loop to collect, and its
  own `requires_capture` branch now skips it anyway (2026-09-21).
- **Could this regress a flow that currently works?** The one case that matters
  is a settlement capture decline on the same PI. It is excluded by
  `status != completed` and pinned by
  `test_completed_ride_still_records_a_capture_decline`. Non-pre-auth sources
  (`ride_completion_charge`, `cancellation_fee`) are untouched at any status and
  pinned by `test_non_preauth_source_on_a_pre_trip_ride_is_unchanged`.
- **Hold sweepers:** unchanged and still cover the affected rides.
  `orphaned_hold_reconciler` and `card_hold_release` select on
  `OPEN_AUTH_STATES = ("authorized", "fare_only")`, which is `auth_status`, not
  `payment_status` — this diff writes neither. A ride left at `pending` with a
  live `authorized` hold is exactly what those sweepers already watch.
- **Stripe event bookkeeping:** acking (not unclaiming) is required — an
  unclaimed event is redelivered by Stripe for days and would re-run this same
  evaluation. Matches the existing orphan-ack branch.
- **No money math, no Decimal arithmetic, no Stripe call added or removed.**

## 5. User-experience effect

Rider-facing, and visible mid-session to someone using the app right now: a
rider whose scheduled ride is being dispatched **no longer receives a spurious
"Payment Failed ❌" push** while their card hold is live and their ride is being
matched. The assigned driver no longer receives the matching "Rider payment
failed" push. Both were sent from *outside* the write branch, so skipping the
write alone would not have stopped them — pinned by
`test_pre_trip_preauth_failure_sends_no_payment_failed_push`.

No UI, copy, or screen change. Downstream, this is what stops a pre-trip ride
from being pulled into the payment-retry loop at all.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/webhooks.py` | Added `RideStatus` to both dual-import branches; added the `_preauth_stage_failure` early return in the `payment_intent.payment_failed` branch, behind the existing `webhook_preauth_failure_ack_enabled` setting | The actual fix — a pre-auth-stage PI failure must not mark a not-yet-settled ride payment-failed or notify anyone |
| `backend/tests/test_webhook_payment_failed_guard.py` | `_data_object` takes an optional `source` (omitted entirely when `None`, so every pre-existing test keeps its old shape); `_dispatch` takes `source`/`ack_preauth` and now also returns the `mark_stripe_event_processed` and `send_push_notification` mocks; new `TestPreAuthStageFailureIsNotASettlementFailure` (11 cases) | Regression-cover the guard, the push suppression, the completed-ride carve-out, the non-pre-auth sources, and the kill switch |

## 7. Before / after

```python
# Before — webhooks.py, payment_intent.payment_failed, ride row PRESENT
_observed_status = current.get("payment_status")
_observed_pi = current.get("payment_intent_id")
if _observed_status in _SETTLED_PAYMENT_STATUSES:
    ...          # ack, no write
elif _observed_pi and _observed_pi != payment_intent_id:
    ...          # ack, no write
else:
    await db_supabase.update_one(..., {"$set": {
        "payment_status": "failed",
        "payment_intent_id": payment_intent_id,
        "payment_failure_reason": failure_message,
    }})
# ...and then, OUTSIDE the branch, an unconditional
# "Payment Failed ❌" push to the rider and the driver.
```

```python
# After — a pre-auth PI failing before settlement is not a payment failure
_preauth_stage_failure = (
    data_object.get("metadata") or {}
).get("source") == _PREAUTH_METADATA_SOURCE and current.get("status") != RideStatus.COMPLETED
if _preauth_stage_failure:
    if _ack_preauth:                       # webhook_preauth_failure_ack_enabled
        await mark_stripe_event_processed(event_id)
        return {"received": True, "preauth_stage": True, "event_id": event_id}
# ...unchanged from here down; a COMPLETED ride still records and still pushes.
```

## 8. Rollback plan

**Flag off, no deploy:** set `webhook_preauth_failure_ack_enabled = false` in the
`settings` table (admin dashboard). That restores the previous behaviour for both
this branch and the pre-existing orphan ack. This is the intended rollback.

Code revert of this commit is also safe: pure logic addition, no migration, no
schema change, no data mutation, and no money moves in either direction on this
path. Nothing needs remediation to roll it back.

## 9. Verification performed

- [x] Syntax: `python -m py_compile` clean on both changed files.
- [x] Lint/format: `ruff check` — all checks passed; `ruff format --check` — both
      files already formatted.
- [x] Guard predicate verified against an 8-case matrix (pre-trip/scheduled/
      in_progress pre-auth → exempt; completed pre-auth → recorded; completion
      charge, cancellation fee, and no-source → recorded; no-source/no-status,
      i.e. the shape every pre-existing test in the file uses → recorded, so
      those tests are unaffected). All 8 as intended.
- [x] Blast-radius grep performed (Section 4): every writer/reader of
      `payment_failure_reason`, every reader of `payment_status`, the
      `OPEN_AUTH_STATES` sweepers.
- [x] Root cause corroborated against production data (Supabase, `ca-central-1`):
      both affected ride rows, their `financial_events` rows, the absence of any
      `stripe_refund` event for either, and the `settings` row confirming
      `scheduled_ride_notice_window_fee_enabled` is unset (so the flag-gated
      pre-dispatch notice fee is **off** and charged nothing here).
- [x] Reviewed against relevant CLAUDE.md conventions: dual-import pattern used
      for the new `RideStatus` import; `RideStatus.COMPLETED` rather than a
      hand-rolled `"completed"`; no float/Decimal money math introduced; a
      payment-path anomaly is still surfaced rather than swallowed (the acked
      case logs at `info` with the PI, ride and status, because it is a benign
      non-failure — the genuine-failure paths keep their existing `error`/
      `warning` levels and their 503/500 unclaim behaviour).
- [x] Adversarial review run before commit (`spinr-money-auditor`) — see
      Section 11.

### What was NOT verified

- [ ] **The test suite was not run.** This session's container has **no backend
      Python dependencies installed at all** (`pytest`, `fastapi`, `stripe`,
      `supabase`, `pydantic`, `loguru` all absent — the SessionStart hook's
      `pip install` failed) and the environment's network policy blocks PyPI, so
      `pip install` cannot recover it. The 11 new tests are written but
      **unexecuted**, and the ~15 pre-existing tests in the same file were not
      re-run to confirm they still pass. The helper changes were made
      specifically to be backwards-compatible (`source=None` omits the key
      entirely) and the predicate matrix covers their exact shape, but that is
      reasoning, not a green run. **This must be confirmed in CI before merge.**
- [ ] Manual repro in staging — not done; no staging Stripe test-mode session
      reproducing an incremental-authorization refusal.
- [ ] Production log confirmation of the event ordering on the two affected
      rides — Fly.io logs not reachable from this session (Section 2).
- [ ] No visual/snapshot regression tooling applies — backend-only change, no
      rider-app/driver-app/admin-dashboard surface touched.

## 10. Sign-off

- [x] Rollback plan is concrete and testable, and is a flag flip rather than a
      deploy
- [x] Blast radius is stated, not assumed (sole writer of
      `payment_failure_reason` identified; `payment_status` readers enumerated;
      sweepers shown to key on a different column)
- [x] No silent behavior change to an already-shipped flow without the UX field
      filled in (Section 5 names the two pushes that stop being sent)
- [ ] **Tests green — NOT satisfied in this session.** See "What was NOT
      verified". Do not merge on the strength of this entry alone.

## 11. Adversarial review (spinr-money-auditor, before commit)

See the reviewer's findings appended below / in the PR thread. Any finding it
raised that is fixed in this same commit is noted there.

## 12. Open follow-ups (not fixed here)

1. **The two affected riders have not been refunded.** `SPR-XY55VL` ($2.54) and
   `SPR-RKYCJM` ($2.10) — $4.64 total — are still `payment_status='paid'`,
   `refund_amount=0.00`, with a `stripe_charge` and no `stripe_refund` in
   `financial_events`. The 2026-09-21 cancellation fix is preventive only; it
   does not reach back. These need a deliberate refund + `record_refund_event`
   ledger write, authorized by a human. **Not** done in this session.
2. **Persist the incremental-authorization ineligibility** so the doomed
   PaymentIntent is never minted (Section 3's rejected alternative). Removes one
   wasted Stripe round-trip and one orphaned `payment_failed` webhook per
   process start.
3. **`financial_events.source` is misleading** for every row the retry loop
   writes: `_finalize_card_settlement` does not override the
   `source="process_payment"` default (`services/payment_service.py:346`), so a
   retry-loop capture is indistinguishable from an in-app settlement in the
   7-year ledger. This actively misled the first pass of this investigation.
   Worth a distinct `source` (e.g. `"payment_retry_capture"`), but it changes
   ledger metadata and deserves its own entry.
