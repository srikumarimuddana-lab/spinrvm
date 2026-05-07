# Vendor Inventory

**Purpose:** Authoritative list of every external service Spinr relies on.
Required by D22 (third-party risk), SOC2 CC9.2, PIPEDA s.4.1.3 (cross-border
disclosure), and PCI-DSS 12.8 (service-provider tracking).

**Owner:** `compliance` + `infra` · **Review cadence:** quarterly + on any new
vendor onboarding.

---

## Schema

Each vendor row carries:

- **Vendor** · legal entity name
- **Service** · what Spinr uses it for
- **Data class** · from `docs/data-classification.md` (C1–C5)
- **Region** · primary data hosting region
- **DPA on file** · data processing agreement status
- **Sub-processors** · Vendor's own sub-processors (monitored separately)
- **Disclosed in privacy policy** · ✅ if listed as sub-processor
- **DPIA status** · Data Protection Impact Assessment (if applicable)
- **Criticality** · CRITICAL (prod must pause if down) / HIGH / MEDIUM / LOW
- **Contract owner** · Spinr team responsible for renewal / review

---

## Active Vendors

### Infrastructure

| Vendor | Service | Data class | Region | DPA | Disclosed | DPIA | Criticality | Contract owner |
|---|---|:-:|---|:-:|:-:|:-:|:-:|---|
| **Supabase** (Supabase Inc., DE/US HQ) | Postgres + Auth + Storage + Realtime | C1–C5 | **Canada (ca-central-1)** — VERIFY | ✅ | ✅ | ✅ | CRITICAL | infra + data |
| **Railway** | Backend hosting (primary) | C2 (runtime only) | US | ✅ | ✅ | — | CRITICAL | devops |
| **Render** (fallback host) | Backend hosting (failover) | C2 (runtime only) | US | ✅ | Partial | — | HIGH | devops |
| **Vercel** | Admin dashboard + marketing site | C2 (runtime only) | US | ✅ | ✅ | — | HIGH | admin |
| **Upstash** (Redis) or **Redis Cloud** | Cache + pub/sub + rate-limit | C2/C3 (OTP hash, session) | **Verify region** | ⚠ VERIFY | ⚠ VERIFY | — | CRITICAL | devops |
| **Cloudflare** (if in path) | DNS + DDoS + CDN | C1 | Global edge | ✅ | Partial | — | HIGH | devops |

### Payments & Financial

| Vendor | Service | Data class | Region | DPA | Disclosed | DPIA | Criticality | Contract owner |
|---|---|:-:|---|:-:|:-:|:-:|:-:|---|
| **Stripe, Inc.** | Card processing (PaymentIntents) + Stripe Connect (driver payouts) | C3/C4 (no PAN stored at Spinr) | US (prod) | ✅ | ✅ | ✅ | CRITICAL | backend + finance |
| **Stripe Radar** | Fraud detection | C3 | US | Via Stripe DPA | ✅ | — | HIGH | backend |

### Authentication & Messaging

| Vendor | Service | Data class | Region | DPA | Disclosed | DPIA | Criticality | Contract owner |
|---|---|:-:|---|:-:|:-:|:-:|:-:|---|
| **Google / Firebase** | Auth (Firebase Auth), Push (FCM), Crashlytics, App Check | C3 | US | ✅ (Google DPA + SCCs) | ✅ | ✅ | CRITICAL | backend + mobile |
| **Twilio** | SMS OTP delivery | C3 (phone + OTP) | US | ✅ | ✅ | — | CRITICAL | backend |
| **Apple (APNs)** | Push notifications (iOS) | C2 | US | Via Apple DPA | ✅ | — | HIGH | mobile |

### Maps & AI

| Vendor | Service | Data class | Region | DPA | Disclosed | DPIA | Criticality | Contract owner |
|---|---|:-:|---|:-:|:-:|:-:|:-:|---|
| **Google Maps Platform** | Geocoding, Directions, Place Details, Static Maps | C3 (addresses queried) | US | ✅ | ✅ | Partial | CRITICAL | backend + mobile |
| **Google Gemini** | AI text processing (support features) | C2/C3 (user text) | US | ✅ | **❌ — DV-16 open** | ❌ | MEDIUM | backend + legal |

### Build & Release

| Vendor | Service | Data class | Region | DPA | Disclosed | DPIA | Criticality | Contract owner |
|---|---|:-:|---|:-:|:-:|:-:|:-:|---|
| **Expo (EAS)** | Mobile build pipeline | C2 (source code, build artifacts) | US | ✅ | Partial (developer-only data) | — | HIGH | mobile |
| **GitHub** | Source hosting + Actions | C2 | US | ✅ | Partial | — | HIGH | devops |
| **Apple App Store Connect** | iOS distribution | C2 (submitter info) | US | Via Apple | Partial | — | HIGH | mobile |
| **Google Play Console** | Android distribution | C2 (submitter info) | US | Via Google | Partial | — | HIGH | mobile |

### Observability

