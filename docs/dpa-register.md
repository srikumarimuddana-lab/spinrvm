# Data Processing Agreement (DPA) Register

**Purpose:** Track every signed DPA + its key terms. Required by PIPEDA Principle
4.1.3 (accountability for data transferred to third parties), GDPR Art. 28
(where applicable to EU users), and SOC2 CC9.2.

**Owner:** `legal` + `compliance` · **Review cadence:** annual + on contract
renewal + on any vendor sub-processor change notification.

---

## Why This Matters

Under PIPEDA, Spinr remains responsible for personal information transferred to
third parties for processing. A signed DPA is the primary evidence that Spinr
has taken reasonable steps to protect that data.

If Spinr cannot produce a current DPA for any vendor listed in
`docs/vendor-inventory.md`, that vendor relationship is a **compliance finding**
and the vendor must be moved to "pending remediation" status.

---

## Register

| Vendor | DPA signed | Effective | Renewal | Spinr signatory | Vendor signatory | Key terms | Document |
|---|:-:|---|---|---|---|---|---|
| **Supabase** | ⚠ VERIFY | — | — | — | — | Data-in-region, sub-processor list, breach 72h | `reports/legal/dpa-supabase-YYYY.pdf` |
| **Stripe** | ✅ (standard) | on account activation | auto | (auto-acceptance on signup) | Stripe, Inc. | SCCs, breach 24h, sub-processors published | [stripe.com/legal/dpa](https://stripe.com/legal/dpa) |
| **Google (Firebase + Maps + Gemini)** | ✅ (Google Cloud DPA) | on GCP account creation | auto | `infra` lead | Google LLC | SCCs + Supp. Measures, sub-processors published, breach notification | [cloud.google.com/terms/data-processing-addendum](https://cloud.google.com/terms/data-processing-addendum) |
| **Twilio** | ⚠ VERIFY | — | — | — | Twilio Inc. | SCCs, sub-processor list | `reports/legal/dpa-twilio-YYYY.pdf` |
| **Railway** | ⚠ VERIFY | — | — | — | Railway Corp. | Check default Terms include DPA | `reports/legal/dpa-railway-YYYY.pdf` |
| **Vercel** | ✅ (standard) | auto | auto | — | Vercel Inc. | Standard DPA | [vercel.com/legal/dpa](https://vercel.com/legal/dpa) |
| **Expo (EAS)** | ⚠ VERIFY | — | — | — | Expo | Developer data DPA | `reports/legal/dpa-expo-YYYY.pdf` |
| **GitHub** | ✅ (GitHub Customer DPA) | — | auto | — | GitHub Inc. | Standard | [github.com/customer-terms](https://github.com/customer-terms) |
| **Apple (App Store Connect + APNs)** | ✅ (Apple Developer Program) | account activation | annual | (auto) | Apple Inc. | Developer terms | — |
| **Google (Play Console)** | ✅ (Google Play Developer DPA) | account activation | annual | (auto) | Google LLC | Developer terms | — |
| **Upstash / Redis Cloud** | ⚠ VERIFY | — | — | — | — | Region + encryption at rest + breach | `reports/legal/dpa-redis-YYYY.pdf` |
| **Sentry** (if in use) | Required on onboarding | — | — | — | Sentry Inc. | Standard | — |
| **Email provider** (TBD) | Required | — | — | — | — | Standard + CASL | — |
| **PagerDuty / OpsGenie** (TBD) | Required | — | — | — | — | Standard | — |
| **Status page provider** (TBD) | Required | — | — | — | — | Standard | — |

---

## Sub-Processor Monitoring

Many vendors use their own sub-processors. Spinr must:

1. **Subscribe to update feeds:** Each vendor's sub-processor list page should
   be monitored. Set up a monthly check (or webhook where offered).
2. **Review on change:** When a vendor adds / removes a sub-processor, the
   privacy policy is updated within 30 days.
3. **Document here:** Below is the current list of vendor sub-processors.

### Current Vendor Sub-Processor Tree (authoritative as of 2026-04-24)

| Primary vendor | Sub-processors (as disclosed) | Region |
|---|---|---|
| Supabase | AWS (DB host), Twilio (email), Mailchimp (marketing — not in Spinr path) | US / CA |
| Stripe | AWS, Google Cloud, Datadog, Sumo Logic | US / EU |
| Google | No further sub-processors for core GCP services | Global |
| Twilio | AWS, Google Cloud (carrier routing via regional partners) | Global |
| Firebase | Google Cloud, Apple (APNs) | US |

**Monitoring:** `compliance` runs `grep` on each vendor's public sub-processor
page quarterly and logs any change to this file.

---

## Breach Notification Chain

When a vendor notifies Spinr of a breach affecting Spinr data:

```
Vendor notification
      ↓ (within hours of vendor's discovery)
security@spinr.ca (24/7 monitored)
      ↓ (within 1h of receipt)
Incident Commander (from on-call rotation)
      ↓ (within 4h)
Legal + Compliance + CEO notified
      ↓
Triage: determine whether breach is reportable to OPC (PIPEDA 72h rule)
      ↓ if YES
Draft OPC notification; send within 72h of determining real-risk-of-significant-harm
      ↓
Notify affected individuals without unreasonable delay
      ↓
File breach record in breach-register (retained indefinitely per PIPEDA)
```

**Breach register location:** `reports/compliance/breach-register.md` (create on
first incident, retain indefinitely).

---

## Annual DPA Review

Every January, `legal` + `compliance`:

1. Confirm every vendor in `docs/vendor-inventory.md` has a current DPA listed here
2. Request updated DPAs from vendors that have not signed Spinr's standard template
3. Review any vendor DPA amendments published in the past year
4. Re-verify sub-processor lists
5. Re-verify regional hosting matches privacy policy disclosure
6. File evidence: `reports/compliance/YYYY-annual-dpa-review.md`

---

## Open Items

| Item | Severity | Owner | Target |
|---|---|---|---|
| Supabase DPA not filed locally (Canadian region attestation) | CRITICAL | legal | Before launch |
| Twilio DPA not filed locally | HIGH | legal | Before launch |
| Upstash/Redis Cloud DPA + region | CRITICAL | legal + infra | Before launch |
| Railway DPA verification | HIGH | legal | Before launch |
| Expo DPA (developer data + build artifacts) | MEDIUM | legal | Before launch |
| Gemini sub-processor not in privacy policy (**DV-16**) | MEDIUM | legal | Before launch |
| Email provider not yet selected → DPA pending | MEDIUM | backend + legal | Before launch |
| PagerDuty / OpsGenie DPA pending vendor selection | HIGH | devops + legal | Before launch |
| Status page provider DPA pending vendor selection | MEDIUM | devops + legal | Before launch |
| Breach register not yet created | LOW (needed on first incident) | compliance | On first incident |

---

## Change Log

| Date | Change | Author |
|---|---|---|
| 2026-04-24 | Initial register; DPA gaps flagged for launch | audit-framework |
