# Spinr CASL Marketing Consent Disclosure — Draft for Legal Review

> **What this is.** Canada's Anti-Spam Legislation (CASL) has its own
> disclosure and consent requirements, separate from and in addition to
> PIPEDA — the Privacy Policy draft mentions withdrawing marketing consent
> (§5) but doesn't carry CASL-specific disclosures (sender identification,
> unsubscribe mechanics, consent-type record-keeping). The product
> mechanism already exists (`backend/services/marketing_consent.py`,
> migration 190) — this document is the required customer-facing
> disclosure that sits alongside it, and the required footer content for
> every commercial electronic message Spinr sends.
>
> **This is a draft, not legal advice.** CASL penalties (Administrative
> Monetary Penalties up to $10 million per violation for organizations) are
> separate from and can exceed PIPEDA's, so this deserves its own counsel
> review, not just a rider on the Privacy Policy review.

---

## BEGIN DRAFT

### A. CASL disclosure section (for the Privacy Policy or a standalone page)

HOW WE HANDLE MARKETING MESSAGES (CANADA'S ANTI-SPAM LEGISLATION)

If you've told us it's okay, we may send you commercial electronic
messages — emails, SMS, or push notifications about promotions, new
features, or referral offers. This is separate from service messages about
your account or an active ride, which we send regardless of your marketing
preference because they're necessary to provide the Service.

WHAT COUNTS AS CONSENT

We ask for your express consent to marketing messages — for example, an
opt-in checkbox during signup or in Settings, not a pre-checked box. If you
don't opt in, we don't send marketing messages, though we may still rely on
implied consent for a limited time after you've had a business relationship
with us (for example, shortly after your last completed ride), consistent
with CASL's implied-consent rules — we still let you opt out at any time
even during that period.

WHO IS SENDING

Every marketing message we send identifies Spinr Mobility Inc. as the
sender and includes our contact information: [SPINR BUSINESS ADDRESS] and
a way to reach us, either in the message or on a linked page.

HOW TO UNSUBSCRIBE

Every marketing email includes an unsubscribe link that works immediately
and doesn't require you to log in. For SMS, reply STOP. For push
notifications, turn off marketing notifications in Settings. You can also
manage all of this in one place: Settings → Notifications → Marketing
Preferences. We process unsubscribe requests promptly and in any case within
10 business days, as CASL requires.

Opting out of marketing messages never opts you out of service messages
about your account or your rides — those aren't marketing, and we can't
turn them off without affecting your ability to use Spinr safely.

### B. Required message footer (for every commercial electronic message)

Spinr Mobility Inc. | [SPINR MAILING ADDRESS] | [PHONE OR WEBSITE
CONTACT]

You're receiving this because you opted in to marketing messages from
Spinr. [Unsubscribe] | [Manage preferences]

## END DRAFT

---

## Pre-publication notes

1. **Fill in a real mailing address and contact method** — CASL requires
   sender identification with valid contact information in every commercial
   message; a placeholder here would make every message sent before it's
   filled in non-compliant.
2. Confirm the implied-consent window described matches CASL's actual rule
   (generally 2 years from the end of a business relationship, or 6 months
   from an inquiry) with counsel before publishing a specific claim about it.
3. Cross-reference `backend/services/marketing_consent.py` and migration
   190 to confirm the described opt-in/opt-out mechanics match what's
   actually implemented — this document should describe the real system,
   not a target one.