| Vendor | Service | Data class | Region | DPA | Disclosed | DPIA | Criticality | Contract owner |
|---|---|:-:|---|:-:|:-:|:-:|:-:|---|
| **Sentry** *(if in use)* | Error tracking | C2/C3 (stack + user_id) | US | ✅ | ✅ | — | HIGH | backend + mobile |
| **Firebase Crashlytics** | Mobile crash reports | C2/C3 | US | Via Google DPA | ✅ | — | HIGH | mobile |

### Support & Ops

| Vendor | Service | Data class | Region | DPA | Disclosed | DPIA | Criticality | Contract owner |
|---|---|:-:|---|:-:|:-:|:-:|:-:|---|
| **Email provider** (SendGrid / Postmark — TBD) | Transactional email | C3 (email addr) | US | ✅ on contract | Required | — | HIGH | backend |
| **Status page provider** (Statuspage / Atlassian — TBD) | Public status page | C1 | US | ✅ on contract | Partial | — | MEDIUM | devops |
| **PagerDuty / OpsGenie — TBD** | On-call paging | C2 (internal IDs only) | US | ✅ on contract | — | — | HIGH | devops |

---

## Vendor Onboarding Checklist

Before Spinr sends any production data to a new vendor:

- [ ] Security review filed: `reports/vendor-review/YYYY-MM-DD-<vendor>.md`
- [ ] DPA signed (or standard contractual clauses in place)
- [ ] Sub-processor list obtained and reviewed
- [ ] Data class categorized per `docs/data-classification.md`
- [ ] Regional hosting confirmed; cross-border disclosure drafted for privacy policy
- [ ] DPIA completed if high-risk (special-category data, novel use, large scale)
- [ ] Access scope limited to minimum necessary
- [ ] Vendor's security attestations on file (SOC 2 Type 2, ISO 27001, or equivalent)
- [ ] Incident-response contact + escalation path documented
- [ ] Exit strategy: how to extract Spinr data + purge vendor's copy
- [ ] Added to this inventory
- [ ] Added to `docs/dpa-register.md`

---

## Vendor Offboarding Checklist

When a vendor relationship ends:

- [ ] Request data deletion certification (must arrive within 30 days of termination)
- [ ] Rotate any keys / credentials Spinr had with the vendor
- [ ] Remove vendor row from privacy policy (publish update within 30 days)
- [ ] Mark row in this inventory as "RETIRED — YYYY-MM-DD"
- [ ] Archive contract + last-access-log to `reports/vendor-archive/`

---

## Known Gaps (triage targets)

| Gap | Vendor | Severity | Action |
|---|---|---|---|
| Supabase region not confirmed in doc | Supabase | CRITICAL if non-CA | `data` to verify + update this file |
| Upstash region unknown | Upstash | HIGH | `devops` to verify |
| Gemini not in privacy policy | Google | MEDIUM | **DV-16 open** |
| DPIA for Google Maps not filed | Google | MEDIUM | `compliance` to file |
| ~~No sub-processor monitoring cadence~~ | Multiple | RESOLVED | `.github/workflows/subprocessor-monitor.yml` (B-P3-3) opens a `subprocessor-review` issue if this doc hasn't been touched in 90+ days |
| Email provider not yet selected | — | MEDIUM | `backend` to decide + onboard |
| PagerDuty/OpsGenie not yet selected | — | HIGH | `devops` to decide |
| Status page provider not yet selected | — | MEDIUM | `devops` to decide |

---

## Monitoring (B-P3-3)

A scheduled GitHub Action — `.github/workflows/subprocessor-monitor.yml` —
runs every Monday at 13:00 UTC and:

1. Reads the last commit date for this file (excluding bot-only commits).
2. If the file is older than 90 days, opens a `subprocessor-review`
   GitHub issue assigned to the `compliance` label.
3. The check is idempotent — a single open issue silences the workflow
   until the issue is closed.

The intent is to enforce the quarterly review cadence the doc itself
declares. Closing the auto-generated issue does **not** reset the 90-day
clock; only an actual commit to this file does. The expected close-out
flow is:

- Owners walk the **Vendor Onboarding** + **Active Vendors** rows.
- Update the **Change Log** entry with the new review date.
- Commit with message `chore(compliance): vendor inventory quarterly review YYYY-Q`.
- Close the GitHub issue once the commit lands.

Manual trigger is also exposed via `workflow_dispatch` with a
`force_open_issue=true` input for ad-hoc reviews (e.g. mid-cycle vendor
changes).

Future enhancement: per-vendor sub-processor URL list with hash-diff,
so material changes to (e.g.) Stripe's published sub-processor page
auto-flag here. Tracked separately; not in scope for B-P3-3.

---

## Change Log

| Date | Change | Author |
|---|---|---|
| 2026-04-24 | Initial inventory created | audit-framework |
| 2026-04-28 | B-P3-3: scheduled `subprocessor-monitor.yml` workflow added; review-cadence gap resolved | session 01L8Q1k |
