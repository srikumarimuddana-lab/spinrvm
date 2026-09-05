# Change Impact & Risk Log — Card riders are charged the no-show fee

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | Claude Code (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments (secondary: rides, drivers) |
| PR / commit link | branch `claude/pickup-otp-payment-fixes-5a8dnk` |
| Related issue or gap ID | `docs/audit/2026-09-05-engineering-director-review-round3.md` §1.10 finding **N2** (Major) |

## 1. Issue / gap identified

`mark_rider_noshow` had a wallet debit branch and no card branch, so a card
rider's no-show fee was **never collected** — while `pay_driver_cancellation_fee`
below it still credited the driver the $4.00 driver share and the ride was
stamped with `cancellation_fee_admin`/`cancellation_fee_driver` as if the money
had been taken. Every card no-show cost the platform $4.00 paid out plus $0.50
of admin share never billed. The booking pre-auth hold was also neither captured
nor released, leaving the rider's card blocked until Stripe's ~7-day expiry.

## 2. Root cause

The rider-cancel path (`routes/rides/cancellation.py`) grew a full card
collection flow — hold partial-capture with a fresh-charge fallback, ledger
writes, and the WS-8 separate-PI-column handling — but the driver-side no-show
path in `routes/drivers/ride_cancel.py` was never brought along. It kept only
the wallet branch it was originally written with, and the `if payment_method ==
"wallet":` had no `else`, so a card ride fell through the fee block entirely and
silently. Nothing logged, because nothing failed — the code simply did not
consider the case.

The missing hold release is the same omission: `cancel_ride` in the very same
file releases the hold (WS-8, finding 11), `mark_rider_noshow` never did.

## 3. Fix / remediation

A new `_collect_noshow_fee_from_card()` helper mirrors the rider-cancel
collection order:

1. **Capture from the booking hold** when one is live (`auth_status` in
   `authorized`/`fare_only` with a PI). Funds are already reserved, so this fee
   cannot be declined for insufficient funds; the partial capture releases the
   remainder automatically, which doubles as the hold release.
2. **Fresh charge fallback** via `charge_ancillary_fee(fee_type="noshow_fee")`
   when there is no live hold, or the capture failed. A failed capture
   deliberately does **not** release the hold first — the fee is still owed and
   releasing would drop the only reserved funds before knowing if the fallback
   works.
3. **Zero-fee no-show now releases the hold**, matching `cancel_ride`.

`fee_type="noshow_fee"` produces the Stripe idempotency key
`noshow_fee-{ride_id}-{cents}-{pm}` (`utils/stripe_charge.py:376`), distinct
from a cancellation fee on the same ride, so a replayed no-show cannot
double-charge.

Both collection paths write a `stripe_charge` ledger event with the amount
**actually** taken (a capped capture must not book revenue never collected).
The fee PI is stored in `cancel_fee_payment_intent_id` (migration 251), never
overwriting the booking-time `payment_intent_id`.

**Deliberately unchanged:** the driver is still credited `fee_driver` even when
collection fails. That is the existing policy and reversing it would be a
driver-facing money regression outside this fix's scope; the uncollected amount
is now logged at `error` level so the platform-funded gap is visible instead of
silent.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend), confined to the no-show path.** Greps:

- `charge_ancillary_fee(` — 3 call sites: the two in `cancellation.py`
  (`cancellation_fee`, `scheduled_cancel_notice_fee`) and this new one. Distinct
  `fee_type` values mean distinct idempotency keys; no key collision introduced.
- `capture_cancellation_fee(` — previously only `cancellation.py:166`; helper is
  unmodified, so the rider-cancel path is untouched.
- `cancel_fee_payment_intent_id` — column from migration 251; written by
  `cancellation.py:401` and now here. No reader anywhere in backend,
  admin-dashboard or rider-app, so adding a second writer regresses nothing.
- `mark_rider_noshow` — one route (`POST /drivers/rides/{id}/noshow`), one
  caller (driver app).

Interactions considered:

- **Ride state machine** — untouched. The atomic `driver_arrived → cancelled`
  claim still happens *before* any money moves; all new code runs after it, so
  the 409-on-race behaviour and "nothing charged on a lost claim" property
  (`test_c2_driver_cancel_atomic.py`) are preserved.
- **Insurance periods** — untouched; the Period 1 gating on
  `released.get("is_available")` is below all new code and unchanged.
- **Wallet path** — byte-identical. The `if payment_method == "wallet":` body is
  unmodified; only its enclosing `if total_fee > 0:` gained an `elif`.
- **`payment_retry.py`** — scans by PI. Because the fee PI goes in its own
  column, the booking PI it may already be tracking is not swapped underneath
  it. This was the specific failure mode WS-8 was written to prevent.
- **Money rules** — `Decimal` throughout; the only `float()` in this file is the
  pre-existing DB write of the fee columns, untouched.

Regression risk, stated plainly: **card riders who no-show will now actually be
charged $4.50 where previously they were charged nothing.** That is the point of
the fix, but it is a real, user-visible money change on a live-tested surface —
riders currently in testing who no-show will see a charge they would not have
seen last week. It is also the first time this path calls Stripe at all, so a
Stripe outage now produces logged collection failures on no-shows where before
there was simply no call.

## 5. User-experience effect

