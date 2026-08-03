# Security Incident Response Runbook

**Covers:** Credential compromise · Data breach · Account takeover · Unauthorized API access · Stripe fraud
**Severity scale:** SEV-1 (active breach, data exfiltration) → SEV-4 (low-risk advisory)
**Escalation owner:** Engineering Lead + Legal (for breaches involving personal data)

---

## 1. Triage first — classify before acting

| Signal | Likely SEV | First action |
|--------|-----------|-------------|
| Mass account logins from single IP / ASN | SEV-2 | → [Credential stuffing](#3-credential-stuffing--account-takeover) |
| `JWT_SECRET` or Supabase key in a public commit | SEV-1 | → [Secret exposure](#4-secret-exposure) |
| Stripe webhook delivering unexpected `charge.failed` spikes | SEV-2 | → [Payment fraud](#5-payment-fraud) |
| `app_settings` table modified without admin action | SEV-1 | → [Admin compromise](#6-admin-dashboard-compromise) |
| Rider/driver PII accessible from public URL | SEV-1 | → [Data exposure](#7-data-exposure--pipeda-breach) |
| Trivy scan blocks deploy with new CRITICAL CVE | SEV-3 | → [Dependency CVE](#8-dependency-cve) |

---

## 2. General IR checklist (all incidents)

```
[ ] 1. Document: open a private Slack thread / incident doc with timestamp
[ ] 2. Assess blast radius: how many users affected? what data?
[ ] 3. Contain: stop the bleeding before you investigate
[ ] 4. Preserve: capture logs before rotating keys / rolling back
[ ] 5. Eradicate: fix root cause
[ ] 6. Recover: restore normal service, verify
[ ] 7. Notify: internal → legal → affected users (PIPEDA 72-hour rule if PII)
[ ] 8. Post-mortem: within 5 business days
```

---

## 3. Credential stuffing / Account takeover

**Indicators:**
- Spike in `POST /auth/verify-otp` 401s from IPs outside Canada
- Multiple users reporting "I didn't request this OTP"
- Redis key `otp_fail_count:{phone}` at or above `OTP_MAX_FAILURES`

**Contain:**
```bash
# Inspect OTP failure counters for targeted phones
redis-cli KEYS "otp_fail_count:*"
redis-cli GET "otp_fail_count:+13061234567"

# Manually force lockout for a phone under attack
redis-cli SET "otp_lock:+13061234567" 1 EX 86400

# Force-logout all sessions for a compromised user
POST /api/admin/users/{user_id}/logout-all   # (admin endpoint)
# OR directly via DB:
UPDATE users SET token_version = token_version + 1 WHERE id = '<user_id>';
# AND delete Redis session key:
redis-cli DEL "session:<user_id>"
```

**Investigate:**
```bash
# Count OTP requests by phone in the last hour (Railway log query)
grep "send_otp" /logs | grep "phone=+1306" | awk '{print $NF}' | sort | uniq -c | sort -rn

# Identify source IPs (if SlowAPI rate-limit logs are present)
grep "RateLimitExceeded" /logs | grep "otp"
```

**Eradicate:**
- If systematic: add IP block at Railway / Cloudflare WAF level
- If OTP brute-forced: confirm `OTP_MAX_FAILURES` (default 5) and `OTP_LOCKOUT_DURATION_SECONDS` (default 86400) in `core/config.py`

---

## 4. Secret exposure

### 4a. JWT_SECRET exposed

**Impact:** Anyone can mint valid JWTs for any user, including admins.

**Immediate (< 5 minutes):**
```bash
# 1. Rotate JWT_SECRET in Railway environment variables — new value invalidates ALL existing tokens
# Railway: Settings → Variables → JWT_SECRET → update

# 2. Every user will be logged out. This is correct and expected.

# 3. Bump ALL users' token_version to ensure tokens minted before the rotation are doubly-rejected
UPDATE users SET token_version = token_version + 1;
# And flush Redis sessions:
redis-cli KEYS "session:*" | xargs redis-cli DEL
```

**Investigate:**
- Run TruffleHog on the full git history to find the commit:
```bash
trufflehog git file://. --since-commit HEAD~500
```
- Check Railway deploy logs for any requests with unexpectedly privileged payloads during the exposure window.

### 4b. Supabase service-role key exposed

**Impact:** Full database read/write access, bypassing Row Level Security.

**Immediate:**
```bash
# 1. Rotate in Supabase Dashboard: Settings → API → Service Role Key → Rotate
# 2. Update SUPABASE_SERVICE_ROLE_KEY in Railway environment
# 3. Trigger Railway redeploy
```

**Investigate:**
- Check Supabase Dashboard → Logs → API logs for unusual queries during the exposure window.
- Look for reads on `users`, `drivers`, `otp_records`, `stripe_events`.

### 4c. FIREBASE_SERVICE_ACCOUNT_JSON exposed

**Impact:** Attacker can mint Firebase ID tokens for arbitrary UIDs.

**Immediate:** Firebase Console → Project Settings → Service Accounts → Revoke / Generate new key → update Railway env → redeploy.

---

## 5. Payment fraud

**Indicators:**
- Stripe Dashboard shows `charge.failed` rate > 5% in a 15-minute window
- Multiple rides with `payment_status = 'paid'` but corresponding `stripe_events` row missing `claimed_at`
- Stripe sends `radar.early_fraud_warning.created` webhook event

**Contain:**
```bash
# Pause Stripe payments by revoking the Stripe key in app_settings
UPDATE app_settings SET value = '' WHERE key = 'stripe_secret_key';
# This makes the backend fall back to mock mode — rides can still be requested but no charges processed.

# Alternatively, add a rate-limiting block in Stripe Dashboard → Radar → Rules
```

**Investigate:**
```bash
# Find rides with payment anomalies
SELECT r.id, r.rider_id, r.total_fare, r.payment_status, se.id AS stripe_event_id
FROM rides r
LEFT JOIN stripe_events se ON se.metadata->>'ride_id' = r.id::text
WHERE r.payment_status = 'paid' AND se.id IS NULL
ORDER BY r.completed_at DESC LIMIT 50;
```

**Stripe idempotency check:**
Every webhook is claimed via `claim_stripe_event(event_id)` before processing. If a duplicate is found in `stripe_events` without `claimed_at`, it means the claim failed silently — check `backend/routes/webhooks.py`.

---

## 6. Admin dashboard compromise

**Indicators:**
- Admin login from unexpected IP/country in Railway logs
- `app_settings` rows modified (Stripe keys, fare multipliers) without a corresponding admin action
- New `admin_staff` row created outside of normal onboarding

**Contain:**
```bash
# 1. Immediately disable the compromised admin account
UPDATE admin_staff SET is_active = false WHERE email = 'suspected@spinr.ca';

# 2. Rotate ADMIN_PASSWORD in Railway env (applies to the super-admin bootstrap account)

# 3. Revoke all admin JWTs — admin tokens are 12-hour HS256 signed with JWT_SECRET
#    Rotating JWT_SECRET logs out all users including admins:
# Railway: Settings → Variables → JWT_SECRET → update
```

**Investigate:**
- Check `audit_log` table (if populated) for `actor_id` of the suspicious changes.
- Review Railway access logs for the `/api/admin` prefix during the window.
- Verify `app_settings` values are correct (especially `stripe_secret_key`, `stripe_webhook_secret`).

---

## 7. Data exposure / PIPEDA breach

**Indicators:**
- PII (phone numbers, addresses, trip history) accessible via unauthenticated URL
- A misconfigured Supabase RLS policy allows cross-user data reads
- Researcher or user reports receiving another user's data in an API response

**Contain:**
```bash
# 1. If the leak is via an endpoint: patch the code or disable the endpoint
#    via a Railway env flag (add a DISABLE_ENDPOINT=1 check)

# 2. If via Supabase RLS: fix the policy immediately in Supabase Dashboard
#    Settings → Authentication → Policies → affected table

# 3. Identify the scope:
SELECT id, phone, created_at FROM users
WHERE created_at > '<breach_start>'::timestamptz
ORDER BY created_at;
```

**PIPEDA 72-hour notification requirement:**
If personal information of Canadian residents was accessed by an unauthorized party and poses a real risk of significant harm:
1. Notify the **Office of the Privacy Commissioner of Canada** within 72 hours.
   Form: https://www.priv.gc.ca/en/report-a-concern/report-a-privacy-breach-as-an-organization/
2. Notify affected users directly (in-app message + email if available).
3. Keep an internal breach record for 24 months.

**Notification template (user):**
> We're writing to let you know that [description of incident]. The information that may have been accessed includes [list]. We have [fixed the issue / rotated credentials / etc.] and [describe any protective action users should take]. If you have questions, contact privacy@spinr.ca.

---

## 8. Dependency CVE

**Indicators:**
- Trivy CI job blocks a deploy with CRITICAL/HIGH severity
- GitHub Dependabot opens a security PR
- `pip-audit` or `npm audit` returns vulnerabilities

**Assess:**
```bash
# Backend
cd backend && pip-audit --fail-on-vuln

# All frontend surfaces
cd rider-app  && npm audit
cd driver-app && npm audit
cd admin-dashboard && npm audit
```

**Remediate:**
```bash
# Backend — bump the affected package in requirements.txt
pip install --upgrade <package>  # verify it fixes the CVE
pip freeze | grep <package> >> requirements.txt

# Node
npm audit fix             # auto-patch where semver allows
npm install <pkg>@<safe>  # pin to safe version if audit fix won't
```

For CRITICAL CVEs with no available fix: disable the affected feature behind a flag and open a tracking issue with a 48-hour SLA.

---

## 9. Post-incident checklist

```
[ ] Root cause identified and documented
[ ] Fix deployed and verified
[ ] Secrets rotated if any were exposed
[ ] Affected users notified (if PII involved)
[ ] PIPEDA report filed if required (72-hour window)
[ ] Pre-commit hook and CI checks updated to prevent recurrence
[ ] Post-mortem written using docs/templates/postmortem.md (timeline, impact, root cause via 5-whys, remediation, preventive actions)
[ ] Post-mortem shared with team within 5 business days
```

---

## Contact

| Role | Responsibility |
|------|---------------|
| Engineering Lead | Technical containment and fix |
| Legal / Privacy Officer | PIPEDA notification, user communication |
| Stripe (fraud) | +1-888-926-2289 · https://stripe.com/docs/disputes |
| Supabase support | https://supabase.com/support |
| Firebase support | https://firebase.google.com/support |
