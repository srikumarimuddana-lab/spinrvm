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
don't opt in, we don't send marketing messages. Spinr does not rely on
CASL's implied-consent basis for any marketing message — every marketing
message you receive is because you actively opted in, and you can opt out
at any time.

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
   filled in non-compliant. Still open — needs a named business owner to
   supply the real address; the rendering mechanism already exists
   (`backend/utils/marketing_email.py`'s footer builder) and requires only
   the address value, not new code.
2. **Fixed 2026-08-20** (`spinr-legal-readiness-reviewer`): this section
   previously claimed Spinr "may still rely on implied consent for a
   limited time after you've had a business relationship," describing a
   CASL implied-consent mechanism. That mechanism does not exist in code —
   `backend/services/marketing_consent.py`'s `is_eligible()` requires an
   explicit `opted_in=true` row on every call with no time-decay or
   business-relationship fallback, and migration 190's own comment states
   this is deliberate ("DEFAULT false — never send marketing until the user
   actively opts in (CASL: implied consent is not the default)"). Rewrote
   the section to describe the actual pure-opt-in system instead of a
   consent basis Spinr doesn't rely on. If an implied-consent fallback is
   ever built, this section needs to be rewritten again to match — and
   counsel would need to confirm the window length (generally 2 years from
   the end of a business relationship, or 6 months from an inquiry) at that
   time, not before.
3. **Fixed 2026-08-20**: cross-checked the remaining mechanics against
   `backend/services/marketing_consent.py` and migration 190 — all confirmed
   accurate: opt-in defaults false per channel; unsubscribe is processed
   synchronously via a one-click link (`backend/routes/marketing.py`'s
   `/marketing/unsubscribe`, RFC 8058), not on a multi-day delay as this
   draft's "within 10 business days" phrasing implies (that language is now
   a floor, not the actual mechanism — accurate but understates how fast it
   actually is); SMS STOP is handled (`backend/routes/webhooks.py`); and the
   sender-identification/address footer is rendered unconditionally on
   every marketing send (`backend/utils/marketing_email.py`,
   `routes/admin/messaging.py` confirms it's non-optional).