- **Rider (card, visible):** a no-show now results in a real $4.50 charge —
  either captured from the existing booking hold (the common case, and the
  rider's hold is reduced to the fee rather than released in full) or as a fresh
  charge. Previously: no charge, and the full hold sat on their card for ~7 days.
- **Rider (card, zero-fee no-show):** hold is now released immediately instead
  of expiring in ~7 days — strictly better.
- **Rider (wallet):** no change.
- **Driver:** no change — still credited `fee_driver` in every case, including
  when collection fails.
- **Internal admin:** ride rows for card no-shows now carry
  `cancel_fee_payment_intent_id` and a `payment_status`, where before they had
  fee columns with no corresponding payment.
- No copy changes; no new notification.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/ride_cancel.py` | Adds `_collect_noshow_fee_from_card()`; `mark_rider_noshow` gains the card branch, the zero-fee hold release, and persists the fee PI / payment status | Collect the fee that was silently skipped; release the hold that was silently left open |
| `backend/tests/test_noshow_card_fee_collection.py` | New | Pins hold-capture-first ordering, the fallback, the no-release-on-capture-failure rule, and that the wallet path is unchanged |

## 7. Before / after

```python
# Before — the entire rider-charging block
if total_fee > 0:
    payment_method = (ride.get("payment_method") or "card").lower()
    if payment_method == "wallet":
        ...wallet_apply_delta(delta=-total_fee, reference_id=ride_id)...
    # card riders: no branch at all — fall through, nothing charged

# Pay driver
if fee_driver > 0:
    await pay_driver_cancellation_fee(...)   # $4.00 out, $0.00 in
```

```python
# After
if total_fee > 0:
    if payment_method == "wallet":
        ...unchanged...
    elif payment_method == "card":
        _fee_collected, _fee_pi = await _collect_noshow_fee_from_card(...)
elif _hold_is_live:
    # zero fee -> release the hold instead of leaving it to expire
    await _deps.cancel_authorization(ride_id=ride_id, payment_intent_id=_booking_pi)

# Pay driver — unchanged policy
if fee_driver > 0:
    await pay_driver_cancellation_fee(...)
```

Concrete scenario (the dry run this change needs per CLAUDE.md gate 4):
a $30.00 card ride, rider no-shows after the 5-minute wait. Booking hold
`pi_booking_x` for $30.00, `auth_status="authorized"`. Fee = $0.50 admin +
$4.00 driver = $4.50.

| | Before | After |
|---|---|---|
| Charged to rider | $0.00 | $4.50 (captured from the hold) |
| Remaining hold | $30.00, expires in ~7 days | released by the partial capture |
| Paid to driver | $4.00 | $4.00 (unchanged) |
| Platform net | **−$4.00** | +$0.50 |
| `rides.cancel_fee_payment_intent_id` | unset | `pi_booking_x` |
| `rides.auth_status` | `authorized` (stale) | `captured` |

## 8. Rollback plan

No migration and no schema change — `cancel_fee_payment_intent_id` (migration
251) and `auth_status` already exist and are already written by the rider-cancel
path.

A `git revert` restores the previous behaviour for all *future* no-shows, but is
**not** a complete rollback for money already moved: Stripe captures and charges
made while this is live are real. Remediating those requires refunding the
affected `noshow_fee` PaymentIntents — they are identifiable by the Stripe
idempotency-key prefix `noshow_fee-` and by `rides.cancel_fee_payment_intent_id`
on rides with `cancellation_type = 'noshow'`, so the blast set is enumerable.

There is no feature flag. **This is the weakest point of this change** and worth
a reviewer's attention: CLAUDE.md gate 3 asks for a flag on user-visible,
non-trivial changes, and "riders start getting charged a fee they weren't"
qualifies. A flag was not added because the alternative (`app_settings` read on
the no-show path) would itself be a new failure mode on a money path, and the
existing `noshow_fee_admin`/`noshow_fee_driver` settings already provide a
without-redeploy kill switch: **setting both fee values to 0 in `app_settings`
makes `total_fee == 0`, which skips all collection entirely** (and now releases
the hold). That is the rollback lever to pull first.

## 9. Verification performed

- [x] Blast-radius grep performed — `charge_ancillary_fee`, `fee_type=`,
      `capture_cancellation_fee`, `cancel_fee_payment_intent_id`,
      `mark_rider_noshow` (listed in §4).
- [x] Reviewed against `CLAUDE.md` conventions: Decimal-only money math (no new
      `float()`), Stripe idempotency (distinct `fee_type` → distinct key),
      dual-import pattern, ride state machine (claim-before-charge preserved),
      error policy (`logger.error` on every collection failure, never `warning`).
- [x] Before/after money scenario written out above (gate 4).
- [x] `ruff check` and `ruff format --check` clean.
- [ ] **Automated tests NOT run** — see below.
- [ ] Not feature-flagged — justified in §8.
- [ ] Not exercised against a Stripe test key.

## What was NOT verified

**No tests were executed.** This environment's network policy blocks PyPI
(403), so backend dependencies could not be installed and `pytest` could not
run. `backend/tests/test_noshow_card_fee_collection.py` is written but **has
never been run**, and the existing `test_c2_driver_cancel_atomic.py` no-show
cases were not re-run against this change either — they exercise the same
function and their fee stubs interact with the restructured branch, so they are
the most likely place a mistake here shows up. **Both files need a real `pytest`
run before merge.**

Also not verified: no Stripe test-mode call was made, so `capture_cancellation_fee`
and `charge_ancillary_fee` behaviour is taken from their docstrings and from the
rider-cancel path's use of them, not observed here — in particular the claim
that a partial capture auto-releases the remainder is Stripe's documented
behaviour, not something re-confirmed in this session. The
`cancel_fee_payment_intent_id` and `auth_status` writes were not exercised
against a real Supabase, so the PGRST204 fallback path (older schema) is
reasoned about, not tested. No staging repro of a card no-show end to end.
