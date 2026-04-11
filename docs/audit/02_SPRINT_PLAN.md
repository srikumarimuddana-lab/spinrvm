# Spinr — Strategic Security & Hardening Sprint Plan

**Plan Date:** 2026-04-07
**Planning Basis:** 55-issue Fortune 100 audit (see `01_AUDIT_GAPS_REPORT.md`)
**Repository:** `ittalenthireca-sketch/Spinr`
**Sprint Length:** 1 week per sprint (4 independent branches per sprint, 1 PR per branch)
**Branching Strategy:** All sprint branches cut from `main`; each is independently mergeable

---

## Strategic Principles

1. **Security-first, then features.** All P0 issues must be resolved before any feature work ships to production users.
2. **Independent branches per concern.** No sprint branch depends on another in the same sprint. Any branch can be cherry-picked, reverted, or delayed without affecting the others.
3. **No upstream pollution.** All work stays in `ittalenthireca-sketch/Spinr`. The `srikumarimuddana-lab/spinr` upstream is synced read-only on a daily schedule.
4. **Audit repo never deploys.** All CI deploy jobs are permanently disabled (`if: false`) in the audit repo to prevent accidental production deployments.
5. **Pre-commit hooks as guardrails.** Every commit is scanned for secrets, PII in logs, float money arithmetic, and branch naming before being accepted.

---

## Sprint 1 — CI/CD Hardening & Infrastructure Security
**Priority:** P0/P1 infrastructure gaps
**Rationale:** Fixing the pipeline first ensures that all subsequent security fixes are protected by the CI/CD guardrails before they reach the repo. A secure pipeline is the foundation for everything else.

### Branch 1: `sprint1/cicd-hardening`
**Files:** `.github/workflows/ci.yml`, `.gitignore`, `.claude/launch.json`

| Task | Issue | What |
|------|-------|------|
| Add TruffleHog secrets scan as first CI job | SEC-011 | Block merge if any secret pattern detected |
| Fix Trivy to `exit-code: 1` on CRITICAL/HIGH | SEC-012 | Enforce container vulnerability gates |
| Pin Python version to 3.12 | INF-004 | Match production environment |
| Disable all deploy jobs with `if: false` | Audit repo safety | Prevent accidental production deploys |
| Add `.gitignore` hardening | SEC-010 | Block credentials/key files, OS artifacts, editor files |
| Add `.claude/launch.json` | DX | 6 dev server configs for preview |

### Branch 2: `sprint1/backend-security`
**Files:** `backend/core/config.py`, `backend/hooks/pre-commit`

| Task | Issue | What |
|------|-------|------|
| Add pre-commit hooks | SEC-013 | 5-check suite: secrets scan, forbidden files, PII in logs, branch check, money arithmetic |
| Remove default credentials from config | SEC-001, SEC-002, SEC-003 | Zero defaults for JWT_SECRET, ADMIN_EMAIL, ADMIN_PASSWORD, ALLOWED_ORIGINS |

### Branch 3: `sprint1/admin-hardening`
**Files:** `admin-dashboard/src/middleware.ts`, `admin-dashboard/src/components/session-manager.tsx`

| Task | Issue | What |
|------|-------|------|
| Admin session timeout | INF-002 | Auto-logout after 30 min inactivity |
| Rate limiting on admin login | SEC-019 | Protect admin credentials endpoint |

### Branch 4: `sprint1/audit-repo-setup`
**Files:** `.github/CODEOWNERS`, `.github/PULL_REQUEST_TEMPLATE.md`, `.github/dependabot.yml`

| Task | Issue | What |
|------|-------|------|
| Add CODEOWNERS | INF-002 | Require review on critical security files |
| Add PR template | INF-003 | Standardize security checklist for all PRs |
| Add Dependabot | INF-001 | Weekly automated dependency updates for pip + 4x npm + actions |

**Sprint 1 Outcome:** Secure pipeline, zero-default config, automated dependency tracking, code ownership enforced.

---

