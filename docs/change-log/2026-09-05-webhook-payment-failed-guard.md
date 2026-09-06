# Change Impact & Risk Log — `payment_intent.payment_failed` cannot overwrite a paid ride

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | Claude Code (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/pickup-otp-payment-fixes-5a8dnk` |
| Related issue or gap ID | `docs/audit/2026-09-05-engineering-director-review-round3.md` §1.10 finding **N1** (Major) |

## 1. Issue / gap identified

The `payment_intent.payment_failed` branch wrote `payment_status="failed"` and
swapped `payment_intent_id` with **no predicate on the ride's current payment
status and no check on which PaymentIntent the ride is settled against** — an
`update_ride` filtered on `id` alone. A redelivered failure event could
therefore flip an already-**paid** ride back to `failed`, prompting the rider to
pay a second time.

The second charge is not deduped: `settle_card` mints a fresh PI under
`ride-charge-{ride_id}-{cents}-{pm}` (`utils/stripe_charge.py:181`) while the
PaymentSheet path uses `ps-…` (`routes/payments.py:1339`), and nothing keys off
"a succeeded PI already exists". So this is a real double-charge path, not just a
mislabelled row.

## 2. Root cause

Every other status write in this codebase is a compare-and-swap — the audit
confirms "every ride status write is a compare-and-swap that returns 409". This
one was not, because it was written as a fire-and-forget bookkeeping update
rather than as a state transition. Nothing enforced the distinction.

The redelivery that makes it reachable is ordinary Stripe behaviour, and this
handler creates it deliberately: on a DB failure it calls
`unclaim_stripe_event(event_id)` and returns 500 so Stripe retries. That is
correct for a transient failure, but it means the same event legitimately
arrives again minutes later, by which time the ride may have settled.

## 3. Fix / remediation

Read the ride first, then compare-and-swap on exactly what was read:

1. **Ride not found** → unchanged: unclaim + 500 so Stripe retries.
2. **`payment_status` in `("paid", "waived_admin", "refunded")`** → the ride is
   settled; this event is a redelivery of a superseded attempt. Log at `info`
   and **ack** — deliberately *not* unclaim, or Stripe retries for days and
   re-runs the same overwrite.
3. **A different `payment_intent_id` is linked** → the failure belongs to a
   replaced attempt; recording it would relabel the live one. Ack.
4. **Otherwise** → `update_one` CASed on `{id, payment_status: <observed>,
   payment_intent_id: <observed>}`. Zero rows means someone wrote between the
   read and the CAS — their write is newer, so this failure is stale by
   definition. Ack.

Plain equality only, no `$or`, so the predicate is exactly what it reads as.
`{"payment_intent_id": None}` compiles to PostgREST `is.null`
(`repositories/_base.py:941-943`), not to a never-matching `= NULL`.

**`"processing"` is deliberately excluded from the settled set** even though the
`payment_succeeded` branch's `_already_settled` includes it: a ride still
processing has not settled, so a genuine failure on it must still be recorded.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend), confined to one webhook branch.**

- `_dispatch_stripe_event` has two callers: `stripe_webhook` (after the
  `claim_stripe_event` dedupe) and the admin replay endpoint
  (`routes/admin/stripe_events.py:65-77`). Both reach the changed branch, and
  the admin replay is precisely the path that makes a stale redelivery
  reachable on demand — it now becomes safe rather than destructive.
- `_SETTLED_PAYMENT_STATUSES` is a new module-level constant with one reader.
  It intentionally does **not** replace the existing `_already_settled` tuple in
  the `payment_succeeded` branch (different set, different purpose); that branch
  is untouched.
- `rides.payment_status` / `payment_intent_id` writers elsewhere are unaffected —
  this change only narrows when *this* handler writes.
- `utils/payment_retry.py:350` scans `payment_status IN ("failed",
  "requires_action", "processing")`. Fewer spurious `failed` rows means fewer
  rides pulled into the retry loop against an already-paid ride — strictly an
  improvement, but worth naming as a behaviour change in that loop's input set.

Interactions considered:

- **Stripe idempotency** — untouched; `claim_stripe_event` still runs upstream.
- **Ride state machine** — `rides.status` is not written here, only payment
  fields. No state transition, no WS event expected or removed.
- **Background loops** — only `payment_retry` reads these fields (above).

Regression risk: the added `get_ride` is **one extra DB read per
payment_failed event**. Against the < 500 ms Stripe-webhook SLA this is a single
indexed primary-key lookup on a low-frequency event (failures only), so the
budget is not at risk — but it is a new read on a path that had none.

The other regression to name honestly: a genuine failure is now **dropped**
(acked, not recorded) in the three stale cases. If the settled-state detection
is ever wrong, a real failure goes unrecorded and the rider is not told. That is
why `"processing"` was kept out of the settled set and why every non-terminal
state, including NULL, has an explicit test.

## 5. User-experience effect

- **Rider (visible):** a rider whose payment succeeded after an earlier failure
  no longer gets a "Payment Failed ❌" push and a second payment prompt for a
  ride they already paid for. Previously they could be charged twice.
- **Rider (unchanged):** a genuine first failure still flips the ride to
  `failed`, still sends the push, still feeds `payment_retry`.
- **Internal admin:** the admin Stripe-event replay is now safe to run against a
  settled ride; stale replays log at `info` instead of silently corrupting the
  row.
- No copy changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/webhooks.py` | Adds `_SETTLED_PAYMENT_STATUSES`; the `payment_intent.payment_failed` branch reads-then-CASes and acks stale redeliveries | Stop a redelivered failure relabelling a paid ride and triggering a second charge |
| `backend/tests/test_webhook_payment_failed_guard.py` | New | Pins the three stale cases, the ack-don't-unclaim rule, and that every non-terminal state still records |

## 7. Before / after

```python
# Before
updated = await db_supabase.update_ride(ride_id, {
    "payment_status": "failed",
    "payment_intent_id": payment_intent_id,
    "payment_failure_reason": failure_message,
})                                  # filtered on id ALONE
if updated is None:
    await unclaim_stripe_event(event_id)
    raise HTTPException(500, "Ride update failed — Stripe will retry")
```

```python
# After
current = await db_supabase.get_ride(ride_id)
if current is None:
    await unclaim_stripe_event(event_id); raise HTTPException(500, ...)

if current.get("payment_status") in _SETTLED_PAYMENT_STATUSES:
    logger.info(...)                                   # ack, stale
elif (pi := current.get("payment_intent_id")) and pi != payment_intent_id:
    logger.info(...)                                   # ack, superseded attempt
else:
    updated = await db_supabase.update_one(
        "rides",
        {"id": ride_id,
         "payment_status": current.get("payment_status"),
         "payment_intent_id": current.get("payment_intent_id")},
        {"$set": {...}},
    )
    if updated is None:
        logger.info(...)                               # ack, lost the CAS
```

Concrete scenario (gate 4 dry run) — a $42.50 card ride:

| Step | Before | After |
|---|---|---|
| PI on card 1 fails (event A) | `failed`, PI = `pi_A` | `failed`, PI = `pi_A` |
| DB blip on unclaim → A queued for redelivery | — | — |
| Rider retries; event B settles the ride | `paid`, PI = `pi_B` | `paid`, PI = `pi_B` |
| **A redelivered** | **`failed`, PI = `pi_A`** | **unchanged; `info` log, event acked** |
| Rider prompted to pay again | yes — second charge under a new key | no |

## 8. Rollback plan

No migration, no schema change, no live-data mutation introduced — this change
only makes an existing write *conditional*, so a `git revert` is a complete
rollback (it restores the unconditional overwrite, i.e. the bug).

No feature flag: a flag whose "off" position re-enables double-charging riders
is not a state worth being able to reach. If the stale detection ever proves too
aggressive, the narrower revert is to shrink `_SETTLED_PAYMENT_STATUSES` — a
one-tuple edit — rather than revert the CAS.

Rides already corrupted by this bug (settled, then relabelled `failed`, then
charged again) are **not** repaired by this change. They are identifiable as
rides with `payment_status = 'failed'` that nonetheless have a succeeded
PaymentIntent in Stripe, or with two succeeded PIs for one ride; remediation is a
refund of the duplicate charge. **That backfill is not performed here and is left
for a human to scope against production Stripe data.**

## 9. Verification performed

- [x] Blast-radius grep performed — `_dispatch_stripe_event` callers,
      `payment_status` writers/readers, `payment_retry.py`'s scan states,
      `_already_settled` (left untouched).
- [x] Filter-compiler behaviour checked against `repositories/_base.py`:
      `{"col": None}` → `q.is_(k, "null")` (line 941), plain values → `.eq`,
      and no `$or` used so no OR-builder edge case applies.
- [x] Reviewed against `CLAUDE.md` conventions: compare-and-swap on ride writes,
      Stripe idempotency (unchanged), error policy (stale = `info`, genuine
      failure still `warning`, missing ride still `error` + 500).
- [x] Before/after money scenario written out above (gate 4).
- [x] `ruff check` and `ruff format --check` clean.
- [ ] **Automated tests NOT run** — see below.

## What was NOT verified

**No tests were executed.** PyPI is blocked by this environment's network policy
(403), so backend dependencies could not be installed and `pytest` could not
run. `backend/tests/test_webhook_payment_failed_guard.py` is written but **has
never been run**. Its helper calls `_dispatch_stripe_event(event_id, event_type,
event_payload, data_object)` positionally against the real signature read from
source, but that call has not actually executed, and the branch is reached only
after the corporate-subscription and driver-subscription pre-checks earlier in
that function — which the stub set may not fully satisfy. **This is the most
likely place these tests fail on first run.** Existing webhook tests were not
re-run either.

Also not verified: no Stripe test-mode redelivery was performed, so the
end-to-end redelivery sequence in §7 is reasoned from the code, not observed; the
PostgREST compilation of the CAS filter was read from `_base.py` rather than
executed against a real Supabase; and the size of the already-corrupted
production set in §8 was not measured.
