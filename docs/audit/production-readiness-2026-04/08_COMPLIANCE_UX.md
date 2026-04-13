# 08 — Compliance, Business, Legal & UX

> **Read time:** ~15 min
> **Audience:** Product, Legal/Compliance, UX lead
> **Jurisdiction:** Primary = Canada (Saskatchewan). Secondary = rest of Canada. No US launch implied.

---

## Executive verdict

This is the **weakest pillar**. Engineering controls are there, but the organizational artifacts (DPA, retention policy, ToS acceptance audit trail, PIPEDA registry, driver-classification posture) are either missing or undocumented. **Compliance debt is a launch blocker in Canada**, especially in Quebec (Law 25 / Bill 64) and if TNC municipal bylaws apply.

---

## P0 findings

### P0-C1 — No data-retention policy

**Evidence:** No `PRIVACY.md`, no `DATA_RETENTION.md`, schema has no TTL columns.

**Impact:**
- PIPEDA Principle 5 ("Limiting Use, Disclosure, and Retention") violated.
- Law 25 (QC) requires destruction/anonymization when purpose fulfilled.
- Driver government-ID docs retained indefinitely = unbounded liability.

**Fix (M):** Publish policy:
| Data class | Retention | Destruction method |
|---|---|---|
| Ride records (billed) | 7 yrs (tax) | anonymize `user_id`/`driver_id` → archive |
| GPS breadcrumbs | 90 d | drop partition |
| Chat messages | 180 d | delete |
| Deactivated user PII | 30 d | hard-delete, keep hash for fraud dedup |
| Document images | 30 d after approval or rejection | delete from Supabase Storage |
| OTP records | 24 h | delete |
| Session tokens | access 15m / refresh 30d | revoke + delete |
| Support tickets | 3 yrs | anonymize |

Implement as nightly cron in the worker process. Log every deletion event.

---

### P0-C2 — No documented PIPEDA compliance posture

**Fix (M):** Create `docs/compliance/PIPEDA.md`:
- Designated Privacy Officer + contact.
- Register of purposes for collection (per field).
- Consent mechanism (click-through at sign-up; store in `consents` table: `user_id`, `consent_type`, `version`, `accepted_at`, `ip`, `ua`).
- Access/correction/deletion flow (self-service + email fallback).
- Breach-notification runbook (Commissioner + affected users).
- Quarterly PIA (Privacy Impact Assessment) review.

---

### P0-C3 — No Terms of Service acceptance audit trail

**Evidence:** `backend/routes/users.py` likely stores user profile but no `accepted_tos_version` column observed.

**Fix (S):**
```sql
ALTER TABLE users ADD COLUMN accepted_tos_version TEXT;
ALTER TABLE users ADD COLUMN accepted_tos_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN accepted_privacy_at TIMESTAMPTZ;
```
- Version every ToS/Privacy document (`v1`, `v2`…).
- On version bump, block app entry until re-accepted.

---

### P0-C4 — Driver classification (employee vs contractor) not documented

**Evidence:** Driver onboarding includes documents but no driver agreement, no T4A/T5018 tax-form collection noted.

**Impact:** CRA / provincial ministries may reclassify drivers as employees. Independent-contractor (IC) status needs explicit indicia: no set hours, driver owns vehicle, driver sets own expenses. Also need T4A slip generation at year-end if earnings > $500.

**Fix (M):**
- Draft driver agreement explicitly stating IC relationship (reviewed by employment counsel).
- Collect SIN/business number during onboarding (stored encrypted).
- Year-end T4A issuance flow: `utils/tax_slips.py` + manual compliance review.
- Publish `docs/compliance/DRIVER_CLASSIFICATION.md`.

---

### P0-C5 — PCI scope undefined

**Evidence:** Stripe handles card data (good — keeps us at SAQ-A). But:
- Admin dashboard + backend must never touch raw PAN.
- No attestation in repo.

**Fix (S):**
- Publish `docs/compliance/PCI.md`: "We use Stripe Elements / PaymentSheet. No PAN touches our infra. SAQ-A applies."
- Confirm `@stripe/stripe-react-native` is using `confirmPaymentSheet` / `confirmPayment` without intermediate server-side card collection.
- Document: do NOT add fields like `card_last4` to our DB beyond what Stripe returns via PaymentMethod object.

---

## P1 findings

### P1-C6 — No GDPR / Law-25 style data-subject request flow

**Fix (M):**
- Admin "Export user data" button → generates `.zip` of all rows + documents keyed on `user_id`.
- Admin "Delete user" button → orchestrates hard-delete across tables (respecting financial retention).
- SLA: 30 days to fulfil (Law 25 / GDPR standard).

---

### P1-C7 — Safety features incomplete

