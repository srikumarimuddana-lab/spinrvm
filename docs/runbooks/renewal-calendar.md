# Runbook — Vendor/Service Renewal Calendar

**Owner:** `devops` · **Status:** SCAFFOLDING — dates below are placeholders
**Tracks:** `ACTION_ITEMS.md` E5 (see `docs/change-log/2026-09-03-e5-leading-indicator-monitoring.md`)

---

## Why this exists

`docs/vendor-inventory.md` tracks *which* vendors handle data (PIPEDA/SOC2
compliance angle) and enforces a quarterly review cadence via
`subprocessor-monitor.yml`. Neither tracks *when a paid plan/subscription
actually renews or lapses* — a materially different failure mode (a lapsed
plan can take the platform down or downgrade a tier mid-incident, same
category as the 2026-09-02 outage this doc's sibling `secret-rotation.md`
and `cert-domain-monitor.yml` were built in response to).

**No renewal date below has been verified against the actual vendor
account.** I (Claude, authoring this scaffold) do not have login access to
Fly, Railway, Supabase, Stripe, Twilio, SendGrid, Firebase, Cloudflare, the
domain registrar, Vercel, Expo, Sentry, or GitHub billing — every date
column is `TBD` until a human with account access fills it in. Do not treat
a blank/TBD row as "no renewal risk"; treat it as "not yet audited."

---

## How the automated check works

`.github/workflows/renewal-calendar-monitor.yml` runs weekly and parses the
table below for any `Renewal date` that is:
- within `WARN_DAYS` (default 30) of today → opens/updates a tracked issue
- blank/`TBD` → does **not** alert (nothing to compare against) but is
  called out separately in each run's summary so the gap stays visible
  without becoming alert noise

Fill in a real date and the automated check starts covering that row.

---

## Renewal calendar

| Vendor | Service | Renewal date | Cadence | Contract owner | Notes |
|---|---|---|---|---|---|
| Fly.io | Backend hosting (primary, `spinr-backend-yyz`) | TBD | Monthly billing | devops | Confirm payment method on file, not just plan tier |
| Railway | Backend hosting (standby) | TBD | Monthly billing | devops | Standby is currently drifting from `main` — ACTION_ITEMS C5; renewal risk compounds that gap |
| Supabase | Postgres + Auth + Storage + Realtime (Pro plan) | TBD | Monthly/annual — confirm | infra + data | See `docs/runbooks/capacity-scaling.md` for tier/add-on details |
| Cloudflare | DNS + CDN for `spinr.ca` zone | TBD | Annual (if paid plan) or N/A (free plan never lapses on its own, but the *domain* still needs its own registrar renewal — see below) | devops | Distinct from domain registration |
| Domain registrar | `spinr.ca` registration | TBD | Annual/multi-year | devops | Also covered by `cert-domain-monitor.yml`'s WHOIS check — that catches an approaching *expiry*, this row is for tracking the *renewal decision/payment* ahead of it |
| Stripe | Payment processing + Connect | N/A — usage-based, no renewal date | — | backend + finance | Listed for completeness; the risk here is API key rotation (see `secret-rotation.md`), not renewal |
| Twilio | SMS OTP delivery | TBD | Usage-based + account standing | backend | Confirm account isn't suspended for unpaid balance — this fails silently as OTP delivery errors, not an obvious "renewal" signal |
| SendGrid / Resend | Transactional email | TBD | Confirm plan | backend | `vendor-inventory.md` lists email provider as "Resend" with vendor TBD-decided; reconcile once finalized |
| Firebase / Google | Auth, FCM push, Crashlytics, App Check | N/A — usage-based (Blaze plan) | — | backend + mobile | Risk is billing account standing, not a renewal date |
| Vercel | Admin dashboard + marketing site hosting | TBD | Monthly/annual | admin | |
| Expo (EAS) | Mobile build pipeline | TBD | Monthly/annual subscription tier | mobile | Build quota exhaustion is a related but separate risk — not tracked here |
| Sentry | Error tracking | TBD | Monthly/annual | backend + mobile | Confirm still in active use — CLAUDE.md notes `ANTHROPIC_API_KEY`-adjacent cost decisions have paused other tooling before (C7) |
| GitHub | Source hosting + Actions minutes + Copilot (if used) | TBD | Monthly/annual (org plan) | devops | Actions minutes exhaustion mid-month would silently stop all CI, including this very monitoring workflow |
| PagerDuty / OpsGenie | On-call paging | N/A — not yet adopted (see `docs/runbooks/synthetic-monitoring.md`, ACTION_ITEMS E4) | — | devops | Add a row once adopted |
| Apple Developer Program | iOS distribution | TBD | Annual | mobile | Lapses silently until the next store submission fails |
| Google Play Console | Android distribution | N/A — one-time registration fee, no recurring renewal | — | mobile | |

---

## Adding a new vendor

1. Add a row here with a real `Renewal date` (or `N/A` with a one-line reason if usage-based/no-renewal).
2. Confirm it's also represented in `docs/vendor-inventory.md` if it handles any user data — these two docs serve different purposes and are not required to be kept in exact row-for-row sync, but a vendor missing from both is the real gap.
3. No code change is needed for the monitor to start covering it — `renewal-calendar-monitor.yml` parses this table directly.

---

## Change Log

- 2026-09-03 — Scaffolding created (this doc + `renewal-calendar-monitor.yml`), all dates TBD pending human account access. See `docs/change-log/2026-09-03-e5-leading-indicator-monitoring.md`.
