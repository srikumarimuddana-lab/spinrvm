# Spinr

## What This Is

Spinr is a pre-launch Canadian ride-sharing platform built Saskatchewan-first, operating on a 0% driver commission model. It connects riders and drivers through native mobile apps backed by a FastAPI monolith, managing real-time dispatch, payments, safety, and fleet operations. The platform has five integrated surfaces: backend API, rider app, driver app, admin dashboard, and a shared TypeScript library. It is in active development; device testing is the immediate next milestone before production launch.

## Core Value

Every fare dollar stays with the driver — Spinr monetises through corporate SaaS accounts and premium rider features, never per-trip commission cuts.

## Requirements

### Validated

- Zero-commission fare model implemented and under test
- HttpOnly cookie-based auth completed (P3 migration — admin + rider)
- Admin dual-cookie CSRF protection shipped
- Supabase RLS policies in place for all user-data tables
- Ride state machine with full guard transitions implemented
- Surge pricing engine with 2.5× hard cap running
- Corporate billing layer (allowance → wallet → card fallback) functional
- Insurance period logging (4 TNC periods) implemented

### Active

- [ ] P0-1: Resolve remaining HttpOnly backend cookie edge cases
- [ ] P0-2: Admin JWT TTL reduced to 12 hr (currently longer)
- [ ] P0-3: WAV dispatch — wheelchair-accessible vehicle matching (Saskatchewan legal)
- [ ] P0-4: First-rating crash fix (rider app)
- [ ] P0-5: Fare-collection state mismatch fix
- [ ] P0-6: GPS OOM fix (driver app)
- [ ] P0-7: SOS silent failure fix
- [ ] CI-1: Fix G4c npm-audit-admin (pino lockfile drift — only hard-blocking CI gate)
- [ ] CI-2: Fix G5b Gitleaks invalid `args:` input (non-blocking, cosmetic)
- [ ] CI-3: Fix claude-review `max_turns` invalid input (non-blocking, cosmetic)
- [ ] CI-4: E2E auth mocking (Playwright tests fail due to unprotected redirect)
- [ ] DEV: Dev secrets strategy — `.gitleaks.toml` allowlist + `.env.local` pattern
- [ ] TEST: Device testing preparation — dev backend reachable from physical devices
- [ ] TEST: Golden path verification — ride lifecycle end-to-end on real hardware
- [ ] LAUNCH: Pre-production hardening — security gates flipped to blocking
- [ ] LAUNCH: Production deployment — Railway + Vercel + Expo EAS

### Out of Scope

- Commission on consumer rides — core model constraint; never per-trip cut
- Surge above 2.5× auto — hard regulatory/reputational cap
- Employee-classification language — drivers are contractors; legal risk
- Third-party ad SDKs or behavioural retargeting — not a data product
- Auto-dial 911 via SOS — SOS offers one-tap, never auto-dials, never claims to replace emergency services
- Changing Supabase data residency region — compliance event, requires legal sign-off
- Hidden fees — every charge maps to a disclosed receipt line item

## Context

**Stage**: Pre-launch, dev environment only. Device testing commences once P0 sprint closes and CI is green. Production deployment is the final milestone.

**Team**: Small team, Claude Code–assisted development, YOLO execution mode.

**P0 Sprint (active)**: 6 security/safety findings in flight — HttpOnly backend token storage, admin TTL, first-rating crash, fare-collection state mismatch, GPS OOM, SOS silent failure. All 15 backend CI failures from the P0+P3 merge were fixed and merged in PR #397.

**CI state**: G4c (npm-audit-admin) is the single hard-blocking gate. G5b Gitleaks `args:` and `claude-review` `max_turns` are non-blocking config bugs scheduled for the next CI health phase. Security pipeline is in "baselining window" — all advisory except G4c.

**Dev secrets strategy**: Dev-tier keys live in `.env.local` (gitignored). `.gitleaks.toml` allowlist suppresses test-fixture false positives. Production secrets are NOT rotated during device testing; revisit in pre-production hardening.

**Saskatchewan legal**: WAV dispatch is a legal requirement under the Saskatchewan Transportation Act. It is P0-3, requiring /plan before implementation (5+ files + migration).

## Constraints

- **Regulatory (SK)**: Saskatchewan Transportation Act — WAV support, TNC insurance periods, trip logs 7-year retention, driver eligibility checks on every `go_online`
- **Privacy (PIPEDA)**: No PII in logs, data residency ca-central-1, right-to-delete within 30 days, consent version stored on signup
- **Insurance (SGI)**: 4 TNC periods logged per driver session; period transitions append-only, never deleted
- **Payments**: Decimal-only money arithmetic (`_d()`, `_round()`, `_f()` helpers required); Stripe idempotency via `claim_stripe_event` before every webhook; no hidden fees
- **Tech stack**: FastAPI 0.100+, Supabase (Postgres 15 + RLS), Redis, Stripe, Firebase FCM, Twilio; Expo SDK 54; Next.js 16; Python 3.12; Node 20
- **Security**: Admin JWT 12 hr TTL; rider/driver 15 min; OTP SHA-256 hashed at rest; 5-failures/hr lockout; settings (Stripe/Twilio keys) live in `app_settings` DB table, not `.env`

## Key Decisions

| Decision | Rationale | Outcome |
|---|---|---|
| 0% driver commission | Core differentiator; monetise via corporate SaaS, not per-trip | ✓ Locked |
| P3 HttpOnly cookie auth | Eliminates token exposure in JS-accessible memory | ✓ Shipped |
| Dual-cookie admin (access + CSRF) | Short TTL without constant re-login; CSRF-safe | ✓ Shipped |
| 401 = no-logout in `silentRefresh` | 401 means no refresh token cookie present (fresh visit); logout only on 403/5xx | ✓ Shipped |
| Dev secrets in `.env.local`, not rotated during device testing | Dev keys are throwaway sandbox tier; rotation overhead > benefit at this stage | ✓ Active |
| `.gitleaks.toml` allowlist | Suppress test-fixture false positives without disabling scanner | — Pending |
| GSD YOLO mode, standard granularity | Small team, high trust in roadmap; minimal approval overhead | ✓ Active |
| WAV dispatch before device testing | Saskatchewan legal requirement; cannot launch without it | ✓ Priority |
| Settings in DB, not `.env` | Allows Stripe/Twilio key rotation without redeployment | ✓ Shipped |

---
*Last updated: 2026-05-02 — GSD new-project initialisation*
