# Runbook: Security Alert Rules (Sentry)

**What this covers:** The Sentry alert rules to create so security-relevant
backend events reach a human. Every signal below **already ships** — the code
emits it today. What is missing is a rule that matches it.

**Severity:** Setup task. For an active breach see
[`security-incident.md`](security-incident.md) and
[`data-breach.md`](data-breach.md).

**Status:** 🔴 **No rules configured.** `ACTION_ITEMS.md` **C2** has tracked the
first one as "~5 min in Sentry UI, no code" — this runbook makes the other four
equally mechanical.

**Prerequisites:** Sentry project admin access; a Slack channel and/or
PagerDuty service to route to.

---

## 1. Why these are all "no code"

`backend/server.py` initialises Sentry with:

```python
LoggingIntegration(event_level="ERROR", level="WARNING")
```

plus a loguru→Sentry bridge. **Any `logger.error(...)` in the backend already
becomes a Sentry event.** The security signals below are therefore already
arriving in Sentry right now and being ignored, which is the worst of both
worlds — the cost of emitting them is paid, none of the value is collected.

Creating these rules is pure Sentry-UI configuration.

---

## 2. The rules

### 2a. Refresh-token reuse — possible token theft (**C2**)

| Field | Value |
|---|---|
| Match | `message` contains `REFRESH TOKEN REUSE DETECTED` |
| Or, more precisely | tag `spinr_alert` equals `refresh_token_reuse` |
| Threshold | **any single event** |
| Severity | **P1 — page** |
| Emitted by | `backend/utils/refresh_tokens.py:263` |

Prefer the **tag** match: it is set explicitly for this purpose and survives any
future rewording of the message string.

Why any single event pages: refresh tokens are single-use and rotated on every
use, so a reuse means a token was captured and replayed. One occurrence is
already evidence of compromise, not noise. The event carries `audience` and
`domain=auth` tags but **no PII** — correlate by `user_id` in the event body,
never by name or phone.

**On fire:** treat as a suspected account compromise. The reuse detection
already revokes the token cascade; confirm the affected user's sessions are
terminated, then follow [`auth-tokens.md`](auth-tokens.md). If more than one
user is affected in a short window, escalate to
[`security-incident.md`](security-incident.md) — that pattern suggests a leaked
log or an interception point, not a single stolen device.

### 2b. OTP lockout spike — credential stuffing

| Field | Value |
|---|---|
| Match | `message` contains `OTP_LOCKOUT_TRIGGERED` |
| Threshold | **> 10 events in 15 minutes** |
| Severity | **P2 — notify, do not page** |
| Emitted by | `backend/routes/auth.py:230` (also increments `spinr_auth_otp_lockout_total`) |

A single lockout is a user fat-fingering their code — that is the control
working, and paging on it would train people to ignore the channel. A *spike*
is an attack.

Tune the threshold against real traffic before enabling paging; 10/15min is a
starting point, not a measured value. The metric
`spinr_auth_otp_lockout_total` gives the same signal in Grafana if you would
rather alert there — see [`metrics-alerting.md`](metrics-alerting.md).

**On fire:** check whether lockouts concentrate on a few phone prefixes (attack)
or spread broadly (possible SMS delivery failure making legitimate users retry —
check Twilio before assuming malice). See
[`otp-lockout-false-positive.md`](otp-lockout-false-positive.md).

### 2c. SOS / emergency triggered

| Field | Value |
|---|---|
| Match | `message` contains `EMERGENCY ALERT TRIGGERED` |
| Threshold | **any single event** |
| Severity | **P0** |
| Emitted by | `backend/routes/rides/safety.py:109` (`logger.critical`) |

**This is a backstop, not the primary path.** SOS already produces an admin WS
broadcast, a safety distribution-list email, and — once configured — a
`utils/safety_paging.py` webhook. This rule exists because those can all fail
silently and a missed SOS is the worst failure mode in the product.

