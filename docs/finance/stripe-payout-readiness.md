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

## 6. Concrete cash-flow model (added 2026-08-31)

**Purpose:** turn §1–5 above into an actual dollar range, using the real
figures that exist in Spinr's own launch/growth docs today, so finance has
a starting number to bring to the Stripe conversation in §4.2 rather than
an open-ended risk statement. This section extends the document; it does
not replace §1–5.

### 6.1 What's real vs. what's a modeling assumption

Spinr's own docs are honest about not having committed numbers yet
(`rider-acquisition-strategy.md` §5 explicitly declines to publish a CAC
or promo-budget figure, calling anything here "modeled, not audited").
This model respects that split — every input below is tagged:

| Input | Value used | Status |
|---|---|---|
| Active drivers, first 2 weeks | 5–10 | **Real** — `saskatoon-launch.md` D-1 / §J-2 |
| Per-driver milestone bonus | $200–$1,500 | **Real, but not Spinr's number** — Uride's published BC-market bonus range, cited in `driver-acquisition-strategy.md` §4 as the nearest same-country small-market precedent; Spinr has not set its own figure yet |
| Rider promo budget | *(none available)* | **Not modeled with a dollar figure** — `rider-acquisition-strategy.md` §5 has no committed number and explicitly says publishing a placeholder would get "quoted as fact." This model does not invent one; see §6.4 for how rider promo still factors in without a budget number |
| Rides/driver/day at launch | 10 | **Assumption** — not sourced from any Spinr doc (pricing config, S-2, is still TBD in `app_settings`). A placeholder for early-stage utilization in a 5–10-driver pilot; replace once real ride data exists |
| Average fare | $15 CAD | **Assumption** — same reason; a plausible short-urban-trip figure for Saskatoon, not a Spinr number |
| Window modeled | 14 days | **Real** — matches both Stripe's stated first-payout-delay ceiling (§1) and D-1's "first 2 weeks" driver-supply gate |

Two of six inputs (rides/day, average fare) are placeholders because
Spinr has no real ride-volume or fare data yet — S-2 pricing config isn't
finalized. **This is the same caveat the rider-acquisition doc already
states about its own CAC section: replace these two inputs with real
numbers the moment they exist (first week of real ride data), and treat
the resulting dollar ranges below as directional, not a number to build a
cash policy on unchanged.**

### 6.2 Gross Stripe-processed volume at launch scale

```
Gross 14-day rider fare volume = drivers × rides/driver/day × avg fare × 14 days
Low  (5 drivers):  5 × 10 × $15 × 14 = $10,500
High (10 drivers): 10 × 10 × $15 × 14 = $21,000
```

This is the volume Stripe's reserve-hold policy (§1) would apply against.

### 6.3 Reserve-hold exposure by scenario

Stripe doesn't publish the exact percentage it would apply to a new
account's volume spike — §1 confirms only that a spike is a named trigger,
not a rate. Modeling a range, per this task's instruction:

| Reserve % | Held on $10,500 (5 drivers) | Held on $21,000 (10 drivers) |
|---|---|---|
| 10% | $1,050 | $2,100 |
| 25% | $2,625 | $5,250 |
| 50% | $5,250 | $10,500 |

### 6.4 Driver bonus exposure (the part with a real comparable)

Bonuses are a Spinr-side cash commitment, independent of whether they're
disbursed via Stripe Connect transfer or another rail — either way they
draw on the same operating cash this document is about, and they land in
the *same* week Stripe's reserve hold would bite (D-1's first-2-weeks
window overlaps directly with the blitz).

```
Bonus exposure = drivers × per-driver bonus (Uride range)
Low  (5 drivers × $200 low-tier):    $1,000
High (10 drivers × $1,500 top-tier): $15,000
```

**Rider promo is not added as a dollar line here** because no committed
budget exists (§6.1) — but it is not cash-flow-neutral either: any
first-ride discount or flat-fare promo increases ride *count*, which
increases the gross volume in §6.2/§6.3 that the reserve percentage is
computed against. A rider promo makes the reserve-hold row worse, not the
bonus row — that's the mechanism to flag to finance, even without a
budget figure to plug in.

