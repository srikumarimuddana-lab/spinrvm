# Runbook: API Down

**What this covers:** Steps to diagnose and restore service when the Spinr FastAPI backend is
unreachable or returning 5xx errors to clients.

**Severity:** P0 — production outage. Begin immediately.

**Prerequisites:**
- Render dashboard access (or Fly.io dashboard access)
- Supabase project dashboard access
- Sentry access (error monitoring)
- SSH / shell access to run `curl` / `fly` CLI commands
- Redis CLI or access to the Redis instance

---

## 1. Symptoms

- Rider or driver app shows "Cannot connect" / network error
- Admin dashboard returns blank or 502/503
- Health endpoint returns non-200: `GET https://spinr-api.fly.dev/health`
- Sentry shows a sudden spike in unhandled exceptions

---

## 2. Triage Checklist (do these in order, stop when you find the cause)

- [ ] Health endpoint returns 200?
- [ ] Supabase connection healthy (Supabase status page + direct query)?
- [ ] Recent deploy in the last 30 minutes?
- [ ] Sentry showing new error class in the last 15 minutes?
- [ ] Redis reachable from the backend pod?
- [ ] Container/dyno actually running (not crashed-looping)?

---

## 3. Diagnosis Commands

### 3.1 Check health endpoint

```bash
# Fly.io primary
curl -f https://spinr-api.fly.dev/health

# Render fallback
curl -f https://spinr-api.onrender.com/health
```

Expected response: `{"status": "ok"}` with HTTP 200.

### 3.2 Check Supabase connectivity

```bash
# From a machine with the service role key set
curl -s \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  "$SUPABASE_URL/rest/v1/users?select=id&limit=1"
```

- Non-200 → Supabase is the problem. Check https://status.supabase.com.
- 401 → `SUPABASE_SERVICE_ROLE_KEY` is wrong or expired.

### 3.3 Check Redis connectivity

```bash
# Replace with your Redis host/port
redis-cli -h <REDIS_HOST> -p <REDIS_PORT> PING
```

Expected: `PONG`. Timeout or connection refused → Redis is down.

### 3.4 View live application logs

**Fly.io:**
```bash
fly logs --app spinr-backend
```

**Render:**
Go to Render Dashboard → spinr-backend service → Logs tab.

Look for tracebacks, `ConnectionError`, `OperationalError`, or OOM messages.

### 3.5 Check recent deploys

**Fly.io:**
```bash
fly releases --app spinr-backend
```

**Render:**
Dashboard → spinr-backend → Deploys. Compare timestamps against when alerts started.

### 3.6 Check Sentry for error spike

1. Go to https://sentry.io → Spinr project → Issues.
2. Sort by "First Seen" or "Events".
3. Look for any issue first seen within the last 30 minutes.

---

## 4. Resolution Steps

### 4a. Bad deploy — rollback

**Fly.io:**
```bash
# List recent releases to find the last good version number
fly releases --app spinr-backend

# Roll back to a specific version
fly deploy --image registry.fly.io/spinr-backend:<VERSION>
```

**Render:**
Dashboard → spinr-backend → Deploys → click the last successful deploy → "Redeploy".

### 4b. Supabase outage

- Monitor https://status.supabase.com.
- If project-specific: go to Supabase Dashboard → Settings → check project health.
- Escalate to Supabase support if an incident is not already posted.
- Consider enabling read-only mode or a maintenance page if outage is prolonged.

### 4c. Redis down

- Check your Redis provider dashboard (Render Redis, Upstash, etc.).
- If using Fly Redis: `fly redis status`.
- Restart Redis if self-hosted and the process has died.
- The backend degrades gracefully on rate-limit middleware failures; core auth
  OTP flows will be affected.

### 4d. Application crash loop

```bash
# Fly.io — restart all instances
fly restart --app spinr-backend

# Render — manual restart via dashboard or:
curl -X POST \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services/$RENDER_BACKEND_SERVICE_ID/restart"
```

### 4e. Environment variable missing

If logs show `JWT_SECRET not set` or similar startup errors:

**Fly.io:**
```bash
fly secrets set JWT_SECRET=<value> --app spinr-backend
fly restart --app spinr-backend
```

**Render:**
Dashboard → spinr-backend → Environment → add/update the variable → Save (triggers redeploy).

---

## 5. Post-Recovery Validation

```bash
# 1. Health check
curl -f https://spinr-api.fly.dev/health

# 2. Settings endpoint (verifies DB connection)
curl -f https://spinr-api.fly.dev/api/v1/settings

# 3. Vehicle types (lightweight read query)
curl -f https://spinr-api.fly.dev/api/v1/vehicle-types
```

All three should return 200 before closing the incident.

---

## 6. Escalation

| Escalation trigger | Contact |
|--------------------|---------|
| Supabase outage confirmed | Supabase support chat (Dashboard → Support) |
| Fly.io infrastructure issue | Fly.io status page + support ticket |
| Render infrastructure issue | Render status page + support ticket |
| Stripe-related errors in logs | See `stripe-webhook-failure.md` runbook |
| OTP / auth failures | See `otp-lockout-false-positive.md` runbook |
| Unresolved after 30 min | Page engineering lead via Slack #incidents |
