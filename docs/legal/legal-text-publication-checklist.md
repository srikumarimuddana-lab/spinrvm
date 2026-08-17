# Spinr Legal Documentation — Publication Checklist

> **What this is.** Every draft under `docs/legal/` lists its own
> pre-publication conditions in its file header — this checklist pulls them
> into one tracked place so publication happens deliberately, once, instead
> of someone re-deriving the conditions from twenty different file headers.
> Recommended owner: Legal + Engineering jointly. Update this table as items
> close — don't let it go stale the way `.claude/context/sprint-current.md`
> did (see the startup-hook staleness warning this repo already surfaces).

---

## How to use this checklist

A document in `docs/legal/` moves from **Draft** → **Ready for counsel
review** → **Published** only when every row in its section below is
checked. Do not publish a document with an open row — a false factual claim
in a published legal document is worse than an honest delay.

## Documents and their gating conditions

### Rider & driver-facing

| Document | Gating conditions | Status |
|---|---|---|
| `terms-of-service.md` | ☐ Counsel review (SK/Canada licensed) · ☐ Audience-split routing built so drivers see Part A+B and riders see Part A only (currently both read one shared textarea) | Draft |
| `privacy-policy.md` | ☐ Counsel review · ☐ Data residency attestation closed (`reports/legal/supabase-region-attestation-checklist.md`) · ☐ Gemini + LogRocket disclosed (blocks on `subprocessor-list.md` below) · ☐ GPS retention figure reconciled in `docs/data-classification.md` (2yr vs. 3yr contradiction) · ☐ 30-day deletion enforcement job built (DV-8) · ☐ `accessibility@spinr.ca` live | Draft |
| `community-guidelines.md` | ☐ Counsel review · ☐ Consistency check against `non-discrimination-policy.md` and `driver-deactivation-appeals-policy.md` | Draft |
| `non-discrimination-policy.md` | ☐ Counsel review · ☐ Protected-grounds list verified against current SK Human Rights Code text | Draft |
| `driver-deactivation-appeals-policy.md` | ☐ Counsel review · ☐ Real SLA timeframes from safety team (bracketed placeholders filled) · ☐ In-app appeal channel built | Draft |
| `accessibility-statement.md` | ☐ Counsel review · ☐ `accessibility@spinr.ca` live and monitored · ☐ "What we've built so far" verified against `docs/ACCESSIBILITY.md`, not aspirational | Draft |
| `insurance-coverage-periods.md` | ☐ Counsel review · ☐ Consistency check against ToS §6/§13 | Draft |
| `cancellation-fee-policy.md` | ☐ Counsel review · ☐ Dollar amounts/time windows pulled from actual `services/fare_service.py` config | Draft |
| `promotions-referral-terms.md` | ☐ Counsel review · ☐ Terms cross-checked against `backend/utils/referral_terms.py` and migration 176 | Draft |
| `background-check-consent.md` | ☐ Counsel review · ☐ Real CRC/VSC vendor name filled in · ☐ Consent-capture screen built in driver onboarding | Draft |

### Corporate / B2B

| Document | Gating conditions | Status |
|---|---|---|
| `corporate-master-services-agreement.md` | ☐ Counsel review · ☐ Every bracketed commercial term resolved (notice periods, termination-for-convenience, arbitration vs. litigation, insurance limit) · ☐ Executed together with the DPA below | Draft |
| `corporate-data-processing-addendum.md` | ☐ Counsel review · ☐ Data residency attestation closed (same gate as `privacy-policy.md`) · ☐ Section 4.2 security measures filled in with real detail | Draft |

### Driver contract

| Document | Gating conditions | Status |
|---|---|---|
| `independent-contractor-agreement.md` | ☐ Counsel review, specifically against current CRA worker-classification guidance · ☐ Every bracketed field resolved · ☐ E-signature capture flow built at onboarding · ☐ Arbitration-clause decision made deliberately, not defaulted | Draft |

### Privacy & data infrastructure

| Document | Gating conditions | Status |
|---|---|---|
| `subprocessor-list.md` | ☐ Counsel review · ☐ LogRocket processing region confirmed · ☐ Published together with the Gemini/LogRocket disclosure in `privacy-policy.md` §3 | Draft |
| `cookie-policy.md` | ☐ Counsel review · ☐ Cookie-consent banner built on the website, or the "Your Choices" section rewritten to match actual (browser-only) control | Draft |
| `data-retention-schedule.md` | ☐ **Do not publish before `privacy-policy.md`'s gating conditions close** — this table repeats the same promises in a more citable form | Draft |
| `casl-marketing-consent-disclosure.md` | ☐ Counsel review · ☐ Real mailing address/contact filled in · ☐ Implied-consent window confirmed against current CASL rule · ☐ Mechanics matched to `backend/services/marketing_consent.py` | Draft |
| `breach-notification-letter-template.md` | ☐ Not for public publication — internal template only. ☐ Reviewed by counsel once, kept current with `docs/runbooks/data-breach.md` | Internal template |

### Website-specific

| Document | Gating conditions | Status |
|---|---|---|
| `website-terms-of-use.md` | ☐ Counsel review · ☐ Confirm which repo/host actually serves spinr.ca and mirror there | Draft |
| `trademark-copyright-notice.md` | ☐ Counsel review · ☐ Confirm actual CIPO trademark registration status before using ® vs. ™ | Draft |
| `careers-privacy-notice.md` | ☐ Only needed once a careers/application page exists · ☐ Real ATS/vendor name filled in or clause removed | Draft |

## Process notes

1. **One document publishing does not mean related documents are ready.**
   `community-guidelines.md`, `non-discrimination-policy.md`, and
   `driver-deactivation-appeals-policy.md` describe the same account-standing
   process from different angles — publish them together, not staggered.
2. **The single highest-priority row in this table is `terms-of-service.md`
   and `privacy-policy.md`.** Per the Spinr Legal Ledger review, Spinr is in
   live testing with real users and neither appears to be actually published
   yet — every other document in this checklist assumes those two are fixed
   first.
3. When a gating condition closes, update this table in the same PR that
   closes it, so this checklist never claims a document is more ready than
   it is.
