# Dimension 17 — Observability

**Question:** When something goes wrong, do we know within minutes — not days — and can we trace it to the exact request and line?

---

## Checklist

### Structured logging
- [ ] Every log line is JSON (not free text) with fields: `ts`, `level`, `request_id`, `user_id`, `route`, `event`, `duration_ms`
- [ ] `request_id` (or `trace_id`) propagated from ingress through every log line and every outbound call (Stripe, Supabase, Twilio)
- [ ] No PII in logs — phone, email, card, OTP all redacted at the logger level, not per-call
- [ ] Redis/DB/Stripe errors use `logger.error(...)` with full exception (CLAUDE.md rule) — `.warning(...)` is banned on these paths

### Metrics (prod SLIs)
- [ ] Per-route latency p50/p95/p99 + error rate
- [ ] Dispatch time (ride created → driver assigned) — tail latency is the product metric
- [ ] WebSocket connection count + message rate + disconnect reasons
- [ ] Background-loop liveness (surge, dispatch, payment retry, doc expiry, corp auto-topup, low-balance nudge, allowance reset) — last-run timestamp per loop
- [ ] Stripe webhook lag (received → processed) + failure count
- [ ] Redis fallback-to-memory event counter (rate limiter + OTP lockout)

### Tracing
- [ ] Distributed trace spans cover: HTTP in → DB query → external API → response
- [ ] Trace ID exported to mobile client for support-ticket cross-reference
- [ ] Slow-query log enabled (>200 ms) with query shape, not values

### SLOs / alerts
- [ ] Published SLOs per product surface: ride-accept dispatch time; API availability; WS delivery
- [ ] Error budget burn alert: 50% burn at 2 h / 95% burn at 24 h
- [ ] PagerDuty (or equivalent) routing per severity; escalation rules documented
- [ ] Synthetic check: rider-side "create ride → get estimate" every 60 s
- [ ] Alert on: Redis down · Supabase 5xx · Stripe 5xx · FCM success rate < 95%

### Background tasks
- [ ] Each of the 7 loops emits a heartbeat (metric + log) every run
- [ ] If a loop hasn't fired in 2× its interval → alert
- [ ] Errors in background loops page on-call — not silently swallowed

### Crash reporting (mobile)
- [ ] Crashlytics / Sentry enabled with PII scrubbing
- [ ] Source maps / dSYMs uploaded in CI per release
- [ ] ANR (Android) + launch-time telemetry collected
- [ ] Release health dashboard monitored before rollout completes

---

## Common Findings

- **"logger.warning" on DB/auth/payment errors** — must be `logger.error` with full exception. CLAUDE.md explicitly forbids the warning-and-continue pattern.
- **"It works on my machine but I can't tell if it works in prod"** — no synthetic check on the primary user flow.
- **"The background loop stopped running a week ago"** — no liveness alert on the 7 asyncio loops.
- **"I can't find the error in the logs"** — missing request_id propagation.
- **"Sentry is on but PII is leaking"** — scrub rules not reviewed.

## How to Test

```bash
# Check for banned logger.warning on critical paths
grep -rn "logger\.warning" backend/routes/auth.py backend/routes/payments.py \
  backend/routes/webhooks.py backend/routes/rides.py backend/db_supabase.py

# Verify request_id threads through middleware
grep -n "request_id\|X-Request-ID" backend/core/middleware.py \
  backend/utils/error_handling.py

# Look for synthetic / heartbeat checks
grep -rn "heartbeat\|synthetic\|last_run_at" backend/core/lifespan.py \
  backend/utils/ 2>/dev/null | head

# Mobile crash reporting configuration
grep -rn "Sentry\|Crashlytics\|@sentry/react-native" \
  rider-app/app/_layout.tsx driver-app/app/_layout.tsx \
  admin-dashboard/sentry.*.config.ts
```

## Regulatory tags to apply (from regulatory-matrix.md)
`PIPEDA` (logs must not leak PII) · `SOC2` (change mgmt, IR, monitoring) · `PCI-DSS` (audit trail on card events)
