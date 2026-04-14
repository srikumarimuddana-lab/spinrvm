# 02 — Security Audit

> **Read time:** ~20 min
> **Audience:** Security Engineer, Backend Lead
> **Threat model:** Consumer ride-share — handles PII, government ID, card data (via Stripe), GPS trails, SMS OTP.

---

## Executive verdict

Security posture is **B-minus** — better than most start-ups at this stage, but with two classes of P0 gaps: **(a) network-edge hardening** (no security headers, CORS wildcard risk, in-memory rate limiter) and **(b) identity/session lifecycle** (30-day JWT, no refresh, no revocation). Code-level defenses (Pydantic, magic-byte, bcrypt-12, Stripe sig verification) are solid.

---

## Findings

### P0-S1 — Missing security headers (backend)

**Evidence:** `backend/core/middleware.py` defines CORS + exception handler but **no** `Strict-Transport-Security`, `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`.

**Impact:** Admin dashboard (`/admin`) is **clickjackable**. Any browser request to the API responds over downgraded HTTP once the user has been MITM'd. Script injection on admin has no CSP backstop.

**Root cause:** Middleware module wasn't extended when the admin dashboard moved from a static Next.js export to dynamic pages.

**Permanent fix (S):**
```python
# backend/core/middleware.py
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=()"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: https:; "
            "connect-src 'self' https://api.stripe.com https://*.supabase.co wss:; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        return resp

# server.py
app.add_middleware(SecurityHeadersMiddleware)
```
Pair with a Next.js `middleware.ts` that sets matching CSP for `/admin/*` if the admin is served independently.

---

### P0-S2 — Rate limiter uses in-memory storage

**Evidence:** `backend/utils/rate_limiter.py` uses slowapi default `storage_uri="memory://"`.

**Impact:** `fly.toml` supports auto-scaling to N machines. Each instance maintains its own counter → effective ceiling is **N × admin-configured limit**. OTP endpoint is the worst-case: a bot can request floods of OTPs at N× the intended rate, driving Twilio spend + risking blocklisting. `auth/login` brute-force is similarly amplified.

**Root cause:** Default storage was never swapped.

**Permanent fix (S):** Add managed Redis (Upstash, Fly Redis, or Redis Cloud) and switch SlowAPI:
```python
from slowapi import Limiter
from slowapi.util import get_remote_address
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.getenv("RATE_LIMIT_REDIS_URL"),
    strategy="fixed-window-elastic-expiry",
)
```
Keep `memory://` only when `ENV != production`. Enforce via `_validate_production_config()`.

---

### P0-S3 — 30-day JWT with no refresh / revocation

**Evidence:** `backend/core/config.py::JWT_EXPIRATION_DAYS: int = 30`; `backend/dependencies.py` verifies token but has no deny-list lookup.

**Impact:** Stolen token = **30 days** of unauthorized access. Logout on the client does nothing server-side. Password change, dispute resolution, banning a driver — none of these invalidate an existing token.

**Root cause:** Single-token model, no refresh/access split.

**Permanent fix (M):**
1. Short-lived access token (15 min), long-lived refresh token (30 d, rotating).
2. Refresh token stored hashed in `refresh_tokens` table with `jti`, `user_id`, `device_id`, `expires_at`, `revoked_at`.
3. On every access-token issuance, check `jti` not in `revoked_jtis` (cache in Redis with TTL = access-token expiry).
4. Ban/password-change/logout writes to `revoked_jtis`.
5. Keep `session_id` single-device enforcement already implemented in `dependencies.py`.

---

### P0-S4 — Incomplete RLS coverage

**Evidence:** `backend/supabase_rls.sql` has policies for ~10 tables. `supabase_schema.sql` defines 30+ tables.

**Missing RLS on:** `payments`, `payment_methods`, `disputes`, `notifications`, `wallet`, `wallet_transactions`, `driver_earnings`, `quests`, `quest_progress`, `loyalty_points`, `corporate_accounts`, `corporate_members`, `driver_subscriptions`, `subscription_plans`, `promotions`, `promotion_redemptions`, `documents`, `document_reviews`, `driver_daily_stats`, `driver_activity_log`, `driver_notes`, `surge_pricing`, `scheduled_rides`, `fare_split_participants`, `chat_messages`, `gps_breadcrumbs`.

**Impact:** The only thing separating any authenticated user from every row in these tables is "the backend doesn't expose an endpoint for it." A single SSRF, a misconfigured RPC, or use of the anon key from a misdesigned client exposes PII, payment history, and GPS trails.

