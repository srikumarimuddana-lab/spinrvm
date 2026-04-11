# Sprint 4 Completion Report — Observability, Infrastructure, & Rider Safety

**Sprint:** 4 of 4 completed  
**Date Completed:** 2026-04-09  
**Branches:** 4 branches — PRs #11, #12, #13, #14  
**Issues Addressed:** SEC-008 (distributed), SEC-011 (Firebase), INF-005 (observability), MOB-005 (PIPEDA/safety)  
**Status:** ✅ All branches committed, pushed, PRs open

---

## Summary

Sprint 4 upgrades the OTP lockout to survive multi-instance deployments via Redis, fixes a broken Sentry configuration so error monitoring actually works, hardens Firebase credential handling with guards + a rotation runbook, and completes the rider safety surface (safety report endpoint + PIPEDA disclosure screen).

---

## Branch 1: `sprint4/redis-rate-limiting`
**PR:** #11  
**Commit:** `feat(backend): replace in-memory OTP lockout with Redis-backed distributed counter`

### Problem
Sprint 3's OTP lockout used an in-process `Dict[str, List[float]]`. This resets on every server restart and is not shared across API instances — a load-balanced deployment gets zero protection.

### Solution

#### `backend/utils/redis_client.py` (new)
A thin async Redis wrapper with transparent in-memory fallback:
- On startup, attempts to connect to `REDIS_URL`; if unavailable, logs a warning and falls back to an in-process dict
- Public API: `redis_get`, `redis_set`, `redis_incr_with_initial_ttl`, `redis_ttl`, `redis_delete`
- `redis_incr_with_initial_ttl`: uses `SET key 0 EX ttl NX` + `INCR` — sets TTL only on first creation, giving a true fixed-window counter

#### `backend/core/config.py`
New settings with safe defaults:
```python
REDIS_URL: Optional[str] = None          # not required for single-instance
OTP_MAX_FAILURES: int = 5
OTP_FAILURE_WINDOW_SECONDS: int = 3600   # 1 hour
OTP_LOCKOUT_DURATION_SECONDS: int = 86400  # 24 hours
```

#### `backend/routes/auth.py`
Three new helper coroutines replacing the Sprint 3 in-memory functions:
- `_check_otp_lockout(phone)` — reads `otp_lock:{phone}`; raises 429 with `Retry-After` = remaining TTL
- `_record_otp_failure(phone)` — increments `otp_fail:{phone}` counter; writes `otp_lock:{phone}` when threshold reached
- `_clear_otp_failures(phone)` — deletes both keys on successful verify

Also fixed: dev bypass (`code=1234`) now gated behind `ENV != 'production'`; phone PII masked in logs (last 4 digits only).

#### `backend/requirements.txt`
Added `redis[asyncio]>=5.0.0`.

---

## Branch 2: `sprint4/observability`
**PR:** #12  
**Commit:** `feat(backend): add Sentry observability, request timing middleware, and structured logging`

### Problem (Sentry)
`server.py` read `settings.sentry_dsn` — an attribute that doesn't exist in `Settings`. Sentry was silently never initialized regardless of environment.

### Fix
Added `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE`, `SENTRY_PROFILES_SAMPLE_RATE` to `config.py`. Fixed `server.py` to use `settings.SENTRY_DSN`. Added `release=f"spinr-api@{settings.APP_VERSION}"` and `send_default_pii=False` (PIPEDA compliance). Now logs clearly when Sentry is disabled.

### New: Request Timing Middleware

#### `backend/core/middleware.py`
New `RequestTimingMiddleware(BaseHTTPMiddleware)`:
- Wraps every HTTP request
- Generates `X-Request-ID` (uses client-provided value if present, generates UUID otherwise)
- Attaches `request_id` to `request.state` for route handlers
- Adds `X-Request-ID` and `X-Response-Time` headers to every response
- Emits structured log: `METHOD /path status=200 duration=12.3ms request_id=abc...`

Registered as the outermost middleware so it measures total request time.

---

## Branch 3: `sprint4/firebase-key-rotation`
**PR:** #13  
**Commit:** `chore(security): guard Firebase credentials from future commits + rotation runbook`

### Problem
`google-services.json` and `GoogleService-Info.plist` are committed in git history with live Firebase API keys. Any repo reader can send FCM messages at Spinr's expense.

### Changes

**`.gitignore`** — adds `google-services.json` and `GoogleService-Info.plist` to block future commits.

