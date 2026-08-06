# Runbook: Uptime & Synthetic Monitoring Setup

**What this covers:** Standing up external probing for the Spinr backend, so an
outage is detected by a machine instead of by a support ticket.

**Severity:** Setup task, not an incident. For a live outage see
[`api-down.md`](api-down.md).

**Status:** 🔴 **Not yet provisioned.** Nothing external probes production
today. This runbook is the operator checklist to change that — it is Track B
work that cannot be done from a dev sandbox because it requires a vendor account.

**Prerequisites:**
- An account with an uptime vendor (see §2)
- The production `METRICS_AUTH_TOKEN` value (Fly secrets)
- Permission to add alert destinations (Slack channel / PagerDuty service)

---

## 1. Why this matters more than usual here

Read this before deciding the probe interval — Spinr's deployment topology
makes an uptime alert mean something different than it does on a single-host app.

Per [ADR-007](../adr/007-fly-primary-railway-standby.md), the backend runs on
**Fly.io (`yyz`, primary)** with **Railway as a warm standby**, and traffic is
routed by a **Cloudflare CNAME on `api-spinr.spinr.ca`**. There is no load
balancer — failover is a manual DNS change (see
[`railway-fly-failover.md`](railway-fly-failover.md)).

Two consequences:

1. **Nobody is watching.** A Fly outage today is discovered when riders or
   drivers complain. There is no automated signal at all.
2. **The standby may be stale.** `ACTION_ITEMS.md` **C5** records that
   Railway's `deploy-backend.yml` is blocked by a GitHub Environment protection
   rule, so Railway has been silently drifting from `main`. **An uptime alert
   today therefore means "fail over to a build of unknown age," not "fail over
   and relax."** Check C5's status before executing a failover, and treat
   confirming the standby's commit SHA as part of the response, not an
   afterthought.

---

## 2. Choosing a vendor

Any of these covers the need on a free or near-free tier. Pick one; do not run
two.

| Vendor | Why you might pick it |
|---|---|
| **Better Stack** | Uptime + log drain in one account, so §5's log retention gap can be closed with the same vendor and bill |
| **Checkly** | Best if you want the §4 synthetic browser/API flows as first-class, scripted checks |
| **UptimeRobot** | Simplest and cheapest if all you want is §3 |

**Data residency note:** these probes send only a URL, a status code, and
latency — no personal information — so PIPEDA residency rules do not constrain
the choice the way they do for analytics. Do **not** configure a probe to post
a request body containing rider or driver data.

---

## 3. The three probes to configure

### 3a. Liveness — is the API up at all

| Field | Value |
|---|---|
| URL | `https://api-spinr.spinr.ca/health` |
| Method | `GET` |
| Interval | 60 s |
| Timeout | 10 s |
| Healthy when | HTTP 200 |
| Alert after | 2 consecutive failures (avoids paging on one dropped packet) |
| Severity | **P0** |

`/health` is unauthenticated by design — it is also what Fly, Railway, and the
Docker `HEALTHCHECK` call. It returns 503 when Postgres is unreachable, so this
one probe covers both "process is dead" and "database is unreachable."

**Do not point this at `/health/dependencies`.** That endpoint is auth-gated and
returns 503 for a *vendor* problem, which is not a reason to declare the API
down.

### 3b. Depth — are our dependencies healthy

| Field | Value |
|---|---|
| URL | `https://api-spinr.spinr.ca/health/dependencies` |
| Method | `GET` |
| Header | `Authorization: Bearer <METRICS_AUTH_TOKEN>` |
| Interval | 5 min |
| Healthy when | HTTP 200 |
| Alert after | 2 consecutive failures |
| Severity | **P2** (investigate; do not wake anyone) |

Returns **200 when degraded** and **503 only when a dependency is fully down**,
so alerting on the status code alone is correct — no body parsing needed.

The response body names which dependency is unhealthy and why, e.g.:

