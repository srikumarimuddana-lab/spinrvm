# Rider Acquisition Strategy — Saskatoon Launch

**Status:** strategy reference, not a runbook. This document sets direction
and cites evidence; it does not gate launch (see
`docs/runbooks/saskatoon-launch.md` for the actual launch checklist) and
nothing here is code-verified. Owner: growth/marketing lead, reviewed by
launch lead before any spend commitment.

**Scope note:** every tactic below relies only on legitimate, ToS-compliant
channels — ad-platform interest/lookalike targeting, geofenced campaigns,
public opt-in communities, and Spinr's own referral system. This document
does **not** cover, and explicitly declines to cover, scraping or
individually identifying real people's private social accounts to build a
target list — that would violate ad-platform ToS and PIPEDA, and isn't a
legitimate acquisition channel regardless of effectiveness.

---

## 1. What's already proven vs. what's speculative

| Claim | Confidence | Source |
|---|---|---|
| Referral programs are the standard rideshare rider-acquisition lever | High — near-universal industry pattern | Uber/Lyft historical referral programs (public) |
| Referral fraud is a real, not theoretical, risk at scale | High — documented incidents | 2014 Uber `uber$20FreeRide` incident (~$50k in fraudulent ride credits before being caught); a documented Tinder-adjacent farming incident |
| Local/campus-targeted launches outperform broad-market blasts for a new small-market entrant | Medium — pattern observed, not Spinr-specific | HoosierShare campus rideshare model; TouchBistro–Uber Toronto localized partnership |
| Gamified "quest" mechanics increase engagement but carry backlash risk if perceived as manipulative | Medium | Uber Quest mechanic + documented driver-side backlash coverage — cited here as a transparency warning, not an endorsement |
| CAC figures/payback modeling below | **Low — modeled, not audited.** No real Spinr spend data exists yet. Treat as a planning scaffold to replace with real numbers after the first paid campaign, not a forecast to commit budget against. |

## 2. Guardrails before any promo/referral goes live

Spinr's actual enforced anti-fraud mechanism (verified against
`docs/legal/promotions-referral-terms.md` and the underlying code) is a
rolling 24-hour / 5-referral-payout velocity cap
(`referral_payout_velocity_cap_per_day`), a $0-ride exclusion, and a
self-referral check. **There is no device- or phone-reuse fraud signal in
code** — do not describe one in marketing copy, support scripts, or future
docs; that claim was already found and corrected once in
`promotions-referral-terms.md` (2026-08-20) and must not be reintroduced.

Any new promo mechanic should be checked against this cap before launch,
not after — see `spinr-fraud-auditor` for a pre-merge review of any change
to referral/promo code.

## 3. Local/regional tactics — Saskatoon-specific

- **Whitespace identified, not yet claimed by a competitor:** event/festival
  shuttle demand (e.g. the Craven/Country Thunder SK model of a dedicated
  "SAFERIDE"-style shuttle) does not appear to have a Saskatoon-specific
  rideshare operator serving it today. This is a candidate for a
  high-visibility, low-cost local launch moment — evaluate timing against
  the actual festival calendar before committing.
- **Campus targeting:** University of Saskatchewan student population is a
  plausible early-adopter segment (mirrors the HoosierShare pattern) —
  requires a real partnership conversation (student union, campus safety
  office), not just geofenced ads.
- **Local business partnership model:** the TouchBistro–Uber Toronto
  precedent (restaurant/venue partner promotes rides to its own customers)
  is a lower-CAC alternative to paid ads — worth testing with 2-3 Saskatoon
  venues before scaling.

## 4. Promo mechanics — options, not commitments

Drawing on Uber/Lyft historical promo patterns and smaller-operator
precedents (HOVR, throo, Drivers Cooperative, Fare Co-op, Uride's 2025-26
promotions):

- First-ride discount (standard, low-risk, easy to fraud-cap)
- Time-boxed launch-week flat fare (creates urgency, easy to communicate,
  bounded cost — cap by ride count, not just dollar amount, so it can't
  run past the guardrail above)
- Referral credit (both sides) — gated by the velocity cap in §2
- **Avoid, at least at launch:** open-ended gamified "quest" ladders — the
  Uber Quest backlash precedent in §1 was specifically about riders/drivers
  feeling the mechanic was designed to be hard to actually redeem. If a
  quest mechanic is added later, the redemption math must be simple enough
  to state in one sentence in the app.

## 5. Paid acquisition — CAC framing (unaudited, replace with real data ASAP)

No real Spinr CAC data exists yet. Rather than publish a specific
speculative number here (which would get quoted as fact), the process is:

1. Run one small, geofenced test campaign (single ad platform, single
   Saskatoon neighborhood or the whole city at low daily spend) before
   committing to a channel mix.
2. Track cost-per-install → cost-per-first-ride, not just cost-per-click.
3. Compare against the referral-channel cost (credit value × redemption
   rate) as the baseline "organic" CAC — referral is usually cheaper for a
   new small-market entrant and should be the primary channel until paid
   proves itself.
4. Record the real numbers back into this file once available — this
   section should not stay speculative past the first campaign.

## 6. Week 0–6 synthesis plan (Saskatoon; portable to Regina/Moose Jaw)

| Week | Focus | Notes |
|---|---|---|
| 0 (pre-launch) | Referral program live, launch-week flat-fare promo configured and fraud-cap-tested | Do this against `mock_supabase_client` fixtures per CLAUDE.md's dry-run rule for anything touching wallet/promo deltas |
| 1–2 | Local partnership pilot (2-3 venues), campus outreach conversation started | Track redemption vs. the velocity cap — first real signal on whether the cap is too tight/loose |
| 3–4 | First small paid test campaign, single channel | Compare against referral-channel cost per §5 |
| 5–6 | Retro: real CAC numbers replace the modeled placeholder in §5; decide whether to scale paid, local, or referral further | Feed findings back into `ACTION_ITEMS.md` if a gap is found (e.g. fraud cap needs adjustment) |

## 7. What this document does NOT cover

- Driver-side acquisition — see `docs/growth/driver-acquisition-strategy.md`
- Corporate/B2B account sales — see `docs/CORPORATE_B2B_GTM.md`
- The underlying promo/referral fraud mechanism itself — see
  `docs/legal/promotions-referral-terms.md` (source of truth for what's
  actually enforced in code)

## Sources (external research, not Spinr-verified)

Uber/Lyft historical referral and promo programs (public); 2014 Uber
`uber$20FreeRide` credit-clawback incident coverage; HOVR, throo, Drivers
Cooperative, Fare Co-op, and Uride 2025-26 promotional patterns; HoosierShare
campus rideshare model; TouchBistro–Uber Toronto partnership coverage;
Craven/Country Thunder SK SAFERIDE shuttle precedent. All figures and
program details are third-party public reporting gathered via web research
this cycle — not independently audited against primary sources, and none of
it is Spinr operational data.
