# Security Findings & Remediation — Spinr

This document lists actionable security findings, explanations, and remediation steps prioritized by impact.

1. JWT Secret fallback and handling (High)
   - Finding: `server.py` uses a weak default fallback: `spinr-secret-key-change-in-production`.
   - Risk: Predictable keys allow token forging.
   - Remediation: Fail fast if `JWT_SECRET` not set; require a strong secret (32+ chars). Use secret manager (AWS Secrets Manager / GCP Secret Manager / Vault). Rotate keys and support token blacklist on rotation.

2. Long-lived access tokens (High)
   - Finding: Tokens expire in ~30 days.
   - Risk: Long-lived tokens increase window for misuse if leaked.
   - Remediation: Use short-lived access tokens (15–60 minutes) + refresh tokens (secure, revocable). Store refresh tokens server-side or in HttpOnly secure cookies. Implement revocation (token blacklist).

3. OTP abuse & brute force (High)
   - Finding: OTP endpoints lack rate-limiting and abuse protections.
   - Risk: SMS costs, account enumeration, and brute force.
   - Remediation: Add per-phone and per-IP rate-limits, exponential backoff, one-time-use OTPs, and attempt counters. Consider CAPTCHA for suspicious requests.

4. WebSocket authentication & authorization (High)
   - Finding: WS endpoint accepts connections then uses messages; handshake doesn't validate token.
   - Risk: Unauthorized connections can listen or inject messages.
   - Remediation: Require Authorization header or token query param validated in handshake; reject unauthorized connections. Use per-connection scopes and channel authorization (rider_{id}, driver_{id}) and TTL.

5. Client storage and web fallback (High)
   - Finding: `expo-secure-store` used for native; web falls back to `localStorage` in `authStore.ts`.
   - Risk: `localStorage` is vulnerable to XSS and token theft.
   - Remediation: Use secure, HttpOnly cookies for web or tokenless session via short-lived cookies and refresh flows. Harden frontend against XSS, CSP headers, and Sentry for monitoring.

6. CORS and allowed origins (High)
   - Finding: No visible CORS allowlist in `server.py`.
   - Risk: Cross-origin requests from malicious sites.
   - Remediation: Configure `CORSMiddleware` with an explicit allowed origins list per environment. Disallow credentials unless required.

7. Input validation and injection (High)
   - Finding: Some DB operations take user-provided fields; careful validation required.
   - Risk: Injection, malformed data, runtime errors.
   - Remediation: Validate all external input with Pydantic models; sanitize strings and enforce types. Validate ObjectId formats before using them.

8. Secrets in settings & admin fields (High)
   - Finding: `AppSettings` contains fields `stripe_secret_key` and `google_maps_api_key`.
   - Risk: Secrets stored in DB or exposed via API could leak sensitive keys.
   - Remediation: Never expose secret keys via public API. Store secrets in env/secret manager. For publishable keys (Stripe publishable key) it's fine client-side.

9. Dependency and supply-chain management (High)
   - Finding: Large dependency lists in `requirements.txt` and `package.json`.
   - Risk: Known CVEs in deps.
   - Remediation: Run `pip-audit`, `safety`, `npm audit`; enable Dependabot/Renovate; pin patch versions and test updates. Consider SBOM generation.

10. Rate limiting and abuse protection (Medium)
    - Finding: No global rate-limiter.
    - Remediation: Add API throttling (Redis-backed token bucket), set per-endpoint limits for heavy operations.

11. Logging & PII handling (Medium)
    - Finding: Logging configured; may log sensitive data.
    - Remediation: Redact PII and tokens from logs, use structured logging, centralize logs with access controls and retention policies.

12. Database security & least privilege (Medium)
    - Finding: Mongo URL from env but no details about user privileges.
    - Remediation: Use least-privilege DB users with separate users for read-only analytics. Enforce TLS for Mongo connections and IP allowlisting.

13. TLS, HSTS, and secure headers (Medium)
    - Finding: No deployment-level TLS in repo.
    - Remediation: Enforce TLS/WSS using reverse proxy or managed service; enable HSTS, CSP, and other secure headers via middleware.

14. Stripe & payment security (High)
    - Finding: Stripe SDK present; ensure Payment Intents and webhook signature verification used.
    - Remediation: Implement Payment Intents server-side, verify webhook signatures (`stripe-signature`), store minimal payment metadata, and scope secrets to server only.

15. File uploads and driver verification (Medium)
    - Finding: Driver verification required but no upload handling found.
    - Remediation: Use presigned uploads (S3) with server-side validation, virus scanning, and expiry. Keep uploads out of app server storage.

16. Admin endpoints & RBAC (Medium)
    - Finding: `admin_router` exists but needs RBAC controls.
    - Remediation: Implement role-based access control for admin endpoints, require strong MFA for admin accounts.

17. CI/CD & secrets in pipeline (Low)
    - Finding: No CI/CD manifests.
    - Remediation: Add CI pipeline checks for secrets scanning, dependency scanning, tests, and deploy pipelines that inject secrets from secret manager.

## Quick Implementation Checklist (priority order)
- Fail-fast on missing `JWT_SECRET` and rotate keys.  
- Implement short-lived access tokens + refresh tokens.  
- Add WS handshake auth and reject unauthenticated sockets.  
- Add per-endpoint rate limits (especially OTP endpoints).  
- Move any secret keys out of DB and into secret manager.  
- Replace `localStorage` usage on web with secure cookie flows.  
- Enable CORS allowlist and secure headers.  
- Enable dependency scanning and configure automatic PRs for security patches.