```json
{
  "healthy": false,
  "dependencies": {
    "supabase": {"status": "down", "reason": "circuit_open"},
    "redis": {"status": "degraded", "reason": "using_in_process_fallback"},
    "stripe": {"status": "ok"},
    "twilio": {"status": "not_configured", "reason": "missing_credentials"}
  }
}
```

Statuses: `ok` (1) · `degraded` (0.5, still serving) · `down` (0) ·
`not_configured` (0, credentials absent).

> **Known limitation, read before trusting this.** `stripe`, `twilio`,
> `google_maps`, and `firebase` report **configuration presence only** — they
> are not called. A `configured` Stripe with an expired key still reports `ok`.
> This is deliberate: a scrape endpoint that calls a paid third-party API
> becomes a traffic generator against rate limits shared with real settlements.
> Genuine vendor liveness comes from
> `spinr_payment_settlement_total{outcome="failed"}` and friends — see
> [`metrics-alerting.md`](metrics-alerting.md).

### 3c. TLS certificate expiry

Most vendors offer this as a checkbox on an existing HTTPS monitor. Enable it
on the 3a monitor, alert at **14 days** remaining. Severity **P2**.

---

## 4. Synthetic ride canary (not yet built)

The probes above prove the API answers. They do **not** prove a rider can
actually get a ride — dispatch could be silently failing to match while
`/health` returns 200 the whole time.

Closing that needs a scripted `searching → driver_assigned → completed` flow run
on a schedule. It is **deliberately not built yet** because it creates *real
rides* and therefore needs, at minimum:

- a hard staging-only guard, so it can never execute against production data
- a dedicated canary rider and driver account, excluded from KPI and payout
  reporting
- confirmation it cannot consume a real driver's availability

Tracked as Phase 3 of `plans/monitoring-observability-implementation-plan.md`.
Do not improvise this against production.

---

## 5. Log retention (related gap)

Backend logs currently go to Fly/Railway stdout with short retention and no
search. During an incident that means the evidence may already be gone. If you
chose Better Stack in §2, add a Fly log drain to the same account now; otherwise
Grafana Loki via the Grafana Cloud account from
[`metrics-alerting.md`](metrics-alerting.md) covers it.

---

## 6. Alert routing

Route **P0** (3a) to whatever wakes a human — the same destination
`ALERT_WEBHOOK_URL` already feeds is the path of least resistance, and reuses an
already-proven channel rather than standing up a second one.

Route **P2** (3b, 3c) to a channel that does *not* page.

---

## 7. Verification after setup

Do not consider this done until you have proven the alert fires. An untested
monitor is worse than none, because it manufactures false confidence.

1. **Prove liveness detection.** Point a temporary copy of the 3a monitor at a
   URL you know 503s. Confirm the alert arrives at the intended destination
   within the expected window. Delete the temporary monitor.
2. **Prove auth is correct.** `curl -s -o /dev/null -w '%{http_code}'
   https://api-spinr.spinr.ca/health/dependencies` → expect **503**
   (unauthenticated in production fails closed). Repeat with
   `-H "Authorization: Bearer $METRICS_AUTH_TOKEN"` → expect **200**.
   If the unauthenticated call returns 200, `METRICS_AUTH_TOKEN` is not set in
   Fly secrets — fix that before proceeding, since `/metrics` is exposed by the
   same gate.
3. **Confirm no false positives** across one full day before enabling paging.
4. **Record the date this was completed** in `ACTION_ITEMS.md`, so the next
   person does not have to re-derive whether probing exists.

---

## 8. What this does not cover

- **Mobile app availability.** These probes test the backend. A broken Expo
  build or a rejected app-store release is invisible here.
- **Partial degradation.** A 200 from `/health` says Postgres answered, not that
  dispatch is matching or that fares are calculating. That is §4's job, and §4
  is not built.
- **Per-replica health.** Fly runs multiple machines; the CNAME hits one of
  them. A single sick replica can stay hidden behind healthy siblings. Use the
  per-instance metrics in [`metrics-alerting.md`](metrics-alerting.md) for that.
