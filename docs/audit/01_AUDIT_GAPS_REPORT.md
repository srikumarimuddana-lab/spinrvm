# Spinr — Security & Product Audit: Gap Analysis Report

**Audit Date:** 2026-04-07
**Audited Repository:** `srikumarimuddana-lab/spinr` (read-only upstream)
**Working Repository:** `ittalenthireca-sketch/Spinr`
**Auditor:** Claude Code (Anthropic) — Fortune 100 Production Readiness Assessment
**Audit Standard:** OWASP Top 10 (2021), NIST SP 800-53, CWE Top 25, Ride-Share Platform Security Baseline

---

## Executive Summary

A comprehensive, layer-by-layer audit of the Spinr codebase was conducted across all 5 application surfaces (backend API, rider app, driver app, admin dashboard, frontend web). The audit identified **55 issues** across 9 categories:

| Severity | Count | Must Fix Before |
|----------|-------|----------------|
| **P0 — Critical** | 5 | Any production traffic |
| **P1 — High** | 24 | First user-facing release |
| **P2 — Medium** | 23 | Public beta launch |
| **P3 — Low** | 3 | Post-launch hardening |

The 5 P0 issues represent conditions that would cause immediate security incidents or platform failure in production and must be remediated before a single user request is served.

---

## Issue Categories

| Code | Category | Description |
|------|----------|-------------|
| SEC | Security | Auth, secrets, OWASP, injection, encryption |
| INF | Infrastructure | CI/CD, Docker, secrets management, deployment |
| CQ | Code Quality | Architecture, maintainability, error handling |
| TST | Testing | Test coverage, test infrastructure |
| MOB | Mobile | React Native / Expo app gaps |
| DOC | Documentation | Missing/stale documentation |
| AI | AI/ML | Auto-learn and intelligence features |
| COM | Compliance | Privacy, PIPEDA, regulatory |
| FEAT | Feature Completeness | Missing product functionality |

---

## P0 — Critical (Fix Before Any Production Traffic)

### SEC-001 — Hardcoded JWT Secret
- **File:** `backend/core/config.py:14`
- **Issue:** `JWT_SECRET: str = "your-strong-secret-key"` — a literal string committed to the repository. Any JWT signed with this secret can be forged by anyone who has read the source code (all GitHub users for a public repo; all collaborators for a private one).
- **OWASP:** A02:2021 — Cryptographic Failures
- **CWE:** CWE-798 — Use of Hard-coded Credentials
- **Impact:** Complete authentication bypass. An attacker can forge any user's JWT and impersonate any account, including admin.
- **Remediation:** Remove default, add production startup guard that raises `RuntimeError` if `JWT_SECRET` env var is absent or fewer than 32 characters.

### SEC-002 — Hardcoded Admin Credentials
- **File:** `backend/core/config.py:20-21`
- **Issue:** `ADMIN_EMAIL: str = "admin@spinr.ca"` and `ADMIN_PASSWORD: str = "admin123"` committed to source. Default admin credentials are a well-known attack vector.
- **OWASP:** A07:2021 — Identification and Authentication Failures
- **Impact:** Full admin dashboard access for anyone who reads the config file.
- **Remediation:** Remove defaults entirely; require env vars with no fallback in production.

### SEC-003 — CORS Wildcard in Production
- **File:** `backend/core/config.py:17`, `backend/core/middleware.py`
- **Issue:** `ALLOWED_ORIGINS: str = "*"` allows any origin to make credentialed cross-origin requests. Combined with `allow_credentials=True`, this violates the CORS spec and exposes session tokens to any website.
- **OWASP:** A05:2021 — Security Misconfiguration
- **Impact:** CSRF-equivalent attack surface; any malicious website can make authenticated API calls on behalf of a logged-in rider or driver.
- **Remediation:** Explicit origin allowlist; production guard that raises `RuntimeError` if `*` is detected at startup.

### SEC-004 — Docker Security: Root User, No .dockerignore, No Health Check
- **File:** `backend/Dockerfile`
- **Issue:** Container runs as root, no `.dockerignore` (credentials/keys potentially copied into image), no `HEALTHCHECK` directive.
- **OWASP:** A05:2021 — Security Misconfiguration
- **Impact:** Container escape exploits run as root; secrets may be baked into the image layer; orchestrators can't detect unhealthy containers.
- **Remediation:** Add `USER spinr`, `.dockerignore`, `HEALTHCHECK`.

### MOB-001 — Driver App Has No Push Notification Handler
- **File:** `driver-app/app/_layout.tsx`, `driver-app/hooks/useDriverDashboard.ts`
- **Issue:** The driver app registers an FCM token but has no `setBackgroundMessageHandler`, no `getInitialNotification`, and no `onNotificationOpenedApp`. A driver with the app closed or backgrounded never receives ride offers.
- **Impact:** Platform cannot function. Drivers miss all ride requests when the app is not in the foreground. Core revenue flow is broken.
- **Remediation:** Implement all three FCM lifecycle handlers; remove conflicting Expo push token registration.

