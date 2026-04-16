# Runbook: Driver Not Receiving Rides

**What this covers:** Diagnosing why an online driver is not being offered ride requests,
including WebSocket connectivity, FCM push notification fallback, OTP lockout, the `is_online`
flag, and Supabase RLS policies.

**Severity:** P1 — individual or fleet-wide driver impact.

**Prerequisites:**
- Supabase dashboard access (SQL editor or Table editor)
- Backend logs access (Railway CLI or Render dashboard)
- Redis CLI access (for OTP key inspection)
- Driver's phone number and/or user ID (get from the driver or admin dashboard)

---

## 1. Symptoms

- Driver reports they are marked "online" in the app but receive no ride requests.
- Ride requests exist in the system (visible in admin dashboard) but are not dispatched to the driver.
- Driver app shows connected status but no pop-ups appear.
- Multiple drivers in a region are simultaneously affected (fleet-wide → likely RLS or dispatch bug).

---

## 2. Quick Diagnosis — Which Layer Is Broken?

Work through these checks in order:

| # | Check | Healthy sign | Broken sign |
|---|-------|-------------|-------------|
| 1 | `is_online` flag in DB | `true` | `false` or missing |
| 2 | WebSocket connection | Log line: driver connected | No connection log |
| 3 | FCM token registered | Row exists in `users` table | `fcm_token` is null |
| 4 | OTP lockout | No Redis key for phone | Key present |
| 5 | RLS policies | Query returns driver row | Empty result set |

---

## 3. Step-by-Step Fix

### Step 1 — Verify the driver's `is_online` flag

Run in Supabase SQL Editor (Dashboard → SQL Editor):

```sql
SELECT id, phone, is_online, is_available, current_location, updated_at
FROM drivers
WHERE user_id = '<driver_user_id>';
```

If `is_online` is `false`:
- Ask the driver to toggle offline → online in the app.
- If the flag still doesn't flip, the driver app WebSocket is not writing back to the server.
  Proceed to Step 2.

To manually reset (use only as a temporary fix while investigating):
```sql
UPDATE drivers
SET is_online = true, is_available = true
WHERE user_id = '<driver_user_id>';
```

### Step 2 — Check WebSocket connection in backend logs

**Railway:**
```bash
railway logs --service backend | grep '<driver_user_id>'
```

**Render:** Dashboard → spinr-backend → Logs, then filter by the driver's user ID.

Look for:
- `WebSocket connected: <driver_user_id>` — healthy.
- `WebSocket disconnected` followed by no reconnect — client is not connecting.
- No entries at all — the driver app may be sending to the wrong WebSocket URL.

Verify the driver app is pointing at the correct backend URL. The Expo env var is
`EXPO_PUBLIC_BACKEND_URL`. In production this must be the deployed API base URL (e.g.
`https://spinr-api.up.railway.app`).

### Step 3 — Check FCM token validity

FCM push notifications are the fallback when the WebSocket is not connected.

```sql
-- Check if the driver's user record has a valid FCM token
SELECT id, phone, fcm_token, fcm_token_updated_at
FROM users
WHERE id = '<driver_user_id>';
```

- `fcm_token` is `null` → the driver app never registered a token with the backend.
  Have the driver log out and back in; the app registers the token on login.
- Token is present but stale (older than 30 days) → request the driver re-login to refresh it.

To manually clear a stale token so the driver re-registers on next launch:
```sql
UPDATE users
SET fcm_token = null
WHERE id = '<driver_user_id>';
```

Also verify Firebase is configured: `FIREBASE_SERVICE_ACCOUNT_JSON` must be set on the backend.
Check via logs on startup: `firebase_admin not available for push notifications` means it is not.

### Step 4 — Check OTP lockout in Redis

A locked-out driver cannot re-authenticate. If the session token expired and the driver cannot
log back in, they will appear offline.

```bash
# Replace +1XXXXXXXXXX with the driver's phone number in E.164 format
redis-cli GET otp_lock:+1XXXXXXXXXX
redis-cli GET otp_fail_count:+1XXXXXXXXXX
```

- Non-null value → driver is locked out. See the `otp-lockout-false-positive.md` runbook to clear.
- If Redis is unavailable → auth will fail for all users; see `api-down.md`.

### Step 5 — Check Supabase RLS policies

Row-Level Security (RLS) can silently filter out rows, causing dispatch queries to return no
eligible drivers even when they are online.

Run the dispatch eligibility query as the service role (which bypasses RLS) to see if the
driver appears:

```sql
-- Service role bypasses RLS; run this in Supabase SQL Editor
SELECT d.id, d.user_id, d.is_online, d.is_available, d.vehicle_type
FROM drivers d
WHERE d.is_online = true
  AND d.is_available = true;
```

If the driver appears here but NOT when queried with the anon/public key, an RLS policy is
blocking the dispatch query.

To inspect active RLS policies:
```sql
SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual
FROM pg_policies
WHERE tablename = 'drivers';
```

Contact a Supabase admin to adjust policies. Do NOT disable RLS on production tables without
a security review.

### Step 6 — Check notifications table for delivery failures

```sql
SELECT *
FROM notifications
WHERE user_id = '<driver_user_id>'
ORDER BY created_at DESC
LIMIT 10;
```

Look at the `status` field. Repeated `failed` entries indicate FCM is rejecting the token
(token expired / unregistered). Follow Step 3 to refresh the token.

---

## 4. Fleet-Wide Issue

If multiple drivers are simultaneously not receiving rides:

1. Check the dispatch service logs for errors (not just the driver WebSocket handler).
2. Check Supabase is healthy and the `drivers` table is reachable.
3. Check if a recent deploy changed the ride-matching logic.
4. Roll back the deploy if the issue started immediately after one (see `api-down.md` § 4a).

---

## 5. Escalation

| Escalation trigger | Contact |
|--------------------|---------|
| Redis unreachable | See `api-down.md` |
| RLS policy change needed | Database / backend lead |
| Firebase push notifications globally broken | Firebase Console → Cloud Messaging; check quota |
| WebSocket handler crashing on all connections | Page engineering lead via Slack #incidents |
| Issue unresolved after 20 min | Page engineering lead |
