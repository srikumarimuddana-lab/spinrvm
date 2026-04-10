# Runbook: Stripe Webhook Failure

**What this covers:** Diagnosing and fixing failed Stripe webhook deliveries, including
signature verification errors, unreachable endpoints, and misconfigured secrets. The
webhook handler lives in `backend/routes/webhooks.py`.

**Severity:** P1 — payments may not be confirmed; rides could be stuck in an unpaid state.

**Prerequisites:**
- Stripe Dashboard access (developer/admin role)
- Backend logs access (Fly.io CLI or Render dashboard)
- Access to admin app settings (where `stripe_webhook_secret` and `stripe_secret_key`
  are stored in the `app_settings` Supabase table)

---

## 1. Symptoms

- Rides complete but `payment_status` stays `pending` (never flips to `paid`).
- Riders receive no "Payment Confirmed" push notification after a trip.
- Stripe Dashboard shows webhook events in a failed or pending state.
- Backend logs contain: `Stripe webhook signature verification failed` or
  `stripe_webhook_secret not set in admin settings`.

---

## 2. Dashboard Links

| Resource | URL |
|----------|-----|
| Stripe Webhooks | https://dashboard.stripe.com/webhooks |
| Stripe Events log | https://dashboard.stripe.com/events |
| Stripe API keys | https://dashboard.stripe.com/apikeys |
| Spinr admin settings | Admin Dashboard → Settings → Payment |

---

## 3. Verification Steps

### Step 1 — Check recent webhook deliveries in Stripe Dashboard

1. Go to https://dashboard.stripe.com/webhooks.
2. Select the endpoint registered for Spinr (e.g. `https://spinr-api.fly.dev/webhooks/stripe`).
3. Click "Recent deliveries".
4. Look for failed deliveries (red X). Click one to expand the error.

Common HTTP response codes from the backend and their meaning:

| Status | Meaning |
|--------|---------|
| 400 "Invalid signature" | `STRIPE_WEBHOOK_SECRET` mismatch — see Step 3 |
| 400 "Invalid payload" | Request body was modified in transit (rare) |
| 500 "Stripe not configured" | `stripe_secret_key` not set in admin settings |
| 503 / timeout | Backend is down — see `api-down.md` |

### Step 2 — Check backend logs for webhook errors

**Fly.io:**
```bash
fly logs --app spinr-backend | grep -i 'stripe\|webhook'
```

**Render:** Dashboard → spinr-backend → Logs, filter "stripe".

Key log messages and their meaning:
- `stripe_webhook_secret not set in admin settings — webhook verification disabled`
  → The webhook secret is missing from admin settings. Verification is skipped; events
  still process but are UNAUTHENTICATED. Set the secret immediately (Step 3).
- `Stripe webhook signature verification failed`
  → The secret is set but wrong. Rotate it (Step 3).
- `Stripe secret key not configured in app settings`
  → `stripe_secret_key` is missing. Set it in admin settings.

### Step 3 — Verify and fix the webhook secret

The webhook handler reads `stripe_webhook_secret` from the `app_settings` table via the
`get_app_settings()` helper, not from environment variables.

**To get the correct secret:**
1. Go to https://dashboard.stripe.com/webhooks.
2. Click the Spinr endpoint.
3. Click "Reveal" next to "Signing secret". Copy the value (starts with `whsec_`).

**To set it in the admin dashboard:**
1. Log into the Spinr admin dashboard.
2. Go to Settings → Payment.
3. Paste the signing secret into "Stripe Webhook Secret".
4. Save.

**Verify the endpoint URL is correct in Stripe:**
The registered endpoint must match exactly:
- Fly.io: `https://spinr-api.fly.dev/webhooks/stripe`
- Render: `https://spinr-api.onrender.com/webhooks/stripe`

Note: the path is `/webhooks/stripe` (no `/api/v1/` prefix).

### Step 4 — Confirm Stripe can reach the endpoint

```bash
curl -X POST https://spinr-api.fly.dev/webhooks/stripe \
  -H "Content-Type: application/json" \
  -d '{}' \
  -w "\nHTTP Status: %{http_code}\n"
```

Expected: `400` (missing signature header is correctly rejected). A `404` or `502` means the
route is not mounted or the backend is down.

---

## 4. Replaying a Failed Event

After fixing the root cause, replay failed events so payments are retroactively confirmed.

**In Stripe Dashboard:**
1. Go to https://dashboard.stripe.com/webhooks → select the endpoint.
2. Click "Recent deliveries" → find the failed event.
3. Click "Resend".

**For bulk replay (Stripe CLI):**
```bash
# Install Stripe CLI if not present: https://stripe.com/docs/stripe-cli
stripe listen --forward-to https://spinr-api.fly.dev/webhooks/stripe

# In a second terminal, replay a specific event by ID
stripe events resend evt_XXXXXXXXXXXXXXXX
```

After replaying, verify in Supabase that the affected ride's `payment_status` changed to `paid`:
```sql
SELECT id, payment_status, payment_intent_id, paid_at
FROM rides
WHERE id = '<ride_id>';
```

---

## 5. Prevention

- Keep `stripe_webhook_secret` up to date whenever you rotate or recreate the Stripe endpoint.
- Never use the same webhook secret across environments (test vs. production keys).
- Monitor Stripe's webhook delivery failure rate in the Dashboard → Webhooks → Metrics.
- Add a Stripe webhook alert in the Stripe Dashboard (Settings → Alerts) for failure rates
  above 5%.
- In Sentry, ensure the `Stripe webhook signature verification failed` log line is tagged
  as an error-level event for immediate alerting.

---

## 6. Escalation

| Escalation trigger | Contact |
|--------------------|---------|
| Backend returns 5xx to Stripe | See `api-down.md` |
| Cannot access Stripe Dashboard | Account owner / billing contact |
| Stripe is reporting a platform incident | https://status.stripe.com |
| Rides stuck as unpaid after replay | Backend lead to manually reconcile `payment_status` |
| Issue unresolved after 20 min | Page engineering lead via Slack #incidents |
