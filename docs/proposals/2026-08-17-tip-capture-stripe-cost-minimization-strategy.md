# Minimizing Stripe Calls on Fare + Tip Settlement — Research & Strategy

**Date:** 2026-08-17
**Status:** Analysis / proposal — no code changed by this document. One finding below
(Finding 1) reads as a live money-integrity gap, not a future optimization; flagging it up
front rather than burying it after the buffer-sizing discussion.
**Trigger:** Request to research how rideshare giants avoid a second Stripe call (~$0.30) for
post-ride tips, and produce a strategy — including edge cases and error handling — for Spinr.

---

## TL;DR

**Spinr already built the core of this.** `RIDE_AUTH_BUFFER_CAD` (`backend/core/config.py:146`)
places a manual-capture hold of `estimated_fare + $10` at booking, a 20-minute post-trip
"tip window" (`backend/utils/preauth_capture.py`) lets the rider add a tip before the hold is
auto-captured, and the whole flow — capture, overflow-tip fallback charge, hold release on
cancel, orphaned-hold sweep, stale-intent reconciliation — is implemented, tested, and closed
out in `ACTION_ITEMS.md` (A1c coverage sweep, 2026-08-02/03). This is not a greenfield build;
it is a tuning and gap-closing exercise on a system already in production.

This doc:
1. Documents what's already built, so nobody re-designs it from scratch.
2. Summarizes how Uber/Lyft and Stripe's own product surface approach the same problem.
3. Walks the edge cases against Spinr's actual code and flags **one critical gap** (a tip
   added after settlement is credited to the driver but never charged to the rider) and
   several tuning opportunities (buffer sizing, debit-card behavior, no measurement of how
   often the buffer is actually insufficient).
4. Gives prioritized, additive recommendations that fit the CLAUDE.md release-gate rules for
   a live-tested payments surface.

---

## 1. What Spinr already has (as-built)

| Piece | File | What it does |
|---|---|---|
| Buffer sizing | `core/config.py:146` | `RIDE_AUTH_BUFFER_CAD = Decimal("10.00")` — flat $10, tunable via env, no redeploy |
| Hold placement | `routes/rides/booking.py::_preauthorize_ride_card` | Manual-capture PaymentIntent for `grand_total + buffer` at booking; retries at fare-only (no buffer) if the buffered amount is declined |
| Hold at dispatch for scheduled rides | `utils/scheduled_rides.py:485-520` | Scheduled rides pre-auth **at dispatch**, not at booking — correctly sidesteps Stripe's ~7-day auth lifetime for a ride booked days out |
| Capture (single-fee path) | `services/payment_service.py::_settle_against_hold` (~1220), `utils/stripe_charge.py::capture_ride` (~703) | Captures `min(total_charge, authorized)` on the SAME PaymentIntent — one Stripe fee |
| Tip-window sweep | `utils/preauth_capture.py` | 20-minute post-completion window, then a 5-minute-cadence loop auto-captures fare+tip together if the rider never opens the settlement screen |
| Overflow tip | `payment_service.py:1298-1325` | If tip pushes the total over the authorized hold, only the **excess** is charged on a fresh PaymentIntent — never a full second charge |
| Hold release | `utils/card_hold_release.py` | Single shared definition used by cancellation, the stuck-ride sweeper, and the orphaned-hold reconciler — releases the hold the moment a ride is cancelled instead of letting it sit for 7 days |
| Orphaned-hold safety net | `utils/orphaned_hold_reconciler.py` | Sweeps holds the other two release paths missed |
| Stripe idempotency | `utils/stripe_charge.py` | Every auth/capture/charge/cancel call carries an idempotency key; capture key embeds the amount so replays dedupe safely |
| Fare-only fallback | `booking.py` (`auth_status="fare_only"`) | If the card can't cover fare+buffer, Spinr retries the hold at fare-only rather than blocking the booking |

