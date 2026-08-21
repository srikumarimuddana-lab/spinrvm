# Driver Acquisition Strategy — Saskatoon Launch

**Status:** strategy reference, not a runbook. This document is the "how to
fill the gate" companion to `docs/runbooks/saskatoon-launch.md` §J (which
states the driver-supply minimums and eligibility requirements — read that
first). Nothing here is code-verified; owner is growth/driver-ops lead.

## 1. The gate this strategy needs to hit

Per `saskatoon-launch.md` §J-2: **5–10 active drivers in Saskatoon within
the first 2 weeks**, each meeting the full eligibility checklist in §J-1
(Class 5 licence, 3+ yr clean abstract, CRC + Vulnerable Sector Check, SGI
ride-share endorsement, vehicle < 10 yr, Stripe Connect Express onboarded).

## 2. The single largest unverified operational unknown

**SGI ride-share endorsement turnaround time and Vulnerable Sector Check
turnaround time are not confirmed with SGI or local police services.** This
gates how far in advance recruiting must start — if either takes, say, 2-3
weeks, a "blitz starting Day -3" plan is not achievable and recruiting needs
to start a month or more before launch instead.

**Action before committing to any recruiting timeline below: place a direct
call to SGI (ride-share endorsement processing) and the relevant local
police service (Vulnerable Sector Check processing) and get real turnaround
numbers.** This is the same gap flagged in `saskatoon-launch.md` §I-6 (new)
and tracked as an `ACTION_ITEMS.md` entry — do not proceed on an assumed
timeline.

## 3. Where drivers congregate (evidence, not Spinr-specific)

- r/uberdrivers (~467K members at time of research) — general rideshare
  driver community, not Saskatoon-specific, but a plausible place to run a
  geographically-targeted post or ad if the subreddit's self-promotion
  rules allow it (check rules before posting — do not spam).
- **No Saskatoon- or Regina-specific driver Facebook group was found during
  this research pass.** This reads as an open lane (no incumbent community
  to displace) rather than evidence no such group exists — worth a direct
  search closer to launch, since a live community would be a much higher-
  signal recruiting channel than a cold ad.
- Local channels not yet evaluated: Kijiji/Facebook Marketplace gig-work
  postings, SIAST/Saskatchewan Polytechnic career-services boards, taxi-
  driver associations (a pool of drivers who may already meet the
  licensing bar and are actively looking for gig alternatives).

## 4. Bonus structures — real precedent, not Spinr commitments

Uride (Thunder Bay-founded, operates in several small/mid Canadian markets
including BC) has publicly documented milestone-gated driver bonuses by
market. These are **Uride's numbers, not Spinr's** — cited as a real,
recent, same-country small-market precedent for what a milestone bonus
structure can look like:

| Market (Uride, BC) | Reported bonus structure |
|---|---|
| Victoria | $500–$1,000 |
| (Class 4 driver, after 100 rides) | $700 |
| Penticton | $750 |
| PEC | $200 |
| "All-Star" weekly guarantee | $1,500/week |

Any Spinr bonus structure needs its own numbers, tied to Spinr's actual
cash-flow plan (see `docs/finance/stripe-payout-readiness.md` — a bonus
promise Spinr can't fund on time because of Stripe's first-payout delay is
worse than no bonus at all) and reviewed against the CLAUDE.md
independent-contractor-classification guardrail: a bonus tied to ride
count/hours-online is fine; a bonus that reads as "mandatory shift
completion" risks control-of-work/misclassification exposure — see
`docs/legal/independent-contractor-agreement.md` §1.2.

## 5. Recruiting timeline (draft — adjust once §2's turnaround numbers are real)

| Phase | Activity | Depends on |
|---|---|---|
| T-30 (or earlier, pending §2) | Start CRC/VSC/SGI-endorsement paperwork for first recruiting cohort | Real turnaround numbers from §2 |
| T-14 | Local channel outreach (taxi associations, Kijiji, campus boards) begins | — |
| T-7 | First cohort's documents verified, Stripe Connect Express onboarding started | Document review capacity (human reviewer per `saskatoon-launch.md` §I-3) |
| T-3 to T0 | Final onboarding push to hit 5-10 minimum, at least 1 WAV driver confirmed | `saskatoon-launch.md` §J-2, §J-3 |
| T0–T+14 | Retention focus begins — see `docs/growth/driver-retention-strategy.md` | — |

## 6. Real-world reference: Uride's Moose Jaw soft-launch precedent

Uride reportedly soft-launched in Moose Jaw, SK with a restricted service
window (Fri/Sat 10pm–3am) before expanding hours — a precedent for
launching supply-constrained rather than promising 24/7 coverage from day
one. Worth considering if the 5-10 driver minimum is only reachable with a
restricted initial window; cross-reference against
`docs/runbooks/saskatoon-launch.md` §M (day-of launch sequence) if this
path is chosen, since it changes the "T=launch" definition of done.

## Sources (external research, not Spinr-verified)

Uride public driver-bonus figures by BC market (third-party reporting, not
independently confirmed with Uride); Uride Moose Jaw, SK soft-launch
reporting; r/uberdrivers subscriber count at time of research. None of this
is Spinr operational data.
