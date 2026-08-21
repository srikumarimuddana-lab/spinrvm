# Stripe Payout Readiness — The Headline Launch Cash-Flow Risk

**Status:** strategy/risk reference, not a runbook. This is the single
highest-priority finding from this cycle's financial-readiness research —
read this before committing to any driver bonus timeline, promo spend, or
launch-week marketing spike. Owner: finance/founder, engineering lead for
the technical mitigations in §4. Tracked as an `ACTION_ITEMS.md` entry
(see that file) because it is a real, near-term, launch-blocking-severity
risk, not a someday-nice-to-have.

## 1. The risk, stated plainly

Stripe imposes a **non-waivable first-payout delay** for new platform
accounts — commonly 7-14 days before the first payout is released — and
can hold **reserves for up to 180 days**, explicitly triggered by "a sales
spike, a promotion, or a sudden increase in disputes." A driver-acquisition
blitz (`docs/growth/driver-acquisition-strategy.md`) and any launch-week
rider promo (`docs/growth/rider-acquisition-strategy.md`) are, by
definition, exactly the kind of "sudden increase" Stripe's own terms name
as a reserve trigger.

**The failure mode this creates:** Spinr promises a driver a fast bonus
payout, or promises riders a promo, and the resulting volume spike causes
Stripe to hold funds in reserve right when the platform most needs to pay
out reliably to build early trust — the opposite of the intended launch
effect.

## 2. Why this is structurally different from a normal payments company

Spinr holds **zero float by design** (0% commission, driver keeps the fare,
no interest-earning balance sits with the platform the way it does for,
e.g., Airbnb — publicly reported at ~$700M in interest income from holding
guest payments before host payout, cited here only as a contrast, not a
model to emulate). That's a deliberate product/business choice and the
right one for Spinr's positioning — but it also means Spinr has **no
cushion of held funds** to smooth over a Stripe reserve hold. A company
that does hold float can absorb a reserve hold more easily; Spinr cannot,
because there's no float to draw from.

## 3. A cautionary precedent on payout trust (not a Stripe-specific one)

DoorDash's $16.75M New York AG settlement over a tip-offset practice is
cited here not because it's the same mechanism, but because it's a real,
recent, large precedent for how badly a *perceived* broken payout promise
damages trust with exactly the population (drivers/couriers) a launch-phase
platform most needs to keep. A payout delay explained clearly and in
advance is a manageable operational fact; a payout delay a driver
discovers by surprise reads as a broken promise, even if it's contractually
disclosed somewhere.

## 4. Mitigations to evaluate before launch

1. **Set driver-bonus and promo payout timelines against the realistic
   Stripe payout schedule, not an assumed instant one.** Cross-reference
   `docs/growth/driver-acquisition-strategy.md` §4's bonus structure —
   don't promise "bonus paid same week" if Stripe's first-payout delay
   alone could exceed that window for a brand-new platform account.
2. **Contact Stripe directly before launch** to understand Spinr's actual
   account-specific first-payout timeline and reserve policy — the 7-14
   day / 180-day figures above are Stripe's general public documentation,
   not a confirmed number for Spinr's specific account. This requires
   Stripe dashboard/account access this session does not have — flag to
   whoever owns the Stripe relationship.
3. **Model the cash-flow gap explicitly**: if Stripe holds a reserve during
   the exact week Spinr is paying acquisition bonuses and running a promo,
   does the business have runway to cover driver payouts from operating
   cash while waiting on the held Stripe balance? This is a real
   calculation to run with actual numbers, not something this document can
   answer — flag to finance/founder before committing to a blitz.
4. **Consider staggering the driver-acquisition blitz and the rider promo**
   rather than running both at full intensity simultaneously in week 1 —
   reduces the volume spike that triggers the reserve trigger language in
   §1, at the cost of slower dual-sided growth. This is a real trade-off,
   not a free mitigation — flag to launch lead as a decision, not a
   default.
5. **Reconcile daily against the existing Stripe reconcile cron**
   (`backend/utils/stripe_reconcile.py`, already running per
   `saskatoon-launch.md` §D-4) — a reserve hold should show up as a
   discrepancy pattern there before it becomes a driver-facing surprise.
   This is existing code, not new work — just make sure someone is
   actually watching that cron's output during the blitz week specifically,
   not just relying on it running silently.

## 5. What this document does NOT resolve

This document identifies and frames the risk; it does not set a specific
reserve-cushion dollar figure, does not confirm Spinr's actual Stripe
account terms, and does not make the staggering decision in §4.4. Those
are finance/founder decisions requiring real account access and real
numbers this session doesn't have.

## Sources (external research, not Spinr-verified)

Stripe Connect public documentation on new-account payout delays and
reserve policy (general terms, not Spinr-account-specific); Airbnb
interest-income public reporting (contrast only); DoorDash NY AG
$16.75M tip-offset settlement (public reporting, cited as a trust-precedent
analogy, not a claim that Spinr has a similar practice — it does not).
