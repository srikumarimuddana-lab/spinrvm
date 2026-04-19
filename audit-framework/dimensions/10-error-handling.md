# Dimension 10 — Error Handling & Resilience

**Question:** When things break, does the app degrade gracefully? Can the user recover?

---

## Checklist

### Backend Error Responses
- [ ] Exception hierarchy defined (e.g. `SpinrException` with subclasses per domain)
- [ ] HTTP status codes correctly mapped: 400 (validation), 401 (auth), 403 (forbidden), 404 (not found), 409 (conflict), 422 (schema), 500 (internal)
- [ ] `request_id` included in all error responses (enables tracing)
- [ ] CORS headers present on error responses (not just success responses)
- [ ] Stack traces never exposed in production — `ENV=production` guard
- [ ] All error handlers return consistent JSON format: `{error, request_id, code}`

### Database Resilience
- [ ] DB calls retry on transient errors: HTTP/2 RST, TCP reset, network timeout
- [ ] `httpx.TimeoutException` included in retry clause
- [ ] Connection pool has explicit acquisition timeout (not unbounded wait)
- [ ] Supabase errors wrapped into domain exceptions — not raw HTTP errors exposed to clients

### Client-Side Resilience
- [ ] 401 retry queue: concurrent requests don't all attempt token refresh simultaneously
- [ ] Request timeout configured (Spinr standard: 15 seconds)
- [ ] Offline queue: failed requests retried when connection restored
- [ ] 4xx errors not retried (only 5xx and network errors)
- [ ] Error log ring buffer: bounded size (50 entries max) — no unbounded accumulation
- [ ] Error log doesn't contain large response bodies — only relevant fields

### React Error Boundaries
- [ ] Root-level ErrorBoundary catches unhandled errors
- [ ] Every major screen is wrapped in an ErrorBoundary (not just root)
- [ ] Error boundary shows a useful recovery action (retry / go home)
- [ ] Error reported to Crashlytics with user context (driver ID, ride ID)

### Offline Handling
- [ ] Offline detected via `@react-native-community/netinfo`
- [ ] Offline banner visible — not hidden behind notch
- [ ] Actions queued when offline and replayed when reconnected
- [ ] Queue entries don't retry 4xx errors (permanent failures cleared immediately)
- [ ] Queue survives app restart (persisted to AsyncStorage)

---

## Severity Guide

| Finding | Severity |
|---|---|
| Stack trace exposed in production error response | CRITICAL |
| Unhandled exception crashes the server (no catch-all) | HIGH |
| Offline banner hidden behind notch/status bar | CRITICAL (UX) |
| No request_id in error responses — can't trace production issues | MEDIUM |
| CORS missing on error responses — frontend can't read error body | MEDIUM |
| DB timeout not retried — all timeouts are permanent failures | MEDIUM |
| 401 retry not queued — concurrent refresh flood | MEDIUM |
| Error boundary at root only — major screens unprotected | MEDIUM |
| Offline queue retries 4xx — infinite loop on auth errors | MEDIUM |
| Error log stores full response body — OOM risk | LOW |
