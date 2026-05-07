# ADR-005: Dual auth: Firebase ID token + short-lived HS256 JWT

**Date:** 2026-02-14
**Status:** Accepted

---

## Context

Spinr needs to authenticate three distinct actor classes:

1. **Riders and drivers** — identified by Canadian phone numbers, authenticated via SMS OTP
2. **Admin staff** — identified by email/password, need role-based access to the admin dashboard
3. **Service-to-service** — Stripe webhooks, Firebase push callbacks

The initial design had to answer: who issues tokens, how long do they live, and what claims does the backend trust?

Additional constraints:
- Phone-number identity (not email) is the primary identity for riders/drivers; Firebase Auth supports phone-number providers natively.
- The backend must work even if Firebase is temporarily unavailable (token refresh must not depend on Firebase being reachable at request time).
- Admin tokens must carry role and module-access claims to avoid a DB lookup on every admin request.

Alternatives considered:

| Option | Rejected because |
|--------|-----------------|
| Firebase ID tokens only (for all users) | Rider/driver tokens are 1-hour Firebase tokens; refresh requires Firebase; module-gated admin claims can't be embedded |
| Supabase Auth | Does not support phone+OTP for Canadian numbers without additional Twilio plumbing; adds a second auth vendor |
| Fully custom OTP + long-lived tokens | No replay protection; long-lived tokens are harder to revoke |
| Keycloak / Auth0 | Operational overhead; overkill for a small-team SaaS |

---

## Decision

Use a **dual-token model**:

**Riders and drivers:**
- Firebase handles phone-number OTP verification (via Twilio SMS gateway). The mobile app receives a Firebase ID token after OTP success.
- The mobile app sends the Firebase ID token to `POST /auth/verify-otp`. The backend verifies it with Firebase Admin SDK, then issues its own **short-lived HS256 JWT** (15-minute access token + 30-day refresh token).
- All subsequent API calls use the Spinr-issued JWT, not the Firebase token. Firebase is only in the path at login.
- The backend never trusts the `role` claim in rider/driver JWTs — the `users` table is queried on every request to get the current role and `is_active` state.

**Admin staff:**
- No Firebase involved. Admins log in with email + bcrypt password via `POST /api/admin/auth/login`.
- The backend issues a **12-hour HS256 JWT** with `role`, `email`, `modules`, and `admin_staff_id` embedded in claims.
- Admin JWTs are fully trusted: no per-request DB lookup for role or module access. This is acceptable because admin tokens are short-lived (12 hours) and `JWT_SECRET` rotation invalidates all of them instantly.

**Token lifecycle:**
- Access tokens: 15 min (rider/driver), 12 hr (admin)
- Refresh tokens: 30 days, stored as SHA-256 hash in the `refresh_tokens` table, rotated on every use
- Mobile clients auto-retry 401s via an Axios interceptor after calling `POST /auth/refresh`
- Instant revocation: `token_version` column in `users`; a JWT's `ver` claim must match. Bumping `token_version` (e.g., after `logout-all`) invalidates all outstanding tokens for that user without a key rotation.
- Redis session mirroring: `session:{user_id}` is written on login and checked on each request; mismatches return `ERR_SESSION_EXPIRED` (supports cross-replica instant revocation without a full DB query per request).

---

## Consequences

**Positive:**
- Firebase is only in the critical path at login. A Firebase outage does not affect authenticated users mid-session.
- Short-lived access tokens limit the blast radius of a stolen token to 15 minutes.
- The `token_version` mechanism allows instant revocation of a specific user's sessions without rotating `JWT_SECRET` (which would log out everyone).
- Admin claims in the JWT eliminate a DB round-trip on every admin request while still being revocable via `JWT_SECRET` rotation.

**Negative / trade-offs:**
- Two token issuers (Firebase for OTP verification, Spinr for session management) means two verification code paths to maintain.
- The `ver` claim check requires a `users` table read on every rider/driver request; this is mitigated by the Redis session cache but adds latency if Redis is unavailable.
- The 15-minute access token lifetime means mobile clients must implement refresh logic correctly. A bug in the Axios interceptor can cause spurious logouts (observed once during initial development).
- Admin tokens are 12 hours — longer than ideal. The rationale is UX (admins shouldn't be interrupted mid-shift), but this extends the revocation window for a compromised admin token. Mitigation: `JWT_SECRET` rotation in Railway env immediately invalidates all admin sessions.