### 6.5 Combined cash gap Spinr would need to cover

Adding §6.3 (reserve-held fare volume) and §6.4 (driver bonus commitments)
gives the total operating-cash cushion needed to keep paying drivers on
time through a launch week where both the blitz and Stripe's first-payout
delay/reserve hold are live simultaneously:

| Scenario | Reserve-held volume | Bonus exposure | **Total cash gap** |
|---|---|---|---|
| Low (5 drivers, 10% reserve) | $1,050 | $1,000 | **≈ $2,050** |
| Mid (7–8 drivers, 25% reserve) | ≈ $3,900 | ≈ $8,000 | **≈ $11,900** |
| High (10 drivers, 50% reserve) | $10,500 | $15,000 | **≈ $25,500** |

**The headline finding:** because Saskatoon launch is deliberately a
5–10-driver, single-city pilot (not a multi-market blitz), the absolute
dollar exposure is modest — low thousands to roughly $25K in the modeled
worst case, not six figures. That is genuinely reassuring relative to how
alarming §1's "7–14 days, up to 180 days" language reads in isolation.
**It is not a reason to skip the mitigations below** — $25K uncovered on a
promise made directly to a driver in week 1 is still a real reputational
failure at exactly the wrong moment (§3), and this math scales linearly
(and non-trivially) the moment the pilot expands past 10 drivers or a
second city.

### 6.6 Mitigation options, with tradeoffs (extends §4)

1. **Stagger the driver blitz and rider promo by 1–2 weeks instead of
   running both in week 1.**
   - *Tradeoff:* slower dual-sided growth, and per §6.4 it doesn't reduce
     bonus exposure (drivers still get bonuses on their own onboarding
     schedule) — it only avoids compounding the *rider*-side volume spike
     with the driver-side one in the same reserve-hold window. Cheapest
     option (no cash held back), costs calendar time instead.
2. **Hold an explicit cash reserve sized to the modeled gap** — e.g.
   $12,000–$26,000 (mid-to-high scenario in §6.5) set aside as
   launch-week working capital before committing to any bonus timeline.
   - *Tradeoff:* ties up real operating cash that could fund something
     else pre-revenue; but it's the only option that lets the blitz and
     promo run concurrently as currently planned without depending on
     Stripe's cooperation.
3. **Request a Stripe custom account review / expedited payout terms
   before launch**, framing Spinr as a known, small, single-city entrant
   (not an anonymous new account) — per §4.2, this requires the Stripe
   relationship owner, not this session.
   - *Tradeoff:* no guarantee Stripe grants it, and it's the one
     mitigation that can't be executed from inside this repo or by
     engineering — it has to happen as an actual conversation, and should
     start now given P-5's position in the launch checklist, not the week
     of launch.

None of these are mutually exclusive — the realistic plan is likely (2)
sized against the mid scenario as a floor, combined with (1) as the
default sequencing unless (3) comes back with better terms in time.

### 6.7 What this section still does not resolve

This is a planning model built from Spinr's own real driver-count and
Uride's real comparable-market bonus figures, plus two explicitly-flagged
placeholder assumptions (rides/day, avg fare) standing in for ride-volume
and pricing data that don't exist yet. **It is not a substitute for the
actual Stripe conversation in §4.2 / `saskatoon-launch.md` P-5.** This
session has no Stripe dashboard or API access (Stripe MCP tools require
OAuth not available here) and has not obtained, and does not claim to
have obtained, Spinr-specific reserve-percentage or payout-timeline terms
from Stripe. The dollar ranges in §6.5 exist so that conversation has a
concrete number to react to ("is 25–50% realistic for our account, and is
$12–26K enough cushion") — not to close out P-5 or G1 on their own.

## Sources (external research, not Spinr-verified)

Stripe Connect public documentation on new-account payout delays and
reserve policy (general terms, not Spinr-account-specific); Airbnb
interest-income public reporting (contrast only); DoorDash NY AG
$16.75M tip-offset settlement (public reporting, cited as a trust-precedent
analogy, not a claim that Spinr has a similar practice — it does not).