---

## P1 — High (Fix Before First User-Facing Release)

### SEC-005 — JWT Secret Logged on Every Token Operation
- **File:** `backend/dependencies.py:45, 105`
- **Issue:** `logger.info(f"JWT prefix: {JWT_SECRET[:10]}...")` logged on every token creation. `logger.debug(f"... secret={JWT_SECRET}")` logged on every JWT verification failure — logging the FULL secret on every 401.
- **OWASP:** A09:2021 — Security Logging and Monitoring Failures
- **Impact:** JWT secret leaks into every log aggregator, Sentry, Datadog, CloudWatch. Anyone with log access has the signing key.
- **Remediation:** Remove all JWT_SECRET references from log statements.

### SEC-006 — OTP is Only 4 Digits
- **File:** `backend/dependencies.py` — `generate_otp(k=4)`
- **Issue:** 4-digit OTP has only 10,000 possible values. With the 5-minute expiry window and no cumulative lockout, an attacker has 50,000 attempts over the expiry period at 10/minute.
- **OWASP:** A07:2021 — Identification and Authentication Failures
- **Remediation:** Upgrade to 6 digits (1,000,000 combinations), add cumulative lockout.

### SEC-007 — OTP Dev Bypass Exposed in Production
- **File:** `backend/routes/auth.py`
- **Issue:** `if not otp_record and code == '1234':` has no environment gate. The "1234" bypass accepted in production is an authentication backdoor.
- **OWASP:** A07:2021
- **Remediation:** Gate on `ENV != production`.

### SEC-008 — No OTP Cumulative Lockout
- **File:** `backend/routes/auth.py`
- **Issue:** slowapi limits to 10/minute per IP, but an attacker rotating IPs can attempt 600 guesses per hour indefinitely. No per-phone failure counter.
- **OWASP:** A07:2021
- **Remediation:** In-memory failure tracker: 5 failures/hour → 24h lockout with `Retry-After` header.

### SEC-009 — Race Condition: Dual Ride Acceptance
- **File:** `backend/routes/drivers.py:905-913` (accept_ride endpoint)
- **Issue:** Non-atomic status check + update. Two drivers can simultaneously pass the status check and both call `update_one()`, the second overwriting the first. Both drivers believe they have the ride.
- **CWE:** CWE-362 — Concurrent Execution using Shared Resource with Improper Synchronization (Race Condition)
- **Impact:** Dual ride acceptance, inconsistent database state, phantom driver assignments, rider confusion.
- **Remediation:** Conditional update with `{'status': 'searching'}` filter (optimistic locking); 409 + WebSocket notification to losing driver.

### SEC-010 — Firebase Credentials Committed to Git History
- **Files:** `driver-app/google-services.json`, `driver-app/GoogleService-Info.plist`, `rider-app/google-services.json`, `rider-app/GoogleService-Info.plist`
- **Issue:** Firebase project credentials are committed in the git history. Even if removed from HEAD, they remain recoverable via `git log`.
- **OWASP:** A02:2021 — Cryptographic Failures
- **Impact:** Firebase project access, FCM message injection, Crashlytics data access.
- **Remediation:** Owner must rotate all Firebase project keys in Firebase Console; add to `.gitignore`.

### SEC-011 — No Secrets Scanning in CI/CD
- **File:** `.github/workflows/ci.yml`
- **Issue:** No TruffleHog, Gitleaks, or equivalent. Secrets committed to the repo are not detected before merge.
- **OWASP:** A09:2021
- **Remediation:** Add TruffleHog as the first CI job (block merge on detection).

### SEC-012 — Trivy Container Scan Not Enforced
- **File:** `.github/workflows/ci.yml`
- **Issue:** Trivy runs but `exit-code: 0` — critical CVEs pass CI silently.
- **Remediation:** Set `exit-code: 1` for CRITICAL and HIGH findings.

### SEC-013 — No Pre-commit Hooks for Secret/PII Detection
- **Issue:** Nothing prevents a developer from committing a secret, phone number, or hardcoded credential to the repository.
- **Remediation:** Pre-commit hooks for TruffleHog patterns, PII (phone regex), money arithmetic (float), branch name enforcement.

### INF-001 — No Dependabot / Automated Dependency Updates
- **File:** No `.github/dependabot.yml`
- **Issue:** Known CVEs in dependencies are never automatically flagged or updated.
- **Remediation:** Add Dependabot for pip, npm (4 workspaces), and GitHub Actions.

