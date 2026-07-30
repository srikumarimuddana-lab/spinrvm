# Change Impact & Risk — auto-cancelled rides left the rider's card held (T2b)

**Date:** 2026-07-30 · **Branch:** `claude/critical-security-pipeda-breach-pn67ww`
**Surface:** backend (background loop, money path) · **Risk:** high user impact, low code risk
**Related:** `routes/rides/cancellation.py` "WS-8 finding 11", `migrations/156_ride_preauth_columns.sql`

---

## Issue / gap identified

When the stuck-ride sweeper auto-cancelled a ride that had been stuck in `searching`
for 5 minutes, it left the booking-time card authorization **live on the rider's
card** until Stripe expired it — roughly 7 days.

`routes/rides/booking.py:191` places a manual-capture PaymentIntent hold for
`grand_total + RIDE_AUTH_BUFFER_CAD` **before** dispatch, deliberately, so a dead
card is surfaced before a driver is committed. That means every ride sitting in
`searching` has real funds reserved. The sweeper cancelled the ride row and never
released the hold.

## Root cause

The fix already existed on the other cancel path and was missed here.
`routes/rides/cancellation.py:106-118` releases the hold via `cancel_authorization`
and marks `auth_status = "released"` at line 284 — the comment labels it
"WS-8 (finding 11)", so this exact failure had been found and fixed once for
rider/driver-initiated cancellations. The sweeper cancels with a direct
`supabase.table("rides").update(...)`, bypassing that path entirely, and was never
brought along.

Same shape as T1 in this branch: a known bug fixed at two call sites and missed at a
third.

**Nothing else cleaned it up** — checked each candidate rather than assuming:

| Loop | Why it doesn't cover this |
|---|---|
| `utils/preauth_capture._capture_tick` | Filters `status: "completed"`. A swept ride is `cancelled`, so it is never selected — and it *captures* rather than releases, which would be worse. |
| `utils/stripe_reconcile` | Heals stuck **processing** rides (post-completion, mark-paid only). |
| `utils/stale_intent_reconciler` | Despite the name, it reconciles driver *online intent*, not payment intents. |

`cancel_authorization`'s own docstring states the consequence: *"the old hold must be
released or the rider's funds stay reserved until Stripe's ~7-day auth expiry."*

## Concrete before/after scenario

A rider books a $22.50 ride with `RIDE_AUTH_BUFFER_CAD = 5.00`; no driver accepts.

| | Before | After |
|---|---|---|
| T+0 | Hold placed: **$27.50** authorized, `auth_status='authorized'` | same |
| T+5min | Sweeper cancels ride, notifies rider "no drivers found" | same |
| Hold state | **still authorized — $27.50 reserved** | `PaymentIntent.cancel` → funds released |
| `auth_status` | still `'authorized'` (looks like an open hold forever) | `'released'` |
| Rider's card | $27.50 unavailable for **~7 days** | available within seconds |
| Rider's statement | a pending charge for a ride that never happened | nothing |

On a debit card or a near-limit credit card that is real harm, and it is invisible to
us — the ride shows as cleanly cancelled.

## Fix / remediation

`_release_booking_hold(ride)` in `utils/stuck_ride_sweeper.py`, called per claimed
ride:

1. Skip unless `payment_intent_id` is set **and** `auth_status` is in
   `('authorized', 'fare_only')` — the two open states defined by migration 156.
   This guard is the important one: calling `PaymentIntent.cancel` on a `captured`
   intent would attempt to reverse money the rider actually owes, which is a worse
   bug than the one being fixed.
2. `cancel_authorization(ride_id=…, payment_intent_id=…)` — already idempotent via
   its Stripe `idempotency_key`, and already no-raise.
3. On success, mark `auth_status='released'`, filtered on the open states so a
   concurrent writer cannot be clobbered.
4. On failure, **do not** mark released — a false `'released'` would hide a live hold
   from any future reconciler. Log at `error` (a stranded hold is a payment-path
   anomaly; CLAUDE.md requires payment errors to surface loudly) and continue.

Three distinct metric outcomes on `spinr_stuck_ride_hold_release_total`, because they
need different operator responses: `released`, `failed` (money may still be held —
investigate), `released_unmarked` (money is free, bookkeeping drifted — no rider
impact).

**Called before the WS/push notifications**, deliberately. Those are network
round-trips that can block for seconds, and CLAUDE.md's anti-patterns list calls out
awaiting Twilio/Stripe inline; money integrity should not queue behind a push. Both
notify calls already tolerate failure independently, so nothing depends on the old
order. There is a test pinning the ordering.

## Risk & impact on existing functionality

**Blast radius — deliberately narrow.** The new code is reachable only from
`_sweep()`, only for rides the atomic claim just transitioned `searching → cancelled`.

- **Replay safety across replicas.** The sweeper runs on every replica, but the claim
  (`.eq("status","searching")`) is atomic, so exactly one replica receives a given
  ride in `claimed_rides` and therefore makes exactly one release call.
  `cancel_authorization`'s Stripe idempotency key (`ride-cancelauth-{ride}-{pi}`)
  makes a retry harmless even if that guarantee were ever violated.
- **Callers of `cancel_authorization`**: grepped — `routes/rides/cancellation.py`
  (via `_deps`) and now this sweeper. The function itself is unmodified, so the
  existing caller is unaffected.
- **`auth_status` readers**: `utils/preauth_capture` (`$in ['authorized','fare_only']`),
  `utils/payment_retry`, `services/payment_service`, `utils/scheduled_rides`. Writing
  `'released'` moves the row *out* of the open set, which is exactly what those
  readers want — a cancelled ride should never be a capture candidate. Before this
  change, a swept ride sat in the open set indefinitely while being uncapturable
  (wrong `status`), so this also removes permanent junk from that partial index.