## Sprint 2 — Authentication & Secrets Hardening
**Priority:** P0/P1 auth security gaps
**Rationale:** Authentication is the primary security boundary. JWT secret leakage and OTP weaknesses must be addressed before any public traffic.

### Branch 1: `sprint2/auth-secrets-hardening`
**Files:** `backend/dependencies.py`, `backend/routes/auth.py`, `backend/core/config.py`

| Task | Issue | What |
|------|-------|------|
| Remove JWT_SECRET from ALL log lines | SEC-005 | Remove token prefix log on create; remove full secret log on failure |
| Add production startup guard | SEC-001 | `RuntimeError` if JWT_SECRET absent or < 32 chars |
| Upgrade OTP from 4 to 6 digits | SEC-006 | 10,000 → 1,000,000 combinations |
| Gate dev OTP bypass behind `ENV != production` | SEC-007 | Ensure "1234" bypass never works in production |
| Remove credential defaults from config | SEC-001/002/003 | Empty strings for all secrets; validator warns in production |

### Branch 2: `sprint2/cors-hardening`
**Files:** `backend/core/middleware.py`

| Task | Issue | What |
|------|-------|------|
| Explicit CORS origin allowlist | SEC-003 | Production: only `https://spinr-admin.vercel.app`; dev adds localhost |
| `RuntimeError` if wildcard in production | SEC-003 | Hard fail at startup if `*` detected |
| Restrict methods to explicit list | CQ-004 | `["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]` |
| Restrict headers to explicit list | CQ-004 | `["Authorization", "Content-Type", "Accept", "X-Request-ID", "X-Requested-With"]` |
| Fix exception handler CORS headers | CQ-004 | Use same explicit lists in error responses |

### Branch 3: `sprint2/security-logging`
**Files:** `backend/utils/audit_logger.py` (new), `backend/dependencies.py`, `backend/routes/auth.py`

| Task | Issue | What |
|------|-------|------|
| Create `audit_logger.py` | CQ-001 | `SecurityEvent` constants + `log_security_event()` helper |
| Log all auth events | CQ-001 | AUTH_NO_TOKEN, AUTH_FAILED, AUTH_SESSION_MISMATCH, ADMIN_ACCESS_DENIED |
| Log all OTP events | CQ-001 | OTP_SENT, OTP_SEND_FAILED, OTP_VERIFIED, OTP_INVALID, OTP_EXPIRED |
| PII masking in all logs | CQ-001 | Phone numbers: last-4 digits only throughout |

**Sprint 2 Outcome:** JWT secret never logged, OTP 6-digit, dev bypass production-gated, CORS hardened, all auth/OTP events auditable.

---

## Sprint 3 — Production Reliability & Driver App
**Priority:** P0 MOB-001, P1 SEC-008/009, P1 MOB-002/003
**Rationale:** Sprint 3 addresses the remaining P0 (driver push notifications), two P1 backend reliability issues (OTP lockout, race condition), and two P1 driver UX gaps (navigation, earnings export).

### Branch 1: `sprint3/driver-background-push`
**Files:** `driver-app/app/_layout.tsx`, `driver-app/hooks/useDriverDashboard.ts`, `shared/services/firebase.ts`

| Task | Issue | What |
|------|-------|------|
| `setBackgroundMessageHandler` at module scope | MOB-001 | OS-level handler for data-only FCM when app is killed/backgrounded |
| `getInitialNotification` in root component | MOB-001 | Route driver to `/driver` when app opened from killed-state tap |
| `onNotificationOpenedApp` in root component | MOB-001 | Route driver to `/driver` when app foregrounded from background tap |
| Remove duplicate Expo push token setup | CQ-002 | Expo token (`getExpoPushTokenAsync`) is wrong type; FCM token is authoritative |

### Branch 2: `sprint3/otp-lockout`
**Files:** `backend/routes/auth.py`, `backend/utils/audit_logger.py`