### INF-002 — No CODEOWNERS
- **Issue:** Any contributor can merge changes to critical files without required review.
- **Remediation:** `.github/CODEOWNERS` mapping critical paths to required reviewers.

### INF-003 — No PR Template
- **Issue:** PRs have no standard checklist; security/testing items are routinely skipped.
- **Remediation:** `.github/PULL_REQUEST_TEMPLATE.md` with security checklist.

### INF-004 — Python Version Mismatch in CI
- **File:** `.github/workflows/ci.yml`
- **Issue:** CI runs Python 3.11 but production/local uses 3.12. Dependency resolution differences can mask bugs.
- **Remediation:** Pin to `python-version: '3.12'`.

### CQ-001 — No Audit / Security Event Logging
- **Issue:** Authentication failures, OTP events, admin access, and session invalidations produce no structured log output. Incident investigation is blind.
- **OWASP:** A09:2021 — Security Logging and Monitoring Failures
- **Remediation:** `utils/audit_logger.py` with `SecurityEvent` constants; log all auth lifecycle events.

### CQ-002 — Duplicate Push Token Registration Systems
- **Files:** `driver-app/app/_layout.tsx` (FCM token → `/notifications/register-token`), `driver-app/hooks/useDriverDashboard.ts` (Expo token → `/drivers/push-token`)
- **Issue:** Two conflicting push token systems. The backend sends FCM messages using `users.fcm_token`, but the Expo push token is the wrong type for FCM. Drivers on some devices receive no push notifications.
- **Remediation:** Remove Expo push token registration; FCM token in `_layout.tsx` is authoritative.

### CQ-003 — Post-Update Read-Back Verification Anti-Pattern
- **File:** `backend/routes/drivers.py:921-939`
- **Issue:** After updating a ride status, the code immediately re-reads the row to check if the update "landed." This is a symptom of the non-atomic update bug, not a fix. It adds a redundant DB round-trip and a potential TOCTOU window.
- **Remediation:** Fix the underlying race condition; remove the verification block.

### CQ-004 — CORS Exception Handler Missing Security Headers
- **File:** `backend/core/middleware.py`
- **Issue:** Custom exception handler returns CORS headers using `allow_methods=["*"]` and `allow_headers=["*"]`, bypassing the hardened CORS configuration for error responses.
- **Remediation:** Use the same explicit `_ALLOWED_METHODS` and `_ALLOWED_HEADERS` constants in exception handler.

### MOB-002 — In-App Navigation Launches External App
- **File:** `driver-app/components/dashboard/ActiveRidePanel.tsx:114-125`
- **Issue:** `openMapsNavigation()` uses `Linking.openURL()` to leave the Spinr app for Google Maps / Apple Maps. Driver loses context, ride timer continues, OTP entry is blocked.
- **Remediation:** In-app route overlay using Google Directions API + `<Polyline>` on existing `MapView`.

### MOB-003 — No Earnings Export
- **File:** `driver-app/app/driver/earnings.tsx`
- **Issue:** Earnings screen displays data but has no export capability. Drivers cannot submit earnings records for tax purposes (critical for gig economy compliance in Canada).
- **Remediation:** CSV export via `expo-file-system` + `expo-sharing`.

### MOB-004 — WebSocket Reconnection Verified but No Ride-Taken Notification
- **File:** `driver-app/hooks/useDriverDashboard.ts`
- **Issue:** WebSocket reconnect is implemented. However, when a race condition causes a driver to "lose" a ride accept, there is no client-side handler for a `ride_taken` event — the driver app shows no feedback.
- **Remediation:** Add `ride_taken` case to WebSocket message handler.

### FEAT-001 — No SOS / Emergency Button (Rider App)
- **Issue:** No rider-side emergency contact or SOS functionality. Required for ride-sharing safety compliance in most Canadian provinces.

### FEAT-002 — No Scheduled Rides
- **Issue:** Riders cannot book rides in advance. Competitive disadvantage vs. Lyft/Uber.

### FEAT-003 — No Ride Receipts
- **Issue:** No post-trip receipt sent to rider. Required for expense claims, regulatory compliance, and customer satisfaction.

### FEAT-004 — No Surge Pricing / Dynamic Fare Model
- **Issue:** Flat fare model with no demand-based pricing. Revenue optimization gap.

### FEAT-005 — Admin Dashboard: No Real-Time Fleet Map
- **Issue:** Admin cannot see live driver positions on a map. Essential for dispatch monitoring and incident response.

---

## P2 — Medium (Fix Before Public Beta)

### SEC-014 — JWT Expiry Too Long (30 Days)
- Session tokens valid for 30 days increase the window for credential theft exploitation.

### SEC-015 — No Token Refresh Mechanism
- Long-lived tokens with no refresh means revoked tokens remain usable for up to 30 days.

