# Dimension 11 — Security Headers, CORS & CI Pipeline

**Question:** Are the API doors locked? Does CI catch secrets and CVEs before they ship?

---

## Checklist

### CORS
- [ ] No wildcard `*` origin in production
- [ ] Origin allowlist explicitly defined
- [ ] Allowlist enforced via environment variable (not hardcoded)
- [ ] Preflight OPTIONS request handled correctly
- [ ] CORS headers present on both success and error responses

### Security Headers (verify all are set)
- [ ] `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
- [ ] `Content-Security-Policy` — defined and appropriate for API (usually `default-src 'none'`)
- [ ] `X-Frame-Options: DENY`
- [ ] `X-Content-Type-Options: nosniff`
- [ ] `Referrer-Policy: no-referrer`
- [ ] `Permissions-Policy: geolocation=(), camera=(), microphone=()`
- [ ] `Cross-Origin-Resource-Policy: same-origin`
- [ ] `Cross-Origin-Opener-Policy: same-origin`

### Rate Limiting
- [ ] Rate limits configured for all sensitive endpoints:
  - OTP send: 3/min
  - OTP verify: 5/min
  - Login: 5/min
  - Ride creation: 10/min
  - Location update: 60/min
- [ ] Rate limiter reads `X-Forwarded-For` (not direct TCP IP) — works behind CDN/proxy
- [ ] Trusted proxy IPs configured to prevent spoofing
- [ ] Redis-backed rate limiting — not per-worker in-process (shared across all workers)
- [ ] Redis unavailability triggers a fallback — not silent bypass

### CI/CD Security
- [ ] TruffleHog secrets scan on every PR (`--only-verified --fail`)
- [ ] Trivy CVE scan blocks CRITICAL and HIGH vulnerabilities on Docker push
- [ ] `pip-audit` or `safety` scans Python dependencies for CVEs
- [ ] `npm audit` scans Node/React Native dependencies
- [ ] No secrets in CI environment variables printed to logs
- [ ] CI runs on every PR — cannot merge without passing checks

### Startup Validation
- [ ] App refuses to start if JWT_SECRET is absent or too short
- [ ] App refuses to start if Supabase URL is a placeholder
- [ ] App refuses to start if Stripe key is absent (in production profile)
- [ ] Startup validation logs warnings — never echoes the secret values

---

## Severity Guide

| Finding | Severity |
|---|---|
| CORS wildcard in production | HIGH |
| HSTS missing — HTTPS downgrade possible | HIGH |
| Rate limiting bypassed behind CDN (direct IP used) | HIGH |
| Rate limiting silently disabled when Redis is down | HIGH |
| No secrets scanning in CI | HIGH |
| No CVE scanning in CI | HIGH |
| CSP missing — XSS attacks unmitigated | MEDIUM |
| X-Frame-Options missing — clickjacking possible | MEDIUM |
| Startup doesn't validate secrets — weak JWT_SECRET accepted | HIGH |
| pip-audit missing from CI | RECOMMENDATION |