**Root cause:** RLS written opportunistically per feature, never audited holistically.

**Permanent fix (M):**
1. Adopt a **default-deny posture**: `ALTER TABLE … ENABLE ROW LEVEL SECURITY; REVOKE ALL FROM anon, authenticated;`
2. For every user-facing table, write a standard pair:
   - `USING ( user_id = auth.uid() )` SELECT
   - Admin bypass via `SECURITY DEFINER` SQL functions, never direct table access.
3. Write a CI test (Supabase SQL runner) that asserts `pg_class.relrowsecurity = true` for every table in `public`.
4. Add a periodic audit query: `SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT IN (SELECT tablename FROM pg_policies WHERE schemaname='public');`

---

### P0-S5 — Supabase service role key is the sole privilege boundary

**Evidence:** Backend uses `SUPABASE_SERVICE_ROLE_KEY` from env. No rotation workflow, no secrets-manager integration.

**Impact:** Key leak (log, crash dump, accidental repo commit) = total database compromise, bypassing all RLS.

**Root cause:** Standard Supabase bootstrapping not hardened for production scale.

**Permanent fix (M):**
1. Move secrets out of raw env into a secret manager (Fly secrets is OK short-term but rotation-unaware; Doppler or 1Password-Connect for audited rotation).
2. Quarterly rotation runbook (documented, rehearsed).
3. Narrow backend service role access via **PostgREST pre-request hook** or a purpose-built role that excludes destructive statements where possible.
4. CI secret scanning already exists (TruffleHog) — extend to pre-receive hook server-side.

---

### P1-S6 — CORS wildcard risk

**Evidence:** `backend/core/middleware.py::_validate_production_config()` rejects wildcard `*` in production for `ALLOWED_ORIGINS`. Good. But `ALLOWED_ORIGIN_REGEX` has no validator and is currently permissive.

**Impact:** Misconfigured regex (e.g., `.*\.spinr\.app$` without an anchor) lets attacker-controlled origins bypass CORS.

**Permanent fix (S):** Require regex to start with `^` and end with `$`; reject `.*` in prod unless explicitly allow-listed. Add unit test covering `foo.spinr.app.attacker.com` does not match.

---

### P1-S7 — OTP + login brute-force surface

**Evidence:** `routes/auth.py` issues OTPs via Twilio; rate limiter is per-IP, not per-phone-number. Same IP rotation from a mobile carrier's NAT makes IP limiting porous.

**Impact:** Attacker can request OTPs for victim's number at scale, both harassing the target and burning Twilio balance.

**Permanent fix (S):**
- Composite limiter: `min(ip_bucket, phone_bucket)`.
- Add short (60s) cooldown *per phone number* regardless of IP.
- Add exponential back-off: OTP #1 instant, #2 in 30s, #3 in 2m, #4 in 15m, then hard block.
- Log failed verify attempts for SIEM correlation.

---

### P1-S8 — SMS OTP delivered but fallback unclear

**Evidence:** Only Twilio SMS is implemented.

**Impact:** In regions where Twilio deliverability is weak (pre-paid MNOs, Canada/SK rural), users can't sign in. No WhatsApp/email fallback.

**Permanent fix (M):** Add email OTP or passkey as second factor. Provide a clear "didn't get code?" flow with escalation.

---

### P1-S9 — JWT claim forgery resistance only partial

**Evidence:** `dependencies.py` correctly **re-reads role from DB** on each request (great — prevents privilege-escalation JWTs). But `exp`, `iat`, `aud`, `iss` are verified by PyJWT with HS256. The signing key lives in one place (env).

**Impact:** Shared HS256 secret = whoever holds the key mints tokens. Asymmetric keys would let prod verify tokens minted only by the authoritative issuer.

**Permanent fix (M):** Move to RS256/EdDSA. Private key in vault, public key distributed to all services. Token rotation becomes a routine key-rollover ceremony.

---

### P1-S10 — File upload defense is partial

**Evidence:** `backend/documents.py` uses magic-byte checks (good — stops polyglot files). But no AV scan, no image re-encoding, no size-per-user quota.

**Impact:** Hosting user-supplied PDFs/images without sanitization = potential malware distribution, zip-bomb DoS, or PII leakage via EXIF.

**Permanent fix (M):**
- Strip EXIF from all image uploads.
- Re-encode images via Pillow/sharp (effectively neutralizes embedded scripts).
- Route all PDFs through ClamAV (Fly side-car or Lambda) before accepting.
- Per-user daily quota (e.g., 50 MB).

