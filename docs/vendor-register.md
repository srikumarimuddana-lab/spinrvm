# Vendor Register — PIPEDA DPA Inventory

Spinr processes personal data through the third-party sub-processors listed
below. Each entry must be reviewed annually and updated whenever a vendor
changes its DPA, data region, or service scope.

**Owner:** Privacy Officer / Legal  
**Last reviewed:** 2026-04-27  
**Next review due:** 2027-04-27

---

## Sub-processor inventory

| Vendor | Role | Data class | DPA / Privacy policy URL | Processing region | Effective date | Renewal date | Notes |
|--------|------|-----------|--------------------------|-------------------|---------------|-------------|-------|
| **Supabase** (PostgreSQL) | Primary datastore | All app data: rides, drivers, riders, payments, corporate accounts | [Supabase DPA](https://supabase.com/privacy) — obtain signed DPA from Supabase Sales | `ca-central-1` (Supabase Cloud Canada) | _TBD — file DPA_ | Annual | Must remain in Canadian region. Changing regions is a compliance event — requires legal sign-off. |
| **Vercel** (hosting) | Frontend / admin-dashboard hosting | Admin session metadata, page-load paths (URL-scrubbed), Vercel Analytics page-view counts (IDs scrubbed) | [Vercel DPA](https://vercel.com/legal/dpa) | `yyz1` (Toronto) — pinned in `vercel.json` | _TBD — file DPA_ | Annual | Analytics `beforeSend` strips entity IDs before ingestion. Admin dashboard has `robots: noindex`. |
| **Sentry** (error telemetry) | Error tracking & performance monitoring | Error stack traces (PII-scrubbed in `beforeSend`), anonymised session replays | [Sentry DPA](https://sentry.io/legal/dpa/) | ⚠ **US ingestion** (default SaaS) — **must migrate to EU or file DPA addendum before launch** (A-PE-P2-5). EU endpoint: `https://o<id>.ingest.de.sentry.io/...` | _TBD_ | Annual | `blockAllMedia: true`. All PII keys filtered. URL path IDs scrubbed. A startup check in `sentry.server.config.ts` logs a PIPEDA error in production until this is resolved. |
| **Stripe** (payments) | Payment processing | Payment card data (handled entirely by Stripe — Spinr never stores PANs), Stripe customer IDs, payout records | [Stripe DPA](https://stripe.com/legal/dpa) | US / EU (Stripe-controlled) | _TBD — file DPA_ | Annual | PCI-DSS Level 1 compliant. Tax/receipt data retained 7 years per Saskatchewan Transportation Act. |
| **Firebase / FCM** (push notifications) | Push notifications to rider & driver apps | Device tokens, notification payloads (ride status only — no PII in payload body) | [Google Cloud DPA](https://cloud.google.com/terms/data-processing-addendum) | US (Firebase default) — evaluate CA region or addendum | _TBD_ | Annual | Payloads contain ride_id only; no rider/driver PII. |
| **Twilio** (SMS / OTP) | OTP delivery, SMS notifications | Phone numbers (last-4 logged only; full number sent to Twilio for delivery) | [Twilio DPA](https://www.twilio.com/en-us/legal/data-protection-addendum) | US (Twilio default) — evaluate addendum | _TBD_ | Annual | Twilio receives full phone number for SMS routing. PIPEDA: minimal retention, no marketing use. |
| **Railway** (backend hosting) | FastAPI backend runtime | All request traffic passing through backend (auth, rides, payments) | [Railway Privacy Policy](https://railway.app/legal/privacy) — obtain DPA | US (Railway default) — evaluate CA region or addendum | _TBD_ | Annual | Backend processes all app data in transit. Data at rest is in Supabase (CA). |

---

## Open items

| Item | Owner | Due |
|------|-------|-----|
| File signed DPA with Supabase | Legal | Q3 2026 |
| File signed DPA with Vercel | Legal | Q3 2026 |
| File signed DPA with Stripe | Legal | Q3 2026 |
| Resolve Sentry US ingestion — migrate to EU or DPA addendum (A-PE-P2-5). Steps: (1) Sentry → Settings → Data Storage → request EU migration, or create new EU org at sentry.io, (2) update `SENTRY_DSN` + `NEXT_PUBLIC_SENTRY_DSN` in Vercel to use `ingest.de.sentry.io` endpoint, (3) startup check in `sentry.server.config.ts` will stop logging PIPEDA errors once EU DSN is set. | Engineering + Legal | Q2 2026 |
| Evaluate Firebase/FCM Canadian region availability | Engineering | Q3 2026 |
| Evaluate Twilio Canadian routing / addendum | Legal | Q3 2026 |
| Evaluate Railway Canadian region or addendum | Engineering | Q3 2026 |

---

## How to update this register

1. When adding a new third-party service that will process personal data, add a row before the PR merges.
2. Fill in all columns. "Data class" must map to the definitions in `CLAUDE.md § PIPEDA`.
3. Tag Legal (`@spinr/legal`) as a reviewer on the PR.
4. Set a calendar reminder for the renewal date.
5. If the vendor changes their DPA or processing region, treat it as a material change and re-review within 30 days.
