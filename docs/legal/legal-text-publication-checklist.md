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
| `terms-of-service.md` | ☒ Counsel review — **NOT done; published without it** (see note below) · ☑ Audience-split routing built (Phase 1, `legal.tsx` reads the `/legal-documents` endpoint) · ☑ **Published live to `legal_documents` (rider/tos + driver/tos rows, version 1, 2026-08-17)** | **Published (with open gap — see note)** |
| `privacy-policy.md` | ☒ Counsel review — **NOT done; published without it** (see note below) · ☑ Data residency: Supabase region confirmed `ca-central-1` via live API + Fly env confirmed (2026-08-17) · ☐ still open: signed Supabase DPA, Railway env unverified from repo (`reports/legal/supabase-region-attestation-checklist.md`) · ☐ Gemini + LogRocket disclosed (blocks on `subprocessor-list.md` below) · ☐ GPS retention figure reconciled in `docs/data-classification.md` (2yr vs. 3yr contradiction) · ☐ **30-day deletion enforcement job NOT built (DV-8) — policy text promises a 30-day removal the backend does not yet perform** · ☐ **`accessibility@spinr.ca` NOT provisioned as of 2026-08-17 — policy text references this inbox as live; no email/domain admin tool available to this session, requires a human with Zoho Mail/Workspace admin access** · ☑ **Published live to `legal_documents` (rider/privacy + driver/privacy rows, version 1, 2026-08-17)** | **Published (with open gaps — see note)** |
| `community-guidelines.md` | ☐ Counsel review · ☑ Consistency check against `non-discrimination-policy.md` and `driver-deactivation-appeals-policy.md` (2026-08-19: reviewed all three — consistent on what counts as a "serious violation": safety, harassment, discrimination, fraud) · ☐ Safety-hold max-duration figure — no SLA constant found anywhere in `backend/` (see `driver-deactivation-appeals-policy.md` row, same gap) | Draft |
| `non-discrimination-policy.md` | ☐ Counsel review · ☐ Protected-grounds list verified against current SK Human Rights Code text · ☑ WAV-availability language verified against dispatch code (2026-08-20, `spinr-legal-readiness-reviewer`) — `backend/routes/rides/estimates.py` computes `wav_available` per vehicle type from nearby drivers and returns it in the fare-estimate response before booking, matching the draft's claim exactly | Draft |
| `driver-deactivation-appeals-policy.md` | ☐ Counsel review · ☑ In-app appeal channel built (2026-08-19: confirmed real and wired — `driver-app/app/appeal.tsx` → `backend/services/driver_appeals.py` → `routes/drivers/appeals.py`/`routes/admin/driver_appeals.py`; 2026-08-20: appeal-channel placeholder resolved to the in-app screen only — the `driver-appeals@spinr.ca` email alternative it previously offered doesn't exist in code) · ☐ Real SLA timeframes from safety team — still open; checked `appeal.tsx` and backend for any committed number and found none (`appeal.tsx` only says "We'll review it and get back to you," no timeframe) — this is an undecided business fact shared with `community-guidelines.md`'s safety-hold row below (one safety-team decision, two documents waiting on it) | Draft |
| `accessibility-statement.md` | ☐ Counsel review · ☐ `accessibility@spinr.ca` live and monitored (still "to be provisioned" as of 2026-08-17; requires human Zoho Mail/Workspace admin access) · ☑ "What we've built so far" verified against `docs/ACCESSIBILITY.md` (2026-08-19: filled in from the 2026-04-09 compliance status — admin dashboard automated checks at 0 critical violations, mobile screen-reader labeling, WAV matching, mandatory service-animal accommodation; explicitly did NOT claim anything for rider app/driver app/website since those are marked "Not yet audited" — named as a limitation instead) | Draft |
| `insurance-coverage-periods.md` | ☐ Counsel review · ☑ Consistency check against ToS §6/§13 (resolved 2026-08-18 — see the document's own pre-publication notes) · ☐ **In-app Safety Center entry point not built on either surface (2026-08-20, `spinr-legal-readiness-reviewer`)** — the document's own header claims it's "reachable from the in-app Safety Center," but `rider-app/app/safety-hub.tsx` has only two rows (Emergency Contacts, Report a Safety Issue) and no link to this content; `driver-app/` has no Safety Center screen at all. This is a build gap, not a content-accuracy problem — the document's prose is verified correct, it just isn't linked from anywhere yet. Needs a product/eng decision on where this page lives (in-app screen, linked web page, or folded into the ToS view) before counsel review is scheduled, since that affects what counsel is reviewing | Draft |
| `cancellation-fee-policy.md` | ☐ Counsel review · ☑ Fee split pulled from real config (2026-08-19: was factually wrong — claimed Spinr keeps no part of the fee; corrected to the real $4.00 driver / $0.50 admin default split from `schemas.py`/`routes/admin/settings.py`) · ☑ Cancel grace period + no-show wait pulled from real config (2026-08-20, `spinr-legal-readiness-reviewer`: 120s/2min and 300s/5min defaults, `services/cancellation_service.py`/`routes/drivers/ride_cancel.py`, both admin/service-area configurable) · ☐ Dispute window still genuinely unverified — `routes/disputes.py` has no time-based cutoff at all, not just an unfound constant; needs a product/fare-config decision or the backend needs to actually enforce a window | Draft |
| `promotions-referral-terms.md` | ☐ Counsel review · ☑ Terms cross-checked against `backend/utils/referral_terms.py`, `routes/drivers/referrals.py`, `routes/users.py`, and migration 176 (2026-08-19: real numbers pulled in — 30-day completion window and 1/10-ride thresholds for rider/driver referrals — replacing a placeholder that described the wrong mechanism; verified no live service-area override diverges from the global default as of 2026-08-19 — this is DB state and should be re-checked immediately before actual publication, not relied on from this date) · ☑ Anti-abuse paragraph corrected (2026-08-20, `spinr-legal-readiness-reviewer`) — previously named device/phone/payment-method velocity signals that don't exist in code; reworded to describe the real guards (`referral_payout.py`'s per-referrer 24h payout cap + zero-fare-ride exclusion) | Draft |
| `background-check-consent.md` | ☐ Counsel review · ☐ Real CRC/VSC vendor name filled in — still a placeholder; checked `driver-app/app/crc-consent.tsx` and the backend, no vendor name exists anywhere in the codebase, this is an undecided business fact · ☑ Consent-capture screen built in driver onboarding (2026-08-19: confirmed — `driver-app/app/crc-consent.tsx` is real; 2026-08-20 nuance: the screen is not currently gated into onboarding completion — reachable only from `driver-app/app/driver/settings.tsx` — so the draft's "presented during onboarding" framing overstates when it's actually captured) · ☐ Retention figure — **correction 2026-08-20**: this row previously implied Privacy Policy Part B §11 already contains the CRC/VSC retention number; it does not — that section's "7 years" figures are for the insurance-period log and tax records, not background-check results. No authoritative source exists for this figure yet; needs a Legal/Safety decision · ☐ Adverse-eligibility-decision process ("driver gets a chance to respond before action") — no CRC/VSC-specific step found beyond the general appeals flow; needs safety/eligibility team confirmation | Draft |

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
| `casl-marketing-consent-disclosure.md` | ☐ Counsel review · ☐ Real mailing address/contact filled in — mechanism to render it already exists, only the value is missing · ☑ Mechanics matched to `backend/services/marketing_consent.py` (2026-08-20, `spinr-legal-readiness-reviewer`: opt-in default-off, one-click unsubscribe, SMS STOP, and the mandatory sender/address footer all confirmed accurate) · ☑ **Implied-consent claim corrected (2026-08-20)** — the draft previously claimed Spinr relies on CASL implied consent for a limited window; no such mechanism exists in code (`marketing_consent.py` is pure explicit-opt-in by design, migration 190). Rewrote the section to describe the real system; no counsel review of an implied-consent window is needed unless that mechanism is later built | Draft |
| `breach-notification-letter-template.md` | ☐ Not for public publication — internal template only. ☐ Reviewed by counsel once, kept current with `docs/runbooks/data-breach.md` · ☑ **Contact-email drift fixed (2026-08-20, `spinr-legal-readiness-reviewer`)** — the runbook's inline §4c template told users to contact `privacy@spinr.ca` while this standalone template used `security@spinr.ca`; aligned the runbook to `security@spinr.ca` per `privacy-policy.md`'s own convention (that address is specifically for incident reports) | Internal template |

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
2. **`terms-of-service.md` and `privacy-policy.md` were published live on
   2026-08-17** (audience-split rider/driver rows inserted into
   `legal_documents`, version 1) at the explicit direction of the product
   owner, who was told beforehand — and accepted — that this ships **without
   counsel review** and that `privacy-policy.md` specifically still promises
   two things the backend does not yet do: automated 30-day deletion
   enforcement (DV-8) and a live `accessibility@spinr.ca` inbox. This is a
   known, accepted gap, not an oversight — closing it (get counsel review,
   build DV-8, provision the inbox) is tracked as follow-up work, not a
   blocker that was missed.
3. When a gating condition closes, update this table in the same PR that
   closes it, so this checklist never claims a document is more ready than
   it is.
