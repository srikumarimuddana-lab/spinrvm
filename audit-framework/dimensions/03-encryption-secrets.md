# Dimension 03 — Encryption & Secrets Management

**Question:** Are secrets safe? Is sensitive data encrypted at rest and in transit?

---

## Checklist

### Backend Secrets
- [ ] All secrets loaded from environment variables — none hardcoded in source
- [ ] JWT_SECRET minimum length enforced at boot (≥ 32 characters)
- [ ] App refuses to start if required secrets are missing or placeholder values
- [ ] `.env.example` contains only placeholder values (not real keys)
- [ ] No secrets in git history (`git log --all -S "sk_live_"`)
- [ ] Supabase service-role key is not the same as the anon key
- [ ] Firebase service account JSON is not committed

### Mobile App Secrets
- [ ] `EXPO_PUBLIC_*` variables are safe to expose (they end up in the app bundle)
- [ ] No private keys, service account JSON, or Supabase service-role key in the mobile build
- [ ] API URLs pointing to production backend are not bundled in dev/test builds
- [ ] `eas.json` does not contain raw secret values (uses `$VAR_NAME` references)

### Password / OTP Hashing
- [ ] Passwords hashed with bcrypt (cost factor ≥ 12 — ~250ms on target hardware)
- [ ] SHA-256 legacy password upgrade path clears hash after successful bcrypt migration
- [ ] Password comparison uses constant-time comparison
- [ ] OTP hashed before storage (SHA-256 acceptable for short-lived 4-digit codes)

### Data Encryption at Rest
- [ ] Sensitive PII fields use column-level encryption (Supabase Vault) — license_number, VIN, bank_account
- [ ] FCM tokens stored — acceptable in plaintext if bucket is private
- [ ] No plaintext credit card data in any table (Stripe tokenises everything)

### TLS / Transport
- [ ] All backend endpoints served over HTTPS only
- [ ] HSTS header set (1 year + preload)
- [ ] No mixed HTTP/HTTPS content
- [ ] Consider: SSL certificate pinning for mobile (high-security, but breaks OTA updates)

### CI/CD Secrets
- [ ] TruffleHog secrets scan runs on every PR
- [ ] No secrets in GitHub Actions log output
- [ ] Secrets stored as GitHub Actions secrets, not in workflow YAML

---

## Severity Guide

| Finding | Severity |
|---|---|
| Real secret key committed in `.env.example` or source | CRITICAL |
| App starts with placeholder/weak JWT_SECRET | HIGH |
| Private Supabase service-role key in mobile bundle | CRITICAL |
| No bcrypt — plaintext or MD5 passwords | CRITICAL |
| bcrypt cost factor < 10 (< 100ms — too fast to brute-force) | HIGH |
| Sensitive PII (VIN, licence) stored in plaintext | HIGH |
| `EXPO_PUBLIC_*` variable containing a private key | HIGH |
| SHA-256 for passwords (no bcrypt) | HIGH |
| Hard-coded dev keys in source | LOW / RECOMMENDATION |