---

### P1-S11 — Secrets not segregated per environment

**Evidence:** Single `.env` example pattern documented; no clear separation between staging/prod keys.

**Impact:** Ops accidents (e.g., pointing staging backend at prod DB) are a live risk.

**Permanent fix (S):**
- Fly.io app per env: `spinr-backend-staging`, `spinr-backend-prod`.
- Supabase **separate projects** per env (not just separate schemas).
- CI deploy gated on env name matching target secret namespace.

---

### P2-S12 — No request signing between admin and backend

**Evidence:** Admin dashboard → backend uses bearer JWT over TLS only.

**Impact:** In a network-compromised scenario, request replay is unmitigated inside the TTL window.

**Permanent fix (L):** Optional. Add mTLS between admin and backend, or HMAC-signed request bodies for privileged routes.

---

### P2-S13 — Firebase App Check present but not enforced

**Evidence:** `@react-native-firebase/app-check` is in both apps' package.json. No backend verification of App Check tokens.

**Impact:** Anyone can hit the API from curl/postman; App Check provides zero defense at the server boundary.

**Permanent fix (S):** Require `X-Firebase-AppCheck` header on all mobile-originated routes; verify via Firebase Admin SDK. Soft-enforce for 2 weeks (log-only), then hard-enforce.

---

### P2-S14 — Sensitive PII in logs

**Evidence:** Loguru JSON format is structured but log calls like `logger.info(f"sms_send phone={phone}")` appear in `sms_service.py`.

**Impact:** Phone numbers, email, occasionally addresses land in log files with broader access than the DB.

**Permanent fix (S):** Introduce a `redact_pii()` helper used in a Loguru patcher. Mask phone to last-4 and email to `u***@domain`.

---

### P2-S15 — Password reset flow not verified

**Evidence:** Admin dashboard allows admin password reset. No rate limiting or audit trail observed for "forgot password" on user accounts (OTP covers this for riders/drivers, but admins use password).

**Permanent fix (S):** Enforce expiring one-time password-reset links (signed JWT with `type=pwd_reset`, 15 min TTL, single-use stored in `used_reset_tokens`).

---

### P3-S16 — No SECURITY.md / responsible disclosure

**Evidence:** No `SECURITY.md` in repo root.

**Permanent fix (S):** Add SECURITY.md with `security@spinr.app`, PGP key, expected response SLA, safe-harbor language.

---

## OWASP Top 10 summary

| OWASP 2021 | Status | Notes |
|---|---|---|
| A01 Broken Access Control | ⚠️ Partial | RLS incomplete; role-from-DB good |
| A02 Cryptographic Failures | ⚠️ HS256 | Move to RS256; bcrypt-12 good |
| A03 Injection | ✅ Good | Pydantic + parameterized queries |
| A04 Insecure Design | ⚠️ Partial | Webhook non-idempotent; single-token auth |
| A05 Security Misconfig | ❌ Bad | No security headers; memory RL; wildcard regex risk |
| A06 Vulnerable Components | ✅ Good | Dependabot + Trivy |
| A07 Auth Failures | ⚠️ Partial | 30d JWT, no revocation, OTP per-IP only |
| A08 Integrity Failures | ⚠️ Partial | No SLSA provenance; no SBOM attestation |
| A09 Logging/Monitoring | ⚠️ Partial | Sentry optional; no SIEM |
| A10 SSRF | ✅ Good | No user-driven fetch URLs |

---

## Priority summary

| # | ID | Severity | Effort | Owner |
|---|---|---|---|---|
| 1 | S1 | P0 | S | Backend |
| 2 | S2 | P0 | S | Backend + DevOps |
| 3 | S3 | P0 | M | Backend |
| 4 | S4 | P0 | M | DB + Backend |
| 5 | S5 | P0 | M | DevOps |
| 6 | S6 | P1 | S | Backend |
| 7 | S7 | P1 | S | Backend |
| 8 | S8 | P1 | M | Backend |
| 9 | S9 | P1 | M | Backend |
| 10 | S10 | P1 | M | Backend |
| 11 | S11 | P1 | S | DevOps |
| 12 | S12 | P2 | L | Backend |
| 13 | S13 | P2 | S | Backend + Mobile |
| 14 | S14 | P2 | S | Backend |
| 15 | S15 | P2 | S | Backend |
| 16 | S16 | P3 | S | Eng Lead |

---

*Continue to → [03_BACKEND_AUDIT.md](./03_BACKEND_AUDIT.md)*