This is a genuinely close match to what the "ride share giants" do (see §2) — manual-capture
authorization sized above the estimate, single capture at settlement, targeted top-up charge
only when the hold is exceeded.

---

## 2. Industry research

Public documentation on exact hold *amounts* used by Uber/Lyft is sparse (neither publishes the
number), but the mechanism is confirmed and matches Spinr's:

- **Uber**: "Uber may apply a temporary authorization hold for the value of the fare in advance
  ... If the amount initially authorized is greater than the final fare of your trip, your
  payment method will only be charged for the final rate and the difference that was
  pre-authorized will be canceled without being charged." ([Uber Help — "I haven't got refunded
  for estimated amount of authorization hold"](https://help.uber.com/Riders/article/i-havent-got-refunded-for-estimated-amount-of-authorization-hold?nodeId=f3ed4ee8-0baa-4dbb-9649-984eb664e5f3),
  [Uber Blog — "What is an Authorization Hold?"](https://www.uber.com/us/en/blog/what-is-an-authorization-hold/))
- **Lyft**: "A temporary authorization hold is a pre-authorization that Lyft places to verify
  your payment method, not a completed charge." ([JustAnswer summary of Lyft support
  guidance](https://www.justanswer.com/software/nd8s5-ride-12-midnight-payment-85-99.html))
- General e-commerce pattern confirmed via [Wikipedia — Authorization hold](https://en.wikipedia.org/wiki/Authorization_hold):
  holds that exceed the final charge are released, not captured then refunded — this is the
  same "capture ≤ authorized" mechanic Stripe enforces and Spinr already relies on.

Neither vendor documents whether the *tip* itself rides inside the same hold or is a
separate later charge. Given both apps let riders tip at the rating screen (same session,
before the hold would otherwise be captured) and neither discloses a second processing fee to
riders, the working assumption — and the one Spinr's implementation already encodes — is that
the tip is folded into the same capture whenever it arrives inside the settlement window, with
a separate charge only for tips added well after the fact (see Finding 1 below, which is
exactly that "well after the fact" case).

**Stripe's own newer product surface** is relevant context for where this *could* go, with
real caveats:

- **Incremental authorization** — raise the amount on an *existing, uncaptured* PaymentIntent
  without a new PaymentIntent (`stripe.PaymentIntent.increment_authorization`). Up to 10
  increments per PI. Does **not** extend the authorization's expiry — a PI expiring in 5 days
  still expires in 5 days after an increment. Available on Stripe's IC+ pricing, or by request
  to Stripe support. ([Stripe Docs — Increment an
  authorization](https://docs.stripe.com/payments/incremental-authorization))
- **Extended authorization** — capture up to 30 days after confirmation, depending on card
  brand and **merchant category eligibility**. Intended for hotels/car rentals/equipment
  rental — cases where the final amount isn't known at authorization time.
  ([PayRequest — Stripe Extended Authorizations
  guide](https://payrequest.io/blog/stripe-extended-authorizations-guide))
- **Eligibility is card-brand— and MCC-specific.** Discover explicitly lists "passenger
  transportation" as an incremental-auth-eligible category; Visa/Mastercard/Amex eligibility
  by MCC needs confirming directly with Stripe for Spinr's account (rideshare typically codes
  as MCC 4121). ([Stripe Docs — Industry metadata](https://docs.stripe.com/industry-metadata))
- Multicapture and incremental authorization are **mutually exclusive on the same PI** — once
  you partially capture with multicapture, you can't also increment.

**Verdict:** incremental/extended authorization is a real Stripe capability, but it solves a
different problem than Spinr has (final amount unknown for up to 30 days — hotels, car rental
damage). Spinr's tip window is 20 minutes; the flat-buffer + single-capture approach it already
has is the right-sized tool. Incremental auth is worth a Stripe support conversation only if
Finding 2 below (buffer scaling) turns out to need a much larger hold than is comfortable to
place up front — see §4, Recommendation 5.

---

## 3. Cost model (Stripe fixed fee ≈ $0.30 CAD/transaction, before %)

| Scenario | Stripe calls (mutating) | Notes |
|---|---|---|
| Tip within buffer, rider taps "Pay" before window closes | 1 (auth) + 1 (capture) | Auth is placed regardless at booking either way — it's not a *marginal* cost of tipping |
| Tip within buffer, rider never opens settlement screen | 1 (auth) + 1 (capture, via sweeper) | Same as above — sweeper doesn't add a call, it's the same capture that would've happened |
| Tip **exceeds** buffer | 1 (auth) + 1 (capture) + 1 (overflow charge) | The buffer-insufficient case — this is the one lever Spinr can still pull (§4 Rec. 2) |
| No hold usable (expired/declined) at settlement | 1 (auth, wasted) + 1 (failed capture) + 1 (fresh charge) | Rare — hold usually lapses only on abandoned/very-delayed rides |
| Wallet or corporate-allowance ride | 0 | Already fully outside Stripe — no hold placed at all |

The auth call is not really "for tipping" — Spinr would place it anyway to catch a dead card
before dispatch (`stripe_charge.py:477`). The **marginal** cost this whole system exists to
avoid is the third call in row 3 (overflow) and row 4 (dead-hold fallback). Both are already
minimized by design; the open question is *how often* they still fire (§4 Rec. 3 — there's
currently no metric for this).

---

## 4. Findings — edge cases and error handling, checked against actual code

### Finding 1 (P0 — money integrity, not just cost) — a tip added after settlement is never charged

`routes/rides/payments.py::add_tip` (`POST /rides/{id}/tip`) can be called on **any**
`status == completed` ride — it does not check `payment_status` at all
(`routes/rides/payments.py:74`, guard is on ride `status`, not `payment_status`). Its only
duplicate-tip guard is "has a tip already been recorded" (`routes/rides/payments.py:78-80`,
comment `R-P1-20`). When it runs, it:

- increments `tip_amount` and `driver_earnings` on the ride row (`:83-86`)
- updates the fare-breakdown/earnings snapshots that feed T4A statements (`:90-111`)
- notifies the driver they got a tip (`:131-157`)
- **makes no Stripe call and writes no `financial_events` row** — confirmed by reading the
  full function; it imports no Stripe/charge/ledger helper at all.

This is reachable in production, not just theoretically:

- `utils/driver_statement_job.py:48-54` documents it directly: *"`routes/rides/payments.add_tip`
  accepts a tip on ANY completed ride with no time window ... (Uber/Lyft weekly statements
  arrive mid-week for the same reason)"* — i.e., late tipping (days after the ride, from ride
  history) is an intentional product feature the statement job works around, not an oversight
  in that file.
- `rider-app/store/rideStore.ts:648-662` — the **offline action queue** replays a queued `tip`
  request on reconnect, with no bound on how long the device was offline. A rider who tips
  while offline immediately after a ride, then doesn't regain connectivity for 25+ minutes
  (past the tip window, `preauth_capture.py`'s `TIP_WINDOW_MINUTES = 20`), will have the tip
  applied to an **already-`paid`** ride once the queue replays.

**Net effect:** in both cases, `driver_earnings` is credited for money the rider was never
actually billed for. This is the inverse of the cost problem asked about — instead of one too
many Stripe calls, it's zero Stripe calls for a tip that should have produced one. It also
won't self-correct: the daily Stripe reconciliation (`utils/stripe_reconcile.py`) compares
`grand_total + tip` against the PaymentIntent captured **at settlement time** for
`processing`-stuck rides (`_expected_capture_cents`, `:583-597`); it has no mechanism to notice
`tip_amount` changing on a ride that is already `paid`, so this would not surface as a
reconciliation discrepancy.

**Recommendation:** treat this as its own fix, not folded into the buffer-tuning work below —
it's a correctness bug, not a cost-optimization. Two shapes, either is reasonable:
- (a) block `add_tip` once `payment_status == 'paid'`, and route "add a tip from ride history"
  through a real charge (a fresh PaymentIntent for the tip amount only — this *is* a legitimate
  case where a second Stripe fee is unavoidable, because the money must actually move); or
- (b) keep `add_tip` as the single write path but have it trigger the same fresh-charge helper
  `payment_service.py` already uses for over-buffer tips (`charge_ride` for the delta) when
  `payment_status == 'paid'`, so the driver-facing behavior (tip shows up immediately) is
  unchanged but the rider is actually billed.

This needs its own blast-radius pass before shipping (who else reads `add_tip`'s current
no-charge contract — `driver_statement_job.py`'s grace window explicitly assumes tips can land
without a corresponding settlement event, so check it doesn't assume "no charge" as load-bearing
behavior) — flagging rather than fixing inline, per this repo's escalate-when-in-doubt rule for
anything touching payments.

### Finding 2 (P1 — the actual cost-minimization lever) — flat $10 buffer doesn't scale with fare

`RIDE_AUTH_BUFFER_CAD` is a flat $10 regardless of fare size (`core/config.py:141-146`). Using
Spinr's real rate card (`services/fare_service.py:32-36`: `base_fare=$3.50`,
`per_km=$1.50`, `per_min=$0.25`, `booking_fee=$2.00`):

| Ride | Illustrative fare (pre-tax) | 20% tip | Covered by $10 buffer? |
|---|---|---|---|
| Short city hop (~5 km / 10 min) | ~$16 | ~$3.20 | Yes, comfortably |
| Typical city ride (~20 km / 25 min) | ~$42 | ~$8.40 | Yes, barely |
| Longer/XL/intercity ride (~40+ km) | ~$75+ | ~$15+ | **No** — triggers the overflow charge |
| Airport/long-distance with surge | $100+ | $20+ | **No** |

A flat buffer is sized for the median ride and systematically under-covers exactly the rides
where a real tip is most likely to be a meaningful dollar amount — the overflow-charge path
(the second Stripe fee this whole system exists to avoid) fires disproportionately on Spinr's
higher-value trips.

**Recommendation:** move to a hybrid buffer: `max($5, min(20% × estimated_fare, $30))` (numbers
illustrative — tune from real data per Rec. 3 below), replacing the flat $10. This:
- keeps small/median rides close to today's buffer (no regression for the common case)
- scales the buffer for larger fares where a proportional tip is more likely to exceed a flat
  amount
- caps the maximum hold so a rider never sees an alarmingly large "pending" charge on their
  bank statement for a routine ride (the CLAUDE.md "never over-hold relative to what's fair to
  the rider" concern the task raised) — the $30 cap number needs a product/support-cost
  decision, not an engineering guess.

This is a one-line change in shape (`RIDE_AUTH_BUFFER_CAD` becomes a function of fare rather
than a constant) but is money-facing and mid-session-visible (a rider mid-booking would see a
different hold amount), so it needs the standard gate: `app_settings`-driven so it can ship
dark and be flipped per CLAUDE.md's flag convention, plus a dry run against
`mock_supabase_client` fixtures comparing old-vs-new hold amounts across a fare distribution
before merge.

### Finding 3 (P2 — no data to tune with) — buffer-insufficient rate isn't measured

There is no metric today for "how often does the tip+fare-variance exceed the buffer." Without
it, Recommendation 2's specific numbers (20%, $30 cap) are guesses, not measurements. This
should land *before* touching the buffer formula, not after.

**Recommendation:** add `spinr_payment_hold_overflow_total` (counter, no dimensions needed
beyond maybe a fare-bucket label) incremented at `payment_service.py:1298` where the "tip
exceeded the buffer" branch fires, per the existing Prometheus naming convention
(`spinr_<domain>_<metric>_<unit>`). Let it run for 1-2 weeks before deciding the buffer formula.

### Finding 4 (P2) — debit cards hold real funds, not just "available credit"

Uber/Lyft both surface rider confusion specifically about debit-card holds (see the JustAnswer
support threads surfaced in the research above) because a debit hold reserves actual bank
balance, not a credit line, and can take longer to release than a credit-card authorization.
Spinr's current hold logic doesn't distinguish card funding type
(`stripe_charge.py::authorize_ride` treats all cards identically). This isn't a Stripe-call-count
issue, but it's directly the "if we hold more than fare it becomes an issue to the rider" risk
the task called out, and debit is where that risk is sharpest.

**Recommendation:** not urgent enough to block the buffer-formula work, but worth a follow-up:
Stripe's `charge.payment_method_details.card.funding` (`credit`/`debit`/`prepaid`) is already
available on the PaymentIntent response; consider a smaller (or zero) buffer for `debit`/
`prepaid` cards specifically, accepting a slightly higher overflow-charge rate on that subset
in exchange for not tying up a debit cardholder's real cash.

### Finding 5 (P3 — already handled, noted for completeness) — hold expiry / scheduled rides

Already correctly handled: scheduled rides pre-auth **at dispatch time**, not at booking
(`utils/scheduled_rides.py:485-491`, explicit comment citing Stripe's ~7-day auth lifetime).
The 20-minute tip window is well inside that lifetime for same-session rides. No action needed;
listed here so the eventual buffer-formula PR's blast-radius section can say "checked, this
path is unaffected" with a citation instead of a bare assertion.

### Finding 6 (P3) — three independent release paths for the same hold

`utils/card_hold_release.py`'s own docstring flags that `routes/rides/cancellation.py` has a
second, inline implementation of hold release that predates the shared module, plus the
orphaned-hold reconciler as a third backstop. Not a new finding — already documented in-repo —
but relevant here because any change to hold *sizing* (Finding 2) changes the amount every one
of these three paths releases. If Finding 2 ships, all three release paths need a regression
test confirming they release the *new* formula's amount, not just the old flat $10.

---

## 5. Recommendations, prioritized

1. **Fix Finding 1 first, on its own PR.** It's a correctness gap (driver credited for
   uncollected money), independent of everything else here, and small in scope.
2. **Ship the overflow-rate metric (Finding 3)** before touching the buffer formula — 1-2 weeks
   of real data beats guessing at 20%/$30.
3. **Then move the buffer to the hybrid percentage formula (Finding 2)**, `app_settings`-flagged,
   dark-launched, with the three hold-release paths (Finding 6) re-tested against the new
   amounts before flipping the flag.
4. **Debit/credit differentiation (Finding 4)** as a follow-up once the percentage formula is
   stable — it's a refinement on top of Rec. 3, not a prerequisite.
5. **Incremental/extended authorization (§2)** — do not pursue now. Confirm MCC eligibility
   with Stripe only if Rec. 3's data shows the percentage-buffer approach still can't keep the
   overflow rate low without holding uncomfortably large amounts up front; otherwise the added
   complexity (mutual exclusivity with multicapture, no expiry extension, per-brand MCC
   variance) isn't worth it against a 20-minute tip window.

## What this analysis did not verify

- Did not confirm against a live Stripe dashboard/account what MCC Spinr is actually registered
  under, or whether Stripe has already granted IC+ / incremental-authorization access — that's
  a support-ticket-level fact, not something derivable from the codebase.
- Finding 1's "no charge occurs" conclusion is from static reading of `add_tip` and its
  callers, not a live/staging repro — recommend a manual repro (tip a ride, force
  `payment_status` to `paid`, call `/tip`, confirm no Stripe PaymentIntent is created) before
  treating it as confirmed in a fix PR's Change Impact Log.
- Fare examples in Finding 2's table use the default rate card
  (`fare_service.py::DEFAULT_FARE`); real per-service-area pricing in the `service_areas` /
  `app_settings` tables may differ and should be pulled before finalizing the percentage/cap
  numbers.
- No production data was queried (no live-DB access in this session) — the overflow-rate metric
  in Finding 3 is a prerequisite specifically because this doc has no real numbers to offer.
