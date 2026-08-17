# Spinr Public Subprocessor List — Draft for Legal Review

> **What this is.** A plain-language, public version of the internal
> `docs/vendor-register.md` and `subprocessors-baseline.json` — what a
> corporate customer's security/procurement review and a privacy-conscious
> rider or driver both expect to be able to check without signing an NDA
> first. This is also the natural place to finally disclose Google Gemini
> and LogRocket, already flagged internally as undisclosed
> (`docs/data-classification.md` item DV-16, and the LogRocket gap noted in
> the implementation-plan audit) — publishing this page and updating the
> Privacy Policy's vendor list are the same piece of work.
>
> **This is a draft, not legal advice.** Content below is derived from
> `docs/vendor-register.md` as of this review; re-verify against that file
> (and `docs/dpa-register.md` for DPA status) before publishing, since
> vendor relationships change and this page needs to stay current — treat
> it as a public mirror of the internal register, not a separately
> maintained document that can drift from it.

---

## BEGIN DRAFT

SPINR SUBPROCESSOR LIST

Last updated: [INSERT PUBLICATION DATE]

Spinr uses the following third-party service providers ("subprocessors") to
operate the platform. Each one receives only the specific data needed to
perform its function, and each is contractually required to protect it. See
our Privacy Policy for how we handle your information generally.

| Subprocessor | What it does for Spinr | Data it may process | Location |
|---|---|---|---|
| Supabase | Primary database — stores account, trip, and payment records | All app data | Canada (`ca-central-1`) |
| Stripe | Payment processing | Payment card data (Spinr never stores your full card number) | US / EU |
| Twilio | SMS delivery for one-time passcodes and notifications | Phone number | US |
| Google (Firebase) | Push notifications, app stability monitoring | Device token, ride status (no rider/driver PII in the payload) | US |
| Google Maps | Route calculation, ETA | Pickup/drop-off address needed to calculate directions | US |
| Google (Gemini) | AI-assisted in-app support and text features | Text you enter in those specific features; PII is stripped before sending | US |
| LogRocket | Session diagnostics (driver app, iOS only — currently disabled on Android) | App usage session data, used to reproduce and fix bugs | [CONFIRM REGION] |
| Railway | Backend server hosting | Request traffic passing through the backend | US |
| Fly.io | Backend server hosting (Toronto region) | Request traffic passing through the backend | Canada (`yyz`) |
| Vercel | Admin dashboard hosting (not used by the rider or driver apps) | Admin session metadata | Canada (`yyz1`) |

We do not sell your personal information to any of these providers or to
anyone else, and we do not use them to build advertising profiles.

CROSS-BORDER PROCESSING

Some subprocessors above are located outside Canada, principally in the
United States. This means your information may be processed in the United
States and could be accessible to US authorities under US law. We select
subprocessors with this in mind and require appropriate contractual
protections. Our primary database is hosted in Canada.

CHANGES TO THIS LIST

We review this list at least annually and whenever we add or remove a
subprocessor. Where required by law, we will notify you of a material
change before it takes effect.

QUESTIONS

Contact our Privacy Officer at privacy@spinr.ca with questions about a
specific subprocessor, or to request our current Data Processing Addendum
register for procurement/security review purposes.

## END DRAFT

---

## Pre-publication notes

1. **This page must stay in sync with `docs/vendor-register.md`.**
   Recommend a lightweight process (e.g. a checklist item on any PR that
   touches `docs/vendor-register.md`) so the two don't drift — publishing a
   public list only to let it go stale defeats the purpose.
2. **LogRocket's processing region is unconfirmed** in the internal register
   as of this review — resolve before publishing rather than guessing.
3. Publishing this page and disclosing Gemini/LogRocket in
   `docs/legal/privacy-policy.md`'s vendor section (§3) should happen
   together — see that file's own pre-publication note #2.
4. Once published, mark the corresponding rows "disclosed" in
   `docs/vendor-register.md`, consistent with that file's own instruction.
