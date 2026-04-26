# Threat Model — Backend API

**Module:** `backend/` (FastAPI, Supabase, Redis, Stripe, Firebase, Twilio)
**Framework:** STRIDE · **Owner:** `backend` + `security` · **Cadence:** per `audit-framework/CHANGELOG.md` 90-day rule
**Version:** 1.0 · **Date:** 2026-04-24

---

## Trust Boundaries

```
  [Mobile apps]  <---HTTPS + App Check--->  [FastAPI backend]
                                               |
                                               |--- [Supabase DB (CA region)]
                                               |--- [Redis (cache / pubsub)]
                                               |--- [Stripe API (US)]
                                               |--- [Firebase Admin SDK (US)]
                                               |--- [Twilio SMS (US)]
                                               |--- [Google Maps/Gemini (US)]
  [Admin dashboard]  <--HTTPS + admin JWT-->   /

  [Scheduled jobs] (7 background loops on each replica, guarded by atomic claim)
```

Trust zones:
- **Z1 — Mobile clients** (least trust): all inputs validated, App Check enforced
- **Z2 — Backend process** (trusted): service-role key holder
- **Z3 — Supabase** (trusted, row-level policies within)
- **Z4 — Admin dashboard + user** (high trust, MFA required)
- **Z5 — External services** (trust per DPA + transport encryption)

---

## Assets

| Asset | Class (from data-classification) | Storage |
|---|---|---|
| Rider PII (phone, email, address) | C3 | Supabase (pgsodium) |
| Driver PII + licence + SIN-4 + bank | C4 | Supabase (pgsodium) |
| Stripe customer IDs / payment methods | C3 | Supabase + Stripe |
| Ride GPS traces | C5 | Supabase (retention 2 y) |
| Corporate wallet balances | C3 | Supabase |
| JWT secrets, Firebase service account, Stripe keys | C4 | .env + `app_settings` |
| Audit log (append-only) | C2 (metadata) / C3 (bodies) | Supabase |

---

## STRIDE Analysis (key threats only — exhaustive list in OPEN-ITEMS-TRACKER)

### Spoofing

| T-ID | Threat | Attacker | Asset | Current Mitigation | Residual Risk |
|---|---|---|---|---|---|
| S-1 | SIM-swap takeover of rider / driver account | Carrier insider + social engineer | Account, wallet | OTP rate-limit + lockout (Rule 1); sensitive-action reconfirm | MEDIUM (industry baseline) |
| S-2 | Firebase token used cross-app (rider token accepts on driver path) | Rogue account | Auth boundary | **PENDING — DV-10 open** | HIGH until DV-10 fixed |
| S-3 | Cloned mobile app bypasses App Check | Attacker with reverse-engineered APK | Dispatch / payments | App Check enforced in prod | LOW |
| S-4 | JWT forgery with weak secret | External | All | `JWT_SECRET ≥ 32 chars` startup check | LOW |
| S-5 | Refresh token replay after rotation | Stolen-device attacker | Session | SHA-256 hash + single-use rotation | LOW |

### Tampering

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| T-1 | Ride state machine bypass (e.g., COMPLETE from non-`in_progress`) | `_require_ride_in_state()` guard | **MEDIUM** — DV-3 state string mismatch open |
| T-2 | Race condition in ride-accept (two drivers accept same ride) | Supabase update with `status='searching'` filter | LOW (verified P2) |
| T-3 | Client-supplied fare estimate overrides server fare | Server-computed fare is authoritative | LOW (verify during D20 audit) |
| T-4 | Ride GPS trajectory fabricated by driver (fake trip) | GPS velocity / plausibility check | HIGH — not yet implemented; D19 backlog |
| T-5 | Money arithmetic drift via float | Pre-commit hook blocks float; Decimal-only helpers | LOW |
| T-6 | `$set` MongoDB wrapper silently no-ops Supabase update | **PENDING — DV-2 open** | HIGH until DV-2 fixed |

### Repudiation

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| R-1 | Rider disputes a charge but payment event not logged | `audit_logger` emits on payment events | MEDIUM — verify during D17 audit |
| R-2 | Driver claims ride never happened | WS events + state transitions persisted | LOW |
| R-3 | Admin action not attributable to an individual | Admin JWT has email claim; audit_log records actor_id | MEDIUM — break-glass account handling open |

