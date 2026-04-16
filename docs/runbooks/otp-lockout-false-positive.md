# Runbook: OTP Lockout False Positive

**What this covers:** How to identify and clear a Redis-based OTP lockout that is incorrectly
blocking a user from logging in. Also covers checking the lockout configuration env vars
and escalation to manual OTP testing.

**Severity:** P2 — individual user blocked; escalate to P1 if a fleet of drivers is affected.

**Prerequisites:**
- Redis CLI access (or access to the Redis provider dashboard)
- Supabase dashboard access (to look up the user by phone)
- Backend logs access (Railway CLI or Render dashboard)
- The affected user's phone number in E.164 format (e.g. `+14165551234`)

---

## 1. When This Happens

A user reports they cannot log in and keep seeing an "account locked" or "too many attempts"
error. This is expected after repeated wrong OTP entries, but can be a false positive when:

- The user received a delayed SMS and entered the OTP after it expired.
- The SMS was delivered twice and the user entered the OTP from the first message after the
  second OTP was already generated.
- A Redis restart or migration left stale lock keys.
- Automated testing or a bot hit the send-OTP endpoint for the user's number.

The OTP system uses a 4-digit code (`generate_otp()` in `backend/dependencies.py`) and
sends it via Twilio SMS. OTPs expire after `OTP_EXPIRY_MINUTES` (default: 5 minutes).

---

## 2. How to Check

### Step 1 — Confirm the lockout via Redis

```bash
# Replace with the user's phone number in E.164 format
PHONE="+14165551234"

redis-cli GET "otp_lock:${PHONE}"
redis-cli GET "otp_fail_count:${PHONE}"
```

- `otp_lock:<phone>` present with any non-null value → user is locked out.
- `otp_fail_count:<phone>` shows how many failed attempts triggered the lock.
- Both absent → OTP lockout is NOT the cause; investigate further (wrong phone number,
  Supabase auth issue, or Firebase token error).

### Step 2 — Check the lockout configuration

The lockout thresholds are controlled by env vars on the backend. Find them in Render/Fly
environment settings:

| Variable | Default (if not set) | Description |
|----------|----------------------|-------------|
| `OTP_MAX_FAILURES` | `5` | Failed attempts before lockout |
| `OTP_LOCKOUT_DURATION_SECONDS` | `600` (10 min) | How long the lockout lasts |

Check current values:

**Railway:**
```bash
railway variables --service backend
```

**Render:** Dashboard → spinr-backend → Environment.

If these vars are not set, the defaults apply. Confirm the actual values match policy.

### Step 3 — Check backend logs for the user's failed attempts

**Railway:**
```bash
railway logs --service backend | grep "${PHONE}"
```

**Render:** Dashboard → spinr-backend → Logs, filter by the phone number.

Look for repeated `OTP verification failed` or `Invalid OTP` log entries. If you see these
from a timestamp the user claims they were not trying to log in, it may indicate a brute-force
attempt on their number — do NOT clear the lockout without escalating.

### Step 4 — Verify the user exists in Supabase

```sql
SELECT id, phone, created_at, profile_complete
FROM users
WHERE phone = '+14165551234';
```

If the user does not exist, the OTP lockout is moot — the user needs to register first.

---

## 3. How to Clear the Lockout

Only clear the lockout after confirming this is a legitimate user and not a brute-force
situation (see Step 3 above).

```bash
PHONE="+14165551234"

# Remove both keys atomically
redis-cli DEL "otp_lock:${PHONE}" "otp_fail_count:${PHONE}"
```

Confirm both keys are gone:
```bash
redis-cli EXISTS "otp_lock:${PHONE}" "otp_fail_count:${PHONE}"
# Expected output: 0
```

Instruct the user to try logging in again. A new OTP will be sent via Twilio.

---

## 4. Escalation — Manually Send a Test OTP

If the user clears the lockout but still cannot receive an OTP (SMS not arriving), verify
Twilio is working:

```bash
# Requires twilio CLI: https://www.twilio.com/docs/twilio-cli/quickstart
twilio api:core:messages:create \
  --from "<TWILIO_FROM_NUMBER>" \
  --to "+14165551234" \
  --body "Spinr test OTP: 9999"
```

- SMS arrives → Twilio is healthy; the issue is in OTP generation or delivery code.
- SMS does not arrive → Twilio issue. Check https://status.twilio.com and the Twilio
  Console → Monitor → Logs → Messaging.

**Twilio credential locations:**
- `twilio_account_sid` — Twilio Console → Account Info
- `twilio_auth_token` — Twilio Console → Account Info
- `twilio_from_number` — Twilio Console → Phone Numbers → Active Numbers

These are stored in the `app_settings` Supabase table, not as environment variables.
Update them via Admin Dashboard → Settings → SMS / Auth.

---

## 5. Prevention

- Set `OTP_MAX_FAILURES` and `OTP_LOCKOUT_DURATION_SECONDS` explicitly in your deployment
  environment rather than relying on hardcoded defaults.
- Add a Sentry alert for repeated `otp_lock` key creations for the same phone number within
  a short window (potential brute-force signal).
- Consider rate-limiting the `/auth/send-otp` endpoint at the load-balancer level in addition
  to the Redis lockout.
- Document the lockout duration in the user-facing error message so support agents can set
  accurate expectations ("your account will unlock in 10 minutes").

---

## 6. Escalation

| Escalation trigger | Contact |
|--------------------|---------|
| Redis unreachable (cannot check keys) | See `api-down.md` |
| SMS not arriving after lockout cleared | Twilio Console + status.twilio.com |
| Suspected brute-force on a user's number | Security lead — do NOT clear the lockout |
| Multiple users locked out simultaneously | Likely a Redis or rate-limit misconfiguration; page engineering lead |
| Issue unresolved after 15 min | Page engineering lead via Slack #incidents |
