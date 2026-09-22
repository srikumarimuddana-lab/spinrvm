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
"Payment Failed ❌" to the rider (and the assigned driver), based on a
PaymentIntent that is **not** the ride's real hold and whose failure says
nothing about collectability.

`utils/stripe_charge.authorize_ride` asks Stripe for an incremental
authorization on its first attempt. This account is not enrolled, so Stripe
refuses the whole request — **but still mints a real, FAILED PaymentIntent**
(documented at `stripe_charge.py`'s `_account_incremental_auth_ineligible`) —
and `authorize_ride` then retries without the request and places the hold
successfully. The refused PI's `payment_failed` webhook then mislabels a ride
whose hold is live and fine.

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
entry fixes the trigger**, which both left open.

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

Why the existing pre-auth ack did not cover it: the handler already detects a
pre-auth-sourced failure, but gated it on `current is None` — deliberately,
because `metadata.source` is stamped once at PaymentIntent creation and never
updated, so later captures on the same PI carry the same source and must still
be recorded. `current is None` only ever holds for the booking-time insert race.
**A scheduled ride's row is inserted at booking and its hold is placed
minutes-to-days later at dispatch** (`utils/scheduled_rides.py:488-513`,
`block_on_decline=False`), so the row is *always* present when the doomed PI's
failure lands — the row-absent ack could never fire for the scheduled path.

Exact event ordering for these two rows is **not** log-confirmed (Fly.io
production logs were not reachable from this session). It is reconstructed from
the rows themselves; the code-level gap is confirmed directly from source
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
_PREAUTH_METADATA_SOURCE` **and** the ride's `status` is in the new
`_PRE_SETTLEMENT_RIDE_STATUSES` allowlist, the handler acks the event
(`mark_stripe_event_processed`) and returns `{"received": True,
"preauth_stage": True, …}` — the same shape the row-absent ack returns —
instead of falling through to the CAS write and the rider/driver pushes.

```python
_PRE_SETTLEMENT_RIDE_STATUSES = frozenset({RideStatus.SCHEDULED}) | RideStatus.active_statuses()
```

**The allowlist is deliberate, and replaced a `status != RideStatus.COMPLETED`
check that the pre-commit adversarial review caught as a blocker** — see
Section 11, finding 1. An allowlist fails toward *recording* for any status it
does not positively recognise, because wrongly recording costs one redundant
push while wrongly acking swallows a real payment failure *and* makes it
unreplayable (admin replay refuses an already-processed event).

Two states are excluded on purpose, and both are real capture sites against the
same metadata source on the same PI:

| Excluded status | Capture site |
|---|---|
| `completed` | `services/payment_service.py::_settle_against_hold` — capture declined at settlement |
| `cancelled` | `routes/rides/cancellation.py:171`'s `capture_cancellation_fee` — cancellation-fee partial capture declined, and it runs *after* the atomic claim has already written `cancelled` |

**Adversarial alternative considered:** persist the account-level
incremental-authorization ineligibility (currently a process-local cache in
`stripe_charge.py`) so the doomed PaymentIntent is never minted at all. Rejected
as *the* fix on two grounds: (a) it is narrower — it would not help the other
legitimate pre-trip pre-auth failure, a genuine card decline at scheduled
dispatch, which also must not mark a not-yet-settled ride `failed` (that ride
correctly degrades to post-trip settlement); and (b) the process-local scope is
a documented deliberate choice (a cache, not a setting, so a re-enrolled account
re-probes after a deploy), and persisting it needs a settings migration plus an
admin surface. Still worth doing as a follow-up — it would save a pointless
Stripe round-trip and one orphaned webhook per process start — but it is an
optimization, not this bug's fix.

## 4. Risk & impact on existing functionality

- **Blast radius: one new early return in one webhook branch.** Grepped every
  writer of `payment_failure_reason` across `backend/` — `webhooks.py` is the
  **only** one (plus migration 414 which creates the column). No reader anywhere
  in `backend/routes`, `backend/utils`, `backend/services`, or
  `admin-dashboard/src`.
- **What else reads `payment_status`:** `utils/payment_retry.py`'s scan filter
  (`failed`/`requires_action`/`processing`), `routes/rides/payments.py`'s
  settlement guards, and `utils/payment_collection.py`'s
  `SETTLED_PAYMENT_STATUSES`. This change makes a pre-settlement ride stay at
  its prior status (typically `pending`) instead of moving to `failed` — which
  *removes* it from the retry loop's scan. That is the intent: a ride with a live
  hold and no completed trip has nothing for that loop to collect.
- **Both other capture sites against the same PI are excluded** (Section 3's
  table), each with its own regression test. This was finding 2 of the
  adversarial review.
- **Hold sweepers:** unchanged. `orphaned_hold_reconciler` and
  `card_hold_release` select on `OPEN_AUTH_STATES = ("authorized",
  "fare_only")`, which is `auth_status`, not `payment_status` — this diff writes
  neither. A ride left at `payment_status='pending'` with a live `authorized`
  hold is the ordinary pre-trip steady state of every scheduled/searching ride,
  not a newly-orphaned one.
- **Defense in depth, not the sole guard.** `payment_retry.py:576-603` already
  carries its own independent `status != COMPLETED` check (2026-09-21), so the
  full-fare-capture vector is closed even without this change. This removes the
  upstream trigger and the false rider notification.
- **Stripe event bookkeeping:** acking (not unclaiming) is required — an
  unclaimed event is redelivered by Stripe for days and would re-run the same
  evaluation. Matches the row-absent ack.
- **No money math, no Decimal arithmetic, no Stripe call added or removed.**

## 5. User-experience effect

Rider-facing, and visible mid-session to someone using the app right now: a
rider whose scheduled ride is being dispatched **no longer receives a spurious
"Payment Failed ❌" push** while their card hold is live and their ride is being
matched. The assigned driver no longer receives the matching "Rider payment
failed" push. Both are sent from *outside* the write branch, so suppressing the
write alone would not have stopped them — pinned by
`test_pre_settlement_ride_with_a_row_is_acked`.

No UI, copy, or screen change. Downstream, this is what stops a pre-trip ride
from being pulled into the payment-retry loop at all.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/webhooks.py` | Added `RideStatus` to **both** dual-import branches; added the `_PRE_SETTLEMENT_RIDE_STATUSES` allowlist constant; added the `_preauth_stage_failure` early return, behind the existing `webhook_preauth_failure_ack_enabled` setting | The actual fix — a pre-auth-stage PI failure must not mark a pre-settlement ride payment-failed or notify anyone |
| `backend/tests/test_webhook_payment_failed_guard.py` | 5 new cases (10 with parametrization) appended to the **existing** `TestPreauthStageFailureIsAcked` class, reusing its `_run`/`_PREAUTH` helpers; extended that class's docstring | Regression-cover the row-present pre-settlement ack, the absent-status fail-toward-recording direction, both excluded capture sites, and the kill switch on this half |

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
).get("source") == _PREAUTH_METADATA_SOURCE and current.get("status") in _PRE_SETTLEMENT_RIDE_STATUSES
if _preauth_stage_failure:
    if _ack_preauth:                       # webhook_preauth_failure_ack_enabled
        await mark_stripe_event_processed(event_id)
        return {"received": True, "preauth_stage": True, "event_id": event_id}