Note the known gap: there is currently **no re-page if an SOS goes
unacknowledged** (ADR-010 §3 flags it; it needs backend work, tracked as Phase 2
of `plans/monitoring-observability-implementation-plan.md`). Until that exists,
acknowledgement is a human responsibility. Follow
[`sos-incident.md`](sos-incident.md).

### 2d. Session / token revocation burst

| Field | Value |
|---|---|
| Match | `message` contains `session_revoked` or `token_revoked` |
| Threshold | **> 20 events in 10 minutes** |
| Severity | **P2** |
| Emitted by | `backend/socket_manager.py:200,226` |

Individually routine (logout, password change). In bulk it means either a mass
revocation event ran, or something is invalidating sessions incorrectly and
users are being logged out mid-ride.

### 2e. Database circuit breaker open

| Field | Value |
|---|---|
| Match | tag `domain` equals `dispatch` **and** message contains `circuit` — or alert on the metric instead |
| Preferred | metric `spinr_db_circuit_state{state="open"} > 0`, see [`metrics-alerting.md`](metrics-alerting.md) |
| Severity | **P1** |

Listed here for completeness because an open breaker is also a security-relevant
availability event, but the **metric is the better signal** — it is a state, not
an occurrence, and Sentry is poor at expressing "still true."

---

## 3. Routing

| Severity | Destination |
|---|---|
| P0 (2c) | Page immediately — PagerDuty service, plus the SOS on-call path |
| P1 (2a, 2e) | Page — the destination `ALERT_WEBHOOK_URL` already feeds is the least-friction option |
| P2 (2b, 2d) | Slack channel that does **not** page |

Set **rate limits on every rule** (Sentry: "notify at most once per N minutes").
A credential-stuffing run can generate thousands of matching events; without a
rate limit the rule becomes its own denial of service against the channel.

---

## 4. PIPEDA constraint on what you may add

If you add rules beyond these, the events they match must obey the never-log
list in `CLAUDE.md`: no raw GPS, no full phone numbers (`phone_last4` only), no
full names, no email addresses, no government IDs, no exact addresses.

`backend/utils/sentry_scrub.py` strips frame-local variables before send —
verified because `capture_exception` would otherwise ship the locals of every
frame. **Do not disable it**, and do not add a rule that requires unscrubbed
data to be useful; that is a request to weaken the scrubber.

---

## 5. Verification

For each rule, prove it fires before trusting it:

1. **2a** — safe to test in staging: authenticate, capture a refresh token, use
   it twice. The second use must produce a Sentry event *and* an alert. This is
   the single most valuable test here, because it is the rule with the highest
   severity-to-frequency ratio.
2. **2b** — fail an OTP 6 times in staging to trigger one lockout; confirm the
   event lands in Sentry. Confirming the *threshold* requires generating the
   full burst, which is worth doing once.
3. **2c** — trigger a staging SOS. This also exercises
   [`sos-incident.md`](sos-incident.md), which is worth rehearsing regardless.
4. Confirm each alert reaches the intended destination and that the **rate limit
   holds** under a burst.

Record completion dates in `ACTION_ITEMS.md` (C2 specifically) so this is not
re-derived later.

---

## 6. What this does not cover

- **RLS policy denials.** Supabase enforces row-level security in the database;
  denials are not currently surfaced to the backend as `logger.error`, so there
  is no event for Sentry to match. Surfacing them would be a backend change, not
  a rule — a genuine gap, not something this runbook can close.
- **Admin action anomalies.** Admin actions are written to the audit table but
  do not emit Sentry events. Anomaly detection over that table (e.g. bulk
  exports at 03:00) is a query problem, not an alerting-rule problem.
- **Frontend/mobile security events.** Rules here cover backend Sentry projects
  only. The rider/driver apps report through `@sentry/react-native` into their
  own project.
- **Anything about whether these thresholds are right.** Every number above is a
  starting point reasoned from the code path, **not** tuned against production
  traffic. Expect to adjust 2b and 2d after a week of observation.