### SEC-016 — OTP Storage in Plain Text
- OTP codes stored as plain strings in `otp_records` table. Should be hashed (bcrypt/sha256).

### SEC-017 — No Input Sanitization on Address Fields
- Pickup/dropoff address fields accept arbitrary strings fed into DB queries without validation.

### SEC-018 — Stripe Webhook Missing Idempotency Check
- Payment webhook handler does not verify `stripe-signature` idempotency key; replay attacks possible.

### SEC-019 — Rate Limiter Uses IP (Easily Bypassed)
- slowapi `get_remote_address` is trivially bypassed with VPN/proxy rotation. Should supplement with phone-based rate limiting.

### SEC-020 — No HTTPS Enforcement in Backend
- No HSTS header, no redirect from HTTP to HTTPS at the application layer.

### INF-005 — No Environment-Specific Configuration Validation
- No validation that production env vars are set before startup (partially fixed for JWT_SECRET; needs broader coverage).

### INF-006 — No Container Image Signing
- Docker images are not signed; supply chain attack vector.

### INF-007 — No Blue/Green or Canary Deployment
- All deployments are big-bang; a bad deploy takes down 100% of traffic.

### CQ-005 — `current_session_id` Mechanism Is In-Memory Risk
- Session IDs stored in `users` table but not in Redis. Multi-instance deployments can serve stale tokens until DB sync.

### CQ-006 — No Correlation ID / Request Tracing
- No `X-Request-ID` propagation through backend → database → response. Incident investigation is difficult.

### CQ-007 — Error Messages Leak Internal Details
- Several endpoints return raw Python exception messages in HTTP 500 responses.

### CQ-008 — `datetime.utcnow()` Deprecated
- Python 3.12 deprecates `datetime.utcnow()`; should use `datetime.now(timezone.utc)`.

### CQ-009 — Float Arithmetic for Money
- Several fare calculations use Python `float`. Financial calculations must use `Decimal` to prevent rounding errors.

### MOB-005 — No Offline Mode / Request Queuing
- Driver app makes live API calls with no offline fallback; poor network = app failure.

### MOB-006 — No Crashlytics Non-Fatal Error Reporting
- Crashlytics is initialized but non-fatal errors are not reported via `recordError()`.

### MOB-007 — Location Permissions Not Requested on iOS Background
- App does not request `always` location permission for background tracking on iOS.

### TST-001 — No Backend Unit Tests
- `tests/` directory exists but contains no meaningful test coverage for auth, fare calculation, or ride state machine.

### TST-002 — No Mobile Integration Tests
- No Detox or Maestro E2E tests for critical flows (login → ride request → ride complete).

### TST-003 — No Load / Concurrency Tests
- Race conditions and DB bottlenecks untested under concurrent load.

### AI-001 — No Demand Forecasting
- No ML model to predict high-demand zones and pre-position drivers.

### COM-001 — No PIPEDA Privacy Notice at Data Collection
- Canadian PIPEDA requires explicit consent and privacy disclosure at the point of personal data collection. No in-app disclosure exists.

---

## P3 — Low (Post-Launch Hardening)

### DOC-001 — No API Documentation
- No OpenAPI/Swagger spec or Postman collection for the backend API.

### DOC-002 — No Incident Response Runbook
- No documented procedure for security incidents, outages, or data breaches.

### DOC-003 — No Architecture Decision Records (ADRs)
- Key design decisions (Supabase vs. self-hosted Postgres, Expo vs. bare RN) are undocumented.

---

## Appendix: File-Level Risk Summary

| File | Risk Level | Primary Issues |
|------|-----------|----------------|
| `backend/core/config.py` | 🔴 Critical | SEC-001, SEC-002, SEC-003 |
| `backend/dependencies.py` | 🔴 Critical | SEC-005, SEC-006 |
| `backend/routes/auth.py` | 🔴 High | SEC-007, SEC-008 |
| `backend/routes/drivers.py` | 🔴 High | SEC-009, CQ-003 |
| `backend/core/middleware.py` | 🟠 High | SEC-003, CQ-004 |
| `.github/workflows/ci.yml` | 🟠 High | SEC-011, SEC-012, INF-004 |
| `driver-app/app/_layout.tsx` | 🟠 High | MOB-001, CQ-002 |
| `driver-app/hooks/useDriverDashboard.ts` | 🟡 Medium | CQ-002 |
| `driver-app/components/dashboard/ActiveRidePanel.tsx` | 🟡 Medium | MOB-002 |
| `driver-app/app/driver/earnings.tsx` | 🟡 Medium | MOB-003 |

---

*Document version 1.0 — Audit baseline. Updated after each sprint to reflect remediation status.*
