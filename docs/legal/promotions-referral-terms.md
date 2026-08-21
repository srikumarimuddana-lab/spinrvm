# Spinr Promotions & Referral Terms — Draft for Legal Review

> **What this is.** A public terms page for promo codes and the rider/driver
> referral program, closing the gap the Legal Ledger flagged: the backend
> logic already exists (`backend/utils/referral_terms.py`,
> `backend/migrations/176_service_area_referral_terms.sql`), but no
> discoverable public terms page describing eligibility, expiry, and
> anti-abuse rules was found. Canada's Competition Act expects promotional
> conditions to be disclosed at the point of the offer, not only enforced
> silently server-side. Meant to be linked from every promo/referral
> screen in-app, not just buried in the general Terms of Service.
>
> **This is a draft, not legal advice.** Specific dollar amounts, expiry
> windows, and per-user limits are placeholders pending the actual promo
> configuration — check `backend/utils/referral_terms.py` and the fraud/abuse
> guards described in CLAUDE.md's referral-velocity rules before filling
> these in, and keep this page in sync with the enforced limits so it never
> promises something the backend doesn't actually honor.

---

## BEGIN DRAFT

SPINR PROMOTIONS AND REFERRAL TERMS

Last updated: August 21, 2026

These terms apply to promo codes, referral bonuses, and other promotional
offers on the Spinr platform, in addition to the Terms of Service.

PROMO CODES

A promo code entitles the holder to the specific discount or credit stated
when the code is presented in-app. Promo codes: are valid only for the
period stated at the time of offer; may be limited to new accounts, specific
service areas, or specific ride types; cannot be combined with another promo
code unless explicitly stated; and have no cash value and cannot be
exchanged for cash.

Spinr may cancel or adjust a promo code before it is redeemed if it was
issued in error, and may decline to honor a promo code where we reasonably
believe it was obtained through fraud, abuse, or a violation of these terms.

REFERRAL PROGRAM

When you refer a new rider or driver using your referral code or link, both
you and the person you referred may receive a bonus once the referred
person completes the qualifying action stated in the app at the time (for
example, completing their first ride, or completing a minimum number of
trips as a new driver).

To qualify: the person referred must be a genuinely new Spinr user — you may
not refer an account you already control, a household member's duplicate
account, or an account created to farm referral bonuses; and the person you
referred has 30 days from applying your referral code to complete the
qualifying action (1 ride for a rider referral, 10 rides for a driver
referral) — after 30 days with no qualifying action, the referral expires
unpaid. Once the qualifying action is completed, the bonus is credited to
both accounts.

Spinr monitors referral activity for abuse, including an unusually high
rate of referral bonuses paid to the same account in a short period, and
does not count a ride toward a referral's qualifying activity if the ride
itself was free. An account found to be abusing the referral program may
have pending or already-issued bonuses reversed and may be suspended under
the Community Guidelines.

DRIVER REFERRAL BONUSES

A driver referral bonus is a promotional payment for successfully referring
a new driver who completes the stated qualifying activity — it is not part
of your fare earnings and does not affect the 100 percent fare you keep for
rides you personally complete.

CHANGES AND TERMINATION

Spinr may modify or end a promotional offer at any time, and will honor a
bonus already earned under the terms in place when you completed the
qualifying action. Spinr does not owe you a promotional bonus you have not
yet earned as of the date an offer changes or ends.

## END DRAFT

---

## Pre-publication notes

1. **Fixed 2026-08-19**: the "credited only after the qualifying action is
   verified, which may take up to [NUMBER, E.G. 7 DAYS]" placeholder
   described the wrong mechanism — there's no separate post-completion
   verification-lag constant in the codebase. The real, code-backed figure
   is `REFERRAL_WINDOW_DAYS` / `RIDER_REFERRAL_WINDOW_DAYS`
   (`backend/routes/drivers/referrals.py`, `backend/routes/users.py`) — both
   default to **30 days**, and both measure the deadline for the *referred
   person* to complete their qualifying rides before the referral expires
   unpaid, not a bonus-processing delay after completion. Also pulled in the
   real ride thresholds (rider: 1 ride, driver: 10 rides,
   `RIDER_REFERRAL_RIDES_REQUIRED` / `REFERRAL_RIDES_REQUIRED`) to replace
   the vague "first ride... minimum number of trips" phrasing. These are
   **global defaults** — `service_areas.rider_referral_window_days` /
   `driver_referral_window_days` (migration 189) and the sibling
   rides-required/reward columns (migrations 173, 201) let an admin override
   them per service area. Checked directly against the live database: every
   service area with an explicit override set (Regina, Saskatoon, their
   airport variants, and one non-SK area) matches the global default
   exactly — 30 days, 1 ride, $0 driver-referee-reward — so stating these as
   universal is accurate today. Re-check before publication if that changes.
2. Cross-reference `backend/utils/referral_terms.py` and migration 176 for
   the actual per-service-area terms already configured, and make sure this
   public page doesn't promise something broader than what's enforced.
4. **Published 2026-08-21** to `legal_documents` (rider + driver rows,
   version 1) at the explicit direction of the product owner, without
   counsel review — same accepted-risk pattern as `terms-of-service.md`/
   `privacy-policy.md`. The 30-day/1-ride/10-ride figures were not
   re-checked against the live service-area override table at publish
   time (no DB access in this session) — they rely on the 2026-08-19
   verification recorded above; re-check before relying on this note if
   meaningful time has passed.
3. **Fixed 2026-08-20** (`spinr-legal-readiness-reviewer`): the anti-abuse
   paragraph previously named device, phone-number, and payment-method
   velocity signals — none of those exist in code. The only real guard is a
   rolling 24-hour cap on referral-bonus payouts per `referrer_user_id`
   (`backend/utils/referral_payout.py:107-189`, default 5/day,
   admin-tunable via `settings.referral_payout_velocity_cap_per_day`,
   migration 336) plus a rule that a ride only counts toward the
   qualification threshold when `grand_total > 0` (blocks $0 free-ride
   farming). Reworded to describe only what's actually enforced — the
   self-referral check (`backend/routes/users.py:1011`) is unaffected and
   still accurately described above.