# ...unchanged from here down. completed / cancelled / absent / unrecognised
# statuses all still record and still push.
```

## 8. Rollback plan

**Flag off, no deploy:** set `webhook_preauth_failure_ack_enabled = false` in the
`settings` table (admin dashboard). This is the intended rollback.

⚠️ **Note the asymmetry** (adversarial review finding 3): that one flag now
governs two acks with very different blast radii. Disabling it reverts the
row-absent ack (whose failure mode is 3-day Stripe retry noise and a 500 per
booking — loud but harmless) *and* this row-present ack (whose failure mode is
the money-loss trigger described in Section 2). An operator flipping it off to
investigate the first silently reopens the second. Left as one flag deliberately
— they are the same decision, "a pre-auth failure is not a settlement failure",
and a second flag is configurability nobody asked for — but the asymmetry is
recorded here and pinned by
`test_kill_switch_also_restores_recording_for_a_pre_settlement_ride`. If the
team would rather split it, that is a small follow-up.

Code revert of this commit is also safe: pure logic addition, no migration, no
schema change, no data mutation, no money moves either way on this path.

## 9. Verification performed

- [x] Lint/format: `ruff check` — all checks passed; `ruff format --check` —
      both files already formatted.
- [x] Syntax: `python -m py_compile` clean on both changed files.
- [x] Allowlist verified against the **real** `RideStatus` enum (imported, not
      reasoned about) and against **every pre-auth fixture actually present in
      the test file**, including the status-less one at
      `test_capture_decline_on_an_existing_ride_is_still_recorded`. 8/8 as
      intended: orphan → ack; pre-settlement `searching`/`scheduled` → ack;
      `completed`, `cancelled`, absent-status, superseded-PI, and
      non-pre-auth-source → record.
- [x] `str`-Enum set-membership trap checked empirically rather than assumed —
      `hash(RideStatus.SEARCHING) == hash("searching")` is True, so a plain
      string read from the DB row matches the enum allowlist.
- [x] Blast-radius grep performed (Section 4): every writer/reader of
      `payment_failure_reason`, every reader of `payment_status`, the
      `OPEN_AUTH_STATES` sweepers, and both capture sites against the booking
      hold PI.
- [x] Root cause corroborated against production data (Supabase, `ca-central-1`):
      both affected ride rows, their `financial_events` rows, the absence of any
      `stripe_refund` event for either, and the `settings` row confirming
      `scheduled_ride_notice_window_fee_enabled` is unset (so the flag-gated
      pre-dispatch notice fee is **off** and charged nothing here).
- [x] Reviewed against relevant CLAUDE.md conventions: dual-import pattern used
      for the new `RideStatus` import (verified in both branches after a
      formatter silently dropped the second one); `RideStatus` members rather
      than hand-rolled status strings; no float/Decimal money math introduced;
      `routes/webhooks.py` uses stdlib `logging`, not loguru, so the `%s`-style
      positional log calls are correct for this file.
- [x] Adversarial review run (`spinr-money-auditor`) — **1 blocker + 4 warnings;
      see Section 11.** The blocker and two warnings are fixed in this branch.

### What was NOT verified

- [ ] **The test suite was not run.** This session's container has **no backend
      Python dependencies installed at all** (`pytest`, `fastapi`, `stripe`,
      `supabase`, `pydantic`, `loguru` all absent — the SessionStart hook's
      `pip install` failed) and the environment's network policy blocks PyPI, so
      `pip install` cannot recover it. The new cases are written but
      **unexecuted**, and the ~23 pre-existing tests in the same file were not
      re-run. **CI must be the gate before merge.** Note the first revision of
      this fix would have broken a pre-existing test and only an adversarial
      review caught it — see Section 11, finding 1 — so treat an unrun suite
      here as a real risk, not a formality.
- [ ] Manual repro in staging — not done; no staging Stripe test-mode session
      reproducing an incremental-authorization refusal.
- [ ] Production log confirmation of the event ordering on the two affected
      rides — Fly.io logs not reachable from this session (Section 2).
- [ ] No visual/snapshot regression tooling applies — backend-only change, no
      rider-app/driver-app/admin-dashboard surface touched.

## 10. Sign-off

- [x] Rollback plan is concrete and testable, is a flag flip rather than a
      deploy, and records the flag's asymmetric blast radius
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field
      filled in (Section 5 names the two pushes that stop being sent)
- [ ] **Tests green — NOT satisfied in this session.** See "What was NOT
      verified". Do not merge on the strength of this entry alone.

## 11. Adversarial review (spinr-money-auditor)

Verdict: **FIX BLOCKERS**. Run against the first revision of this change.

1. **BLOCKER — fixed in this branch.** The original discriminator,
   `current.get("status") != RideStatus.COMPLETED`, evaluates `True` when
   `status` is simply absent from the ride dict (`None != "completed"`), so it
   acked instead of recording. That broke a **pre-existing** test —
   `TestPreauthStageFailureIsAcked.test_capture_decline_on_an_existing_ride_is_still_recorded`,
   whose fixture carries no `status` key and whose docstring calls this "the
   blocker this guard was nearly shipped with." The author of the row-absent ack
   had chosen that condition specifically to make swallowing *structurally
   impossible*; keying on `!= COMPLETED` replaced that with "depends on a field
   being present." **Fixed** by replacing the check with the
   `_PRE_SETTLEMENT_RIDE_STATUSES` allowlist, which fails toward recording for
   an absent or unrecognised status. The pre-existing test passes unchanged; a
   new test (`test_absent_status_records_rather_than_swallowing`) pins the
   direction explicitly.
   *Root cause of my own error: I read only the first 200 lines of a 477-line
   test file and never saw that class, then asserted in the first commit message
   and change-log that "the pre-existing tests in the file keep their old
   behaviour." That claim was false — the 8-case matrix I ran checked the
   no-source/no-status cell, not the yes-source/no-status cell that actually
   breaks.*
2. **WARNING — fixed in this branch.** The comment claimed a capture decline
   "only ever happens on a completed ride", but `routes/rides/cancellation.py:171`
   calls `capture_cancellation_fee` on the same source/PI while the ride is
   `cancelled`. Traced as low-severity (cancellation.py writes
   `payment_status`/`cancel_fee_payment_intent_id` itself, so the status does not
   end up wrong; the loss was the `payment_failure_reason` detail and one
   redundant push) — but the claim was incomplete. **Fixed** by excluding
   `cancelled` from the allowlist, documenting both capture sites in Section 3,
   and adding
   `test_cancelled_ride_still_records_a_cancellation_fee_capture_decline`.
3. **WARNING — documented, not changed.** Flag coupling. See Section 8's
   asymmetry note.
4. **WARNING — accepted, pre-existing.** `get_app_settings()` failure defaults
   `_ack_preauth = True`, so an app_settings outage silently acks every
   pre-auth-sourced failure for its duration, and `routes/webhooks.py` emits no
   metrics at all (grep-confirmed), so that window is invisible. Consistent with
   the pre-existing orphan ack's behaviour; not introduced here. Worth a metric,
   which belongs with follow-up 4 below.
5. **WARNING — pre-existing, adjacent, not touched.** The "already settled" and
   "different PI linked" staleness branches skip the CAS write but do **not**
   return early, so a stale redelivery on an already-paid ride still sends the
   rider a "Payment Failed ❌" push today. This diff's new branch avoids that
   trap for its own case; the same fix should be applied to those two siblings.
   Recorded as follow-up 5 — deliberately out of scope here rather than widening
   a money-path diff.

Items the review checked and found clean: dual-import correctness;
`_settle_against_hold` and `increment_authorization` reachable only on
`completed` rides (all four call sites read); `rides.status` reliably present on
a real `get_ride` row (`select("*")`, and `NOT NULL DEFAULT 'searching'` at
`supabase_schema.sql:180`) — which is why finding 1 is a defensive/test gap, not
a reachable production gap; zero interaction with the `auth_status` hold
sweepers; acking rather than unclaiming is correct; no metrics bookkeeping
skipped by the early return.

## 12. Open follow-ups (not fixed here)

1. **The two affected riders have not been refunded.** `SPR-XY55VL` ($2.54) and
   `SPR-RKYCJM` ($2.10) — $4.64 total — are still `payment_status='paid'`,
   `refund_amount=0.00`, with a `stripe_charge` and no `stripe_refund` in
   `financial_events`. The 2026-09-21 cancellation fix is preventive only; it
   does not reach back. Needs a deliberate refund + `record_refund_event` ledger
   write, authorized by a human. **Not** done in this session.
2. **Persist the incremental-authorization ineligibility** so the doomed
   PaymentIntent is never minted (Section 3's rejected alternative).
3. **`financial_events.source` is misleading** for every row the retry loop
   writes: `_finalize_card_settlement` does not override the
   `source="process_payment"` default (`services/payment_service.py:346`), so a
   retry-loop capture is indistinguishable from an in-app settlement in the
   7-year ledger. This actively misled the first pass of this investigation.
   Worth a distinct `source` (e.g. `"payment_retry_capture"`), but it changes
   ledger metadata and deserves its own entry.
4. **No metrics in `routes/webhooks.py` at all** (review finding 4). A
   `spinr_payment_*` counter on the ack paths would make both the normal volume
   and an app_settings-outage window visible.
5. **The two sibling staleness branches still push** (review finding 5).