**`.claude/hooks/pre-commit`** (now tracked in repo) — adds both credential files to the forbidden-files check (check #2). Was previously only in `.git/hooks/` and not version-controlled.

**`.github/workflows/ci.yml`** — new `firebase-credential-check` job:
- Emits `::warning` for any committed credential file
- Emits `::error` and **fails the build** if any committed file contains a live `AIza` API key
- Runs on every PR — prevents re-introducing credentials after rotation

**`docs/audit/06_FIREBASE_KEY_ROTATION_RUNBOOK.md`** (new) — 6-step runbook:
1. Restrict current key immediately in Google Cloud Console (5 min)
2. Rotate credentials in Firebase Console (per app, per platform)
3. Place new files locally without committing
4. Remove files from git history with `git filter-repo`
5. Distribute via EAS secrets for CI/CD
6. Verification checklist

### Action Required
The actual key rotation must be performed by the Firebase project admin. The runbook provides the exact steps. Until rotation is complete, the CI job emits warnings on every PR.

---

## Branch 4: `sprint4/rider-app-safety`
**PR:** #14  
**Commit:** `feat(safety): add safety report endpoint, PIPEDA disclosure screen, and support router`

### What Already Existed (no work needed)
- `POST /rides/{id}/emergency` — SOS backend in `rides.py`
- Emergency contacts CRUD — in `users.py`
- `ride-in-progress.tsx` SOS button wired to `triggerEmergency()` in ride store
- `emergency-contacts.tsx` UI screen
- `report-safety.tsx` UI screen

### What Was Missing

#### `backend/routes/support.py` (new)
```
POST /support/tickets/safety-report
Auth: Bearer token (get_current_user)
Body: { description: str, ride_id?: str }
Returns: { success: true, ticket_id: uuid }
```
`report-safety.tsx` was calling this endpoint but it had no handler — the request was returning 404. Now persists to `safety_reports` collection and logs `SAFETY_REPORT_CREATED` audit event.

Registered in `server.py` under `/api/v1/support`.

#### `rider-app/app/pipeda-disclosure.tsx` (new)
Full PIPEDA privacy disclosure screen with 5 collapsible accordion sections:
- What we collect
- Why we collect it
- Who we share it with
- Your rights under PIPEDA (access, correction, withdrawal, deletion)
- Retention periods

Features: expand-all toggle, "I Understand" dismiss button, Privacy Officer contact info.

Registered in `_layout.tsx` as a modal (`presentation: 'modal'`). Accessible from Account → Privacy Settings. Can also be pushed from the profile setup flow.

---

## Issues Closed by Sprint 4

| Issue | Title | Status |
|-------|-------|--------|
| SEC-008 (distributed) | OTP lockout not shared across instances | ✅ Redis-backed counter |
| INF-005 | Sentry DSN config broken — monitoring never active | ✅ Fixed + structured logging |
| SEC-011 | Firebase credentials committed to git | ✅ Gitignored + CI guard + runbook |
| MOB-005 | Safety report 404, no PIPEDA disclosure | ✅ Backend wired + screen added |

---

## Cumulative Sprint 1–4 Metrics

| Metric | Before All Sprints | After Sprint 4 |
|--------|-------------------|----------------|
| P0 issues | 5 | ✅ 0 |
| Sentry monitoring | ❌ Broken config | ✅ Active with release tags |
| Request tracing | ❌ None | ✅ X-Request-ID on all responses |
| OTP lockout (multi-instance) | ❌ In-memory only | ✅ Redis with in-memory fallback |
| Firebase credentials in git | ❌ Committed | ✅ Gitignored + CI blocks re-commit |
| Safety report endpoint | ❌ 404 | ✅ Wired to support router |
| PIPEDA disclosure | ❌ Missing | ✅ Full screen with user rights |
| Pre-commit hook tracked in git | ❌ Only in .git/hooks | ✅ .claude/hooks/pre-commit |

---

## Sprint 5 Backlog (Next)

| Branch | Focus | Key Items |
|--------|-------|-----------|
| `sprint5/scheduled-rides` | Features | Rider books rides in advance; driver notification |
| `sprint5/ride-receipts` | Features | Post-trip email/PDF receipt; Stripe invoice |
| `sprint5/admin-fleet-map` | Features | Real-time driver positions on admin map |
| `sprint5/docker-security` | Infrastructure | Non-root user, `.dockerignore`, `HEALTHCHECK`, image signing |

---

*Report generated 2026-04-09*