### Information Disclosure

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| I-1 | Rider phone/address leaked to driver via API response | Driver-facing response schema strips PII | **VERIFY** during D21 rider audit |
| I-2 | Error message includes SQL, stack trace, or PII | FastAPI `exception_handlers` return generic 500 | LOW (verify ground-rules rule) |
| I-3 | Logs contain raw phone, address, or OTP | Redactor planned (`utils/log_redactor.py`) | HIGH until redactor implemented + corpus scan |
| I-4 | Supabase RLS bypass via service-role key misuse | Service-role only in backend; never exposed to mobile | LOW |
| I-5 | Gemini receives PII in text processed for support | **DV-16 open** — sub-processor disclosure missing | MEDIUM until DV-16 fixed |
| I-6 | Stripe error response body surfaces to user containing metadata | Sanitize before surfacing | MEDIUM — verify |
| I-7 | Admin `View as rider` shows more than rider sees | Verify during admin audit | UNKNOWN |

### Denial of Service

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| D-1 | OTP SMS flood exhausts Twilio budget | Per-phone + per-IP rate limit | LOW |
| D-2 | Ride-creation spam exhausts dispatch | Per-user rate limit on POST /rides | MEDIUM — verify in D19 |
| D-3 | WS connection flood | 30s ping + max-connections + rate-limit | LOW |
| D-4 | Redis falls back to in-process; per-replica rate-limit drift | **DV-6 open** — SRE alert missing | HIGH until DV-6 fixed |
| D-5 | Slow-loris / large-body attack | FastAPI + reverse proxy limits | LOW (verify) |

### Elevation of Privilege

| T-ID | Threat | Mitigation | Residual |
|---|---|---|---|
| E-1 | Rider token accepted on driver-only endpoint | Role check re-reads from `users` table | LOW |
| E-2 | IDOR: rider A reads rider B's rides | `WHERE rider_id = auth.uid()` + RLS | MEDIUM — needs runtime RLS verification |
| E-3 | Admin role claim in JWT bypassed for non-admin | CLAUDE.md: admin JWT fully trusted, others re-read | LOW |
| E-4 | Corporate wallet siphon via crafted request | `corporate_wallet_apply_delta` function + RLS | MEDIUM — verify in D21 |
| E-5 | Supabase direct-access bypasses API | RLS policies act as defense-in-depth | MEDIUM — RLS coverage audit pending |
| E-6 | Webhook replay as administrative action | Stripe signature verification; idempotency via `stripe_events` | LOW |

---

## Attack Trees (top 3)

### AT-1: Drain corporate wallet master account

```
Goal: Transfer corporate wallet balance to attacker-controlled rider account
├── 1. Forge a wallet transfer request
│   ├── 1.1 Obtain a valid corporate-admin JWT  → [E-3 mitigated, admin MFA required]
│   ├── 1.2 SQLi into /corporate/wallet endpoint → [LOW: parameterised queries]
│   └── 1.3 Bypass `corporate_wallet_apply_delta` row-lock → [MEDIUM: code review needed]
├── 2. Trigger payout path
│   ├── 2.1 Self-approve transfer → [Needs dual-approval design — OPEN]
│   └── 2.2 Use service-role key directly → [LOW: key not exposed]
└── 3. Withdraw
    └── 3.1 Onboard rogue driver, payout to own Stripe Connect → [MEDIUM: needs KYC]
```

**Mitigation gap:** Dual-approval for bulk corporate transfers (DV documentation gap).

### AT-2: Mass rider phone/address exfiltration

```
Goal: Exfiltrate 10,000+ rider phones + home addresses
├── 1. Compromise admin account → [S-1 + admin MFA required; break-glass handling needed]
├── 2. Abuse admin "export all" endpoint → [verify admin audit: does this exist?]
├── 3. SQL injection on public endpoint → [LOW: Pydantic + parameterised]
└── 4. Log mining for PII (if I-3 not fixed) → [HIGH until log redactor lands]
```

### AT-3: Ride hijack (redirect rider to attacker location)

```
Goal: Have a rider's driver assignment become attacker
├── 1. Race-condition in accept → [T-2 mitigated]
├── 2. WS spoof with forged events → [auth required first, keyed connections]
└── 3. DV-1: dispatch offers ride to suspended driver → [DV-1 open; HIGH until fixed]
```

---

## Residual Risk Register (feeds OPEN-ITEMS-TRACKER)

| Threat | Risk score | Owner | Target sprint |
|---|---:|---|---|
| S-2 (Firebase cross-audience) | 64 | backend | P1 |
| T-4 (fake GPS trips) | 48 | backend | P2 |
| T-6 (DV-2 $set wrapper) | 96 | backend | P1 |
| I-3 (PII in logs) | 64 | backend | P2 |
| D-4 (DV-6 Redis fallback alert) | 48 | devops | P2 |
| E-4 (corp wallet dual-approval) | 32 | backend + product | P2 |

---

## Review Cadence

- Re-run this threat model every 90 days (aligned with framework CHANGELOG rule)
- After any architectural change (new route, new external vendor, new background job)
- After any CRITICAL/HIGH incident that wasn't anticipated here

**Next review due:** 2026-07-23
