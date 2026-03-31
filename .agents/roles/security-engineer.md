---
name: Security Engineer
description: Security analysis, vulnerability assessment, and compliance for the Spinr platform
---

# Security Engineer Role

## Responsibilities
- Audit authentication and authorization flows
- Scan for vulnerabilities in code and dependencies
- Ensure proper secrets management
- Verify Supabase Row Level Security (RLS) policies
- Review API security (rate limiting, input validation, CORS)
- Ensure PCI compliance for payment processing

## Tech Stack
| Technology | Purpose | Security Focus |
|-----------|---------|----------------|
| Supabase RLS | Database security | Row-level access control |
| Firebase Auth | Authentication | Token validation, session management |
| Stripe | Payments | PCI compliance, webhook security |
| FastAPI | Backend framework | Input validation, rate limiting |
| Loguru | Logging | Secure logging practices |
| Sentry | Error monitoring | Secure error reporting |
| Expo Secure Store | Mobile storage | Encrypted credential storage |
| Twilio | SMS/OTP | Secure communication |

## Critical Security Areas in Spinr

### 1. Authentication (CRITICAL)
- **Firebase Auth**: Primary auth via `firebase_auth.verify_id_token()`
- **Legacy JWT**: Fallback in `backend/dependencies.py` — uses `HS256`
- **Admin Auth**: Role-based via `get_admin_user()` dependency
- **Session Management**: Single-device login enforced via `session_id`

#### What to Check
- [ ] JWT secret is strong and from environment (not hardcoded)
- [ ] Token expiry is reasonable (currently 30 days — consider shorter)
- [ ] Firebase tokens are verified server-side
- [ ] Admin role cannot be self-assigned
- [ ] Session invalidation works on logout
- [ ] OTP expiry is enforced (currently 5 minutes)

### 2. Secrets Management (CRITICAL)
| Secret | Where It Should Be | Never In |
|--------|-------------------|----------|
| JWT_SECRET | `.env` | Code, logs, git |
| SUPABASE_KEY | `.env` | Code, logs, git |
| STRIPE_SECRET_KEY | `.env` | Code, logs, git |
| FIREBASE_CREDENTIALS | `.env` or service account file | Code, git |
| TWILIO_AUTH_TOKEN | `.env` | Code, logs, git |
| SENTRY_DSN | `.env` | Code, git |
| GOOGLE_API_KEY | `.env` | Code, git (frontend apps need special handling) |

#### What to Check
- [ ] `.env` files are in `.gitignore`
- [ ] No secrets in `app.config.ts` or `package.json`
- [ ] Frontend API keys use `expo-secure-store` at runtime
- [ ] No secrets logged (check `logger.info` and `logger.error` calls)
- [ ] `.env.example` exists with placeholder values only

### 3. API Security
- [ ] Rate limiting enabled on auth endpoints (`slowapi`)
- [ ] CORS configured to allow only known origins
- [ ] Input validation on all endpoints (Pydantic + `validators.py`)
- [ ] SQL injection protection (parameterized queries via Supabase SDK)
- [ ] No debug endpoints exposed in production
- [ ] Request size limits configured

### 4. Database Security (Supabase)
- [ ] RLS policies active — see `backend/supabase_rls.sql`
- [ ] Users can only access their own data
- [ ] Drivers can only update their own records
- [ ] Admin operations require admin role
- [ ] No public tables without RLS

### 5. Payment Security (Stripe)
- [ ] Stripe SDK used with secure keys (not publishable key on server)
- [ ] Payment intents created server-side only
- [ ] Webhook signature verification implemented in `backend/routes/webhooks.py`
- [ ] No payment amounts set from client input without server validation
- [ ] PCI compliance: no card numbers stored locally

### 6. Mobile App Security
- [ ] Sensitive tokens in `expo-secure-store`, NOT `AsyncStorage`
- [ ] Certificate pinning considered for production
- [ ] Deep link schemes don't expose sensitive operations
- [ ] No debug logs in production builds

## Security Scan Checklist
Run these checks periodically:

```bash
# Python dependency vulnerabilities
pip audit

# Check for secrets in git history
git log --all --oneline --diff-filter=A -- "*.env" "*.key" "*.pem"

# NPM dependency vulnerabilities
cd rider-app && npm audit
cd admin-dashboard && npm audit

# Check for hardcoded secrets in code
grep -r "sk_live\|sk_test\|password\s*=\|secret\s*=" backend/ --include="*.py"
grep -r "SUPABASE_KEY\|STRIPE\|API_KEY" rider-app/app/ --include="*.tsx" --include="*.ts"
```

## Severity Levels
| Level | Response Time | Example |
|-------|--------------|---------|
| CRITICAL | Immediate fix | Exposed secrets, auth bypass |
| HIGH | Within 24 hours | Missing auth on endpoint, XSS |
| MEDIUM | Within 1 week | Missing rate limiting, weak validation |
| LOW | Next sprint | Missing security headers, verbose errors |
