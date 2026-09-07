---
name: Security Standards
description: Security requirements and best practices for the Spinr platform
---

# Security Standards

> **Note:** This file is part of an older, legacy multi-agent scaffold (`.agents/`) and is not
> actively maintained. The root `CLAUDE.md` is the current, authoritative source of engineering
> rules for this repo. Where this file and `CLAUDE.md` conflict, treat `CLAUDE.md` as correct.

## Secrets Management
| Rule | Enforcement |
|------|------------|
| All secrets in `.env` files | Pre-commit check |
| `.env` in `.gitignore` | Verified by security audit |
| `.env.example` with placeholders only | Required for each component |
| No secrets in logs | Code review check |
| Frontend secrets in `expo-secure-store` | Code review check |

## Authentication

_(Corrected 2026-09-07 — this table previously described a Firebase-ID-token-primary model with a
flat 30-day token expiry, which doesn't match how auth actually works. See root `CLAUDE.md`'s "JWT
trust model" and "Token lifetimes" sections for the authoritative version.)_

| Requirement | Implementation |
|-------------|----------------|
| Admin JWT | Fully trusted (role + email + modules in claims) |
| Rider/driver role | Always re-read from the `users` table on every request — never trusted from the JWT role claim |
| Access token expiry | 15 min (rider/driver), 1 hr (admin) |
| Refresh token | 30 days, stored as SHA-256 hash, rotated on every use |
| OTP | SHA-256 hashed at rest; 5 failures/hour triggers a 24-hour Redis lockout |
| Admin role | Server-side verification only |
| Password storage | Never stored — OTP-based auth |

## API Security
| Requirement | How |
|-------------|-----|
| Rate limiting | `slowapi` on auth endpoints |
| CORS | Whitelist known origins only |
| Input validation | Pydantic models + `validators.py` |
| SQL injection | Supabase SDK (parameterized) |
| XSS prevention | No raw HTML rendering |
| CSRF | Token-based auth (no cookies) |

## Database Security (Supabase)
- RLS (Row Level Security) enabled on all tables
- Users can only access their own records
- Admin operations require admin role
- No public tables without RLS policies
- Service role key only used server-side

## Payment Security (PCI Compliance)
- No card numbers stored locally or in database
- Stripe handles all card processing
- Payment intents created server-side only
- Client only uses Stripe publishable key
- Webhook signatures verified for all Stripe webhooks

## Mobile App Security
- Auth tokens stored in `expo-secure-store` (NOT `AsyncStorage`)
- No sensitive data in app bundle
- Certificate pinning for production
- Deep links verified before acting on them
- No debug logs in production builds

## Dependency Security
- Run `pip audit` before each release
- Run `npm audit` before each release
- Update dependencies with known CVEs within 7 days
- Pin major versions to avoid breaking changes

## Incident Response
| Severity | Response Time | Action |
|----------|--------------|--------|
| Critical (data breach, auth bypass) | Immediate | Rotate secrets, patch, notify |
| High (exposed endpoint, XSS) | 24 hours | Patch and deploy |
| Medium (missing rate limit, weak validation) | 1 week | Schedule fix |
| Low (verbose errors, missing headers) | Next sprint | Track in backlog |
