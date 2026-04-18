# Dimension 02 — Authentication & Session Management

**Question:** Can an attacker take over an account, replay a token, or bypass auth?

---

## Checklist

### OTP / Login Flow
- [ ] Rate limit on OTP send (Spinr standard: 3/min)
- [ ] Rate limit on OTP verify (Spinr standard: 5/min)
- [ ] Brute-force lockout after N failures (Spinr standard: 5 → 24h block)
- [ ] Lockout is Redis-backed — not silently skipped when Redis is down
- [ ] OTP expires after 5 minutes — enforced server-side, timezone-aware
- [ ] OTP hash stored (not plaintext) — SHA-256 is acceptable for 4-digit/5-minute OTPs
- [ ] OTP comparison uses constant-time comparison (hmac.compare_digest)
- [ ] Error messages do not reveal whether a phone number exists ("Invalid OTP" not "Phone not found")

### JWT Tokens
- [ ] JWT algorithm pinned to HS256 — `algorithms=["HS256"]` (not `algorithms=None`)
- [ ] JWT_SECRET minimum length enforced at startup (≥ 32 chars)
- [ ] JWT expiry set (≤ 1 hour for access tokens)
- [ ] Token version checked on every request — supports force-logout-all
- [ ] No sensitive data in JWT payload (no password, no full PII)

### Refresh Tokens
- [ ] Refresh tokens rotate on every use — old token revoked immediately
- [ ] Refresh token expiry enforced (Spinr standard: 30 days)
- [ ] Revocation stored in DB (`revoked_at` timestamp — not boolean `revoked`)
- [ ] Replay attack: revoked token rejected (UNIQUE constraint on token_hash)
- [ ] All auth paths (OTP + Firebase) issue a refresh token

### Client-Side Token Storage
- [ ] Tokens stored in SecureStore (not AsyncStorage, not localStorage)
- [ ] Access token held in memory only — not written to disk
- [ ] In-memory token cleared on logout
- [ ] 401 retry queue prevents concurrent refresh floods

### Session Management
- [ ] Logout clears all local state and navigates to login
- [ ] Force-logout-all works via token version increment
- [ ] Session not restored after password/phone change without re-auth
- [ ] Firebase auth path validates audience (driver vs. rider — separate apps)

---

## Severity Guide

| Finding | Severity |
|---|---|
| Token replay possible (no revocation or UNIQUE constraint) | CRITICAL |
| JWT algorithm not pinned (algorithm confusion attack) | CRITICAL |
| No brute-force lockout at all | HIGH |
| Lockout silently disabled when Redis is down | HIGH |
| Plaintext OTP in database | HIGH |
| Token version not checked (force-logout doesn't work) | HIGH |
| No refresh token issued for one auth path | MEDIUM |
| OTP comparison not constant-time | MEDIUM |
| Error message leaks phone number existence | MEDIUM |
| Access token stored in AsyncStorage | HIGH |