| Task | Issue | What |
|------|-------|------|
| In-memory failure tracker per phone | SEC-008 | `_otp_failures: Dict[str, List[float]]` |
| Lockout after 5 failures/hour | SEC-008 | 24-hour lockout; `Retry-After` header in 429 response |
| `check_otp_lockout()` | SEC-008 | Called at top of verify-otp before any DB lookup |
| `record_otp_failure()` | SEC-008 | Called on invalid code + expired OTP; fires `OTP_LOCKOUT_TRIGGERED` audit event |
| `clear_otp_failures()` | SEC-008 | Called on successful verification to reset counter |

### Branch 3: `sprint3/race-condition-fix`
**Files:** `backend/routes/drivers.py`

| Task | Issue | What |
|------|-------|------|
| Conditional DB update (optimistic locking) | SEC-009 | `{'id': ride_id, 'status': 'searching'}` filter — atomic compare-and-swap |
| 409 response for losing driver | SEC-009 | HTTP 409 with clear error message |
| WebSocket `ride_taken` event | MOB-004 | Notify losing driver's app immediately |
| Audit log for race loss | CQ-001 | `RIDE_ACCEPT_RACE_LOST` event |
| Remove post-update verification block | CQ-003 | Redundant read-back is now unnecessary |

### Branch 4: `sprint3/driver-app-features`
**Files:** `driver-app/components/dashboard/ActiveRidePanel.tsx`, `driver-app/app/driver/index.tsx`, `driver-app/app/driver/earnings.tsx`

| Task | Issue | What |
|------|-------|------|
| In-app navigation mode | MOB-002 | Toggle in ActiveRidePanel; Directions API + `<Polyline>` on MapView |
| `decodePolyline()` helper | MOB-002 | Google encoded polyline decoder (no new packages) |
| "Open in Maps" as secondary fallback | MOB-002 | Keep external nav as option |
| Earnings CSV export button | MOB-003 | `expo-file-system` write + `expo-sharing` share sheet |
| Period-labelled filename | MOB-003 | `spinr-earnings-this-week.csv` |

**Sprint 3 Outcome:** Drivers receive ride offers from any app state; sustained OTP brute-force blocked; ride acceptance is atomic; drivers navigate in-app and export earnings.

---

## Sprint 4 — Planned (Backlog)
**Priority:** Remaining P1 issues and observability

| Branch | Focus | Key Items |
|--------|-------|-----------|
| `sprint4/redis-rate-limiting` | Infrastructure | Redis-backed OTP failure counter (replaces in-memory); rate limiting across instances |
| `sprint4/observability` | Monitoring | Sentry error tracking, APM (response times, DB query counts), structured JSON logging |
| `sprint4/firebase-key-rotation` | Security | Owner rotates Firebase credentials; new `google-services.json` files replace committed ones |
| `sprint4/rider-app-safety` | Features | SOS button, trip sharing, emergency contact; PIPEDA disclosure screen |

## Sprint 5 — Planned (Backlog)

| Branch | Focus | Key Items |
|--------|-------|-----------|
| `sprint5/scheduled-rides` | Features | Rider can book rides in advance; driver notification system |
| `sprint5/ride-receipts` | Features | Post-trip email/PDF receipt; Stripe invoice integration |
| `sprint5/admin-fleet-map` | Features | Real-time driver positions on admin map |
| `sprint5/docker-security` | Infrastructure | Non-root user, `.dockerignore`, `HEALTHCHECK`, image signing |

---

## Branching Convention

```
sprint{N}/{concern}          # Feature/fix branches
docs/{topic}                 # Documentation branches
hotfix/{issue-id}            # Emergency production fixes
chore/{task}                 # Non-code changes (config, docs, deps)
```

## PR Naming Convention

```
feat({scope}): short description [Sprint N #M]
fix({scope}): short description [Sprint N #M]
chore({scope}): short description
docs({scope}): short description
```

## Merge Order

Sprints are independent — branches within a sprint can be merged in any order. However, the recommended sequence is security → infrastructure → features, to ensure that hardening changes are in `main` before feature code that depends on them.

---

*Document version 1.0 — Created 2026-04-09. Updated after each sprint completion.*