**Evidence:** Rider app has `report-safety.tsx` and `emergency-contacts.tsx` screens — good.

**Gaps:**
- No "Share ride status with trusted contact" link.
- No in-ride 911 button with GPS-drop SMS to contact (regulatory expectation in many TNC bylaws).
- No driver background-check re-verification schedule.
- No driver dash-cam policy (data retention/consent — especially for audio).

**Fix (M):**
- Ship "Share ride" deep link.
- Add 911 button on driver AND rider home-to-destination screen; tap sends SMS w/ GPS to emergency contact AND support team.
- Document background-check cadence (e.g., annual).

---

### P1-C8 — Dispute process UX is thin

**Evidence:** `disputes.py` accepts a dispute. No SLA posted, no "status" visibility to rider.

**Fix (S):**
- Publish dispute SLA in the app (e.g., "We'll respond within 72h").
- Admin dashboard shows dispute age + required response time.
- Email templates for acknowledgment, resolution.

---

### P1-C9 — Pricing transparency

**Evidence:** Fare estimate probably shows a single number. Surge multiplier may not be disclosed.

**Impact:** Canadian consumer protection rules require transparent dynamic pricing. Saskatchewan Consumer Protection Act requires upfront price disclosure.

**Fix (S):**
- Display base fare, distance fare, time fare, surge multiplier, taxes breakdown.
- Show tipped amount separately.
- Add "Price is 1.5× because demand is high" explainer when surge active.

---

### P1-C10 — Accessibility for riders with disabilities

**Fix (M):**
- Wheelchair-accessible vehicle flag (WAV). Add as ride-type.
- Voice-over tested paths (see 04-F4).
- Text-size responsiveness.
- Service-animal acceptance policy in driver agreement.

---

### P1-C11 — Cancellation policy ambiguity

**Fix (S):** Publish in-app:
- Free cancellation window (e.g., 2 min).
- Cancellation fee thereafter.
- Driver no-show refund.
- Auto-refund to card vs. app credit.

---

### P1-C12 — Content moderation for chat

**Evidence:** Rider↔driver chat exists (`chat-driver.tsx`).

**Impact:** Harassment, PII exchange, off-platform payments.

**Fix (M):**
- Profanity filter (basic blocklist).
- Flag phone-number/URL patterns → warn user.
- Admin moderation queue for reports.
- Delete chat log 180 days after ride end (retention policy).

---

## P2 findings

### P2-C13 — Accessibility statement

Publish `docs/accessibility.md` with conformance target (WCAG 2.1 AA).

### P2-C14 — Cookie banner on admin

Admin dashboard likely sets auth cookie — if any analytics cookies, need consent banner (Law 25).

### P2-C15 — Mobile data collection disclosure (Apple/Google)

App Store Privacy Nutrition Labels + Google Play Data Safety form must be accurate. Audit.

### P2-C16 — Age gating

Drivers 18+ (or provincial minimum). Riders ≥13 (typical TNC policy). Collect DOB during onboarding.

### P2-C17 — Insurance disclosure

Rider-facing "coverage while on a Spinr trip" disclosure. Driver commercial-insurance attestation.

---

## P3 findings

- **C18** — Open-source license compliance (NOTICE.md for third-party libs).
- **C19** — Accessibility beyond minimum (dyslexia-friendly font option).
- **C20** — Sustainability reporting (EV ride share, carbon accounting).

---

## UX-focused findings (cross-cutting)

| Screen | Issue | Fix |
|---|---|---|
| Rider `ride-options` | No ETA certainty indicator | Show confidence interval |
| Rider `payment-confirm` | Post-surge price change may surprise | Re-confirm if >10% drift |
| Driver accept flow | No "why was I offered this ride?" | Show pickup distance + expected earnings |
| Driver earnings | No CSV export | Ship weekly summary email + export |
| Admin dashboard | No dark mode | Optional, quality-of-life |
| Admin disputes | No bulk-resolve | Add after moderation queue |

---

## Priority summary

| ID | Severity | Effort |
|---|---|---|
| C1 retention | P0 | M |
| C2 PIPEDA doc | P0 | M |
| C3 ToS audit | P0 | S |
| C4 driver class | P0 | M |
| C5 PCI scope | P0 | S |
| C6 DSR flow | P1 | M |
| C7 safety | P1 | M |
| C8 dispute UX | P1 | S |
| C9 price transparency | P1 | S |
| C10 a11y ride | P1 | M |
| C11 cancellation | P1 | S |
| C12 chat moderation | P1 | M |
| C13–C17 | P2 | S–M |
| C18–C20 | P3 | S–M |

---

*Continue to → [09_ROADMAP_CHECKLIST.md](./09_ROADMAP_CHECKLIST.md)*
