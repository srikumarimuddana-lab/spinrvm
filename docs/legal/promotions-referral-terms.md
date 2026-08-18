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

Last updated: [INSERT PUBLICATION DATE]

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
account, or an account created to farm referral bonuses; and the referral
bonus is credited only after the qualifying action is verified, which may
take up to [NUMBER, E.G. 7 DAYS].

Spinr monitors referral activity for abuse, including unusual referral
velocity from a single device, phone number, or payment method. An account
found to be abusing the referral program may have pending or already-issued
bonuses reversed and may be suspended under the Community Guidelines.

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

1. Cross-reference `backend/utils/referral_terms.py` and migration 176 for
   the actual per-service-area terms already configured, and make sure this
   public page doesn't promise something broader than what's enforced.
2. The anti-abuse language here should match, not exceed, the actual
   referral-velocity and self-referral guards CLAUDE.md's fraud-auditor
   context describes — this page states policy; enforcement is a separate,
   already-built concern.