- **One extra Stripe call and one extra DB write per swept ride.** The sweeper
  handles a handful of rides per 60-second tick; not a hot path.
- **No change to the ride state machine.** The row is already `cancelled` by the
  claim before this runs; only `auth_status` and `updated_at` are touched.

## User experience effect

**Rider-facing, and strictly an improvement.** Money that was previously reserved for
~7 days after a failed booking is now released within seconds. No screen, copy, or
API response changes — the rider simply stops seeing a pending charge for a ride that
never happened. Nothing is visible mid-session beyond the hold disappearing.

No driver-, corporate-admin-, or internal-admin-facing change.

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/utils/stuck_ride_sweeper.py` | Added `_release_booking_hold()` + `_OPEN_AUTH_STATES`; called per claimed ride before the notifications; imports `cancel_authorization` | Release the hold the sweeper was orphaning |
| `backend/tests/test_stuck_ride_sweeper.py` | +13 tests | Pin the release, the guard, the failure modes and the ordering |
| `docs/change-log/2026-07-30-stuck-ride-sweeper-orphaned-card-hold.md` | New — this file | Required by CLAUDE.md |

## Before / after

```python
# BEFORE — utils/stuck_ride_sweeper.py: ride cancelled, hold orphaned
for ride in claimed_rides:
    ride_id = ride.get("id")
    rider_id = ride.get("rider_id")
    driver_id = ride.get("driver_id")

    if rider_id:
        ... send WS "ride_cancelled" ...
        ... send push "No drivers available" ...
    if driver_id:
        await db_supabase.set_driver_available(driver_id, True)
    # payment_intent_id / auth_status never touched

# AFTER — money released first, then notify
for ride in claimed_rides:
    ...
    await _release_booking_hold(ride)   # PaymentIntent.cancel + auth_status='released'
    if rider_id:
        ... send WS ...
        ... send push ...
```

## Rollback plan

`git revert` is safe for the code, but **it is not a rollback for money already
moved** — and here that cuts in the harmless direction. Reverting stops *future*
releases; it cannot un-release a hold that was already cancelled, and it does not
need to: releasing an authorization on a cancelled ride returns the rider's own funds
and creates no charge, no refund and no Stripe fee.

If the release must be stopped urgently without a deploy, the operational lever is
the sweeper itself, not this function — the loop is one of the 16 startup loops in
`core/lifespan.py`. There is no `app_settings` flag gating it today; adding one was
considered and rejected as scope creep for a fix that only ever *returns* money.

Rows already marked `auth_status='released'` are correct and should not be reverted:
the corresponding Stripe intents really are cancelled.

## Verification performed

- **New tests:** 13, in the existing `test_stuck_ride_sweeper.py` (17 total there,
  all passing). Exercised against `mock_supabase_client`-style patched fixtures per
  CLAUDE.md gate 4, with a concrete before/after money scenario documented above.
- **Mutation-verified — five mutations, all caught:**

  | Mutation | Failing tests |
  |---|---:|
  | Never call the release (i.e. reintroduce the original bug) | 7 |
  | Drop the `auth_status` guard (would cancel a captured intent) | 3 |
  | Mark `released` even when the Stripe cancel failed | 1 |
  | Move the release *after* the notification awaits | 1 |
  | Drop the open-state filter on the bookkeeping write | 1 |

- **Failure modes covered explicitly**: Stripe returns `False`; `cancel_authorization`
  raises; the `auth_status` write fails; a mid-sweep Stripe failure on ride 2 of 3
  must not stop rides 1 and 3 (asserted by call order `["pi_1","pi_2","pi_3"]`).
- **Anti-over-reach**: 5 parametrized cases assert **no** release attempt for
  `captured` / `released` / `NULL` `auth_status` and for a missing
  `payment_intent_id`.
- **Full suite:** `pytest -m "not slow"` → **5888 passed, 8 skipped, 1 xfailed,
  1 failed** (5874 before; +14). The failure is the same pre-existing
  `test_compliance_reports.py` timestamp mismatch, proven unrelated in the T1 log.
- **Lint:** `ruff check` clean on both changed files.

## What was NOT verified

- **Never exercised against real Stripe.** `cancel_authorization` is mocked in every
  test. Whether `PaymentIntent.cancel` behaves as expected against a
  `requires_capture` intent in this account's configuration is **assumed from the
  Stripe API contract and from the fact that the interactive cancel path already
  relies on it in production** — not observed here. A staging booking that times out
  is the missing check, and it is the highest-value one before merge.
- **The historical backlog is not addressed.** This fixes the leak going forward. Any
  ride already swept with `auth_status IN ('authorized','fare_only')` and
  `status='cancelled'` still has an orphaned hold until Stripe expires it. A one-off
  reconciliation query would find them (`rides WHERE status='cancelled' AND
  auth_status IN ('authorized','fare_only') AND payment_intent_id IS NOT NULL`), but
  writing and running that against production is deliberately not bundled into this
  change. **This is the follow-up that actually returns money to riders already
  affected.**
- **No production build applies** — backend-only, no mobile/admin code touched.
- **`RIDE_AUTH_BUFFER_CAD` was read, not audited.** The $5.00 figure in the scenario
  above is illustrative; the real value comes from settings.
- **No load or latency measurement.** One extra Stripe call plus one DB write per
  swept ride, on a 60-second loop handling a handful of rides — judged negligible
  rather than measured.
- **Not verified whether the rider app surfaces anything about the released hold.**
  The assumption is that no client change is needed because the hold disappearing is
  a Stripe/bank-side event, but no rider-app code was inspected for hold-related UI.
