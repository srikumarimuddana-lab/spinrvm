# Runbook — Synthetic Monitoring + SLO Alerting

**Owner:** `devops` · **Status:** SCAFFOLDING ONLY — no external monitor exists yet
**Tracks:** `ACTION_ITEMS.md` E4

---

## Current state (as of 2026-08-18)

Nothing external probes the platform. A total outage — Cloudflare
misroute, both Fly.io and Railway down, DNS failure — is currently
discovered by users opening the app, not by any alert. This runbook and
`monitoring/synthetic-checks.yaml` are the **scaffolding**: the exact
endpoints, thresholds, and alerting policy a human needs once they pick a
vendor. Nothing in this runbook or the referenced YAML file talks to a
real monitoring vendor or a real PagerDuty account — no account has been
created, no integration key exists, and no endpoint has been probed from
this work.

**What still has to happen before this is real monitoring, not a spec:**
1. A human picks a vendor (see "Vendor options" below — this is a budget
   and ops-ownership decision, not a technical one this doc makes).
2. A human creates an account with that vendor.
3. A human (or a follow-up ticket) translates each entry in
   `monitoring/synthetic-checks.yaml` into that vendor's actual check
   config.
4. A human creates a real PagerDuty service (or reuses an existing one)
   and swaps the placeholder integration key below for a real one, stored
   as a vendor-side secret — never committed to this repo.

---

## What to probe

Three probes, each specified in full (path, method, expected status,
timeout, SLA threshold) in `monitoring/synthetic-checks.yaml`. Summary:

| Check | Method + path | Expected status | Frequency | SLA threshold | SLA source (CLAUDE.md) |
|---|---|---|---|---|---|
| `health-readiness` | `GET /health` | 200 | every 1 min | none (up/down only) | no latency row exists for `/health`; do not invent one |
| `auth-flow-liveness` | `POST /api/v1/auth/refresh` (no token) | **401** (expected — see below) | every 1 min | P95 < 200 ms | Performance SLAs → "Auth token refresh" |
| `fare-estimate-liveness` | `GET /api/v1/fares?lat=52.1332&lng=-106.6700` | 200 | every 1 min | P95 < 300 ms | Performance SLAs → "Fare estimate (rider tap → price shown)"; cross-checked against KPI Targets → "P95 fare calc latency" |

Frequency is **every minute** for all three probes, per E4's own wording
in `ACTION_ITEMS.md` ("hitting `/health`, auth, and fare-estimate every
minute from outside").

### Why these specific endpoints

- **`/health`** (`backend/server.py`) is the same readiness probe
  Fly.io's `[[http_service.checks]]`, Railway's `healthcheckPath`, and
  both Dockerfile `HEALTHCHECK`s already use *internally*. Probing it
  *externally* (outside the platform's own network) is what catches a
  failure those internal gates can't see — DNS, Cloudflare routing, or
  both cloud providers being simultaneously unreachable from the public
  internet. It checks DB reachability (`_db_ready()`), so a 503 here
  means "backend up, DB unreachable," not "backend down" — route
  accordingly (see `docs/runbooks/supabase-down.md`).

- **`POST /api/v1/auth/refresh`** with no refresh token was chosen over
  `/auth/send-otp` or `/auth/verify-otp` deliberately. `send-otp` sends a
  real Twilio SMS (real cost, and consumes the 6/minute per-IP rate
  limit CLAUDE.md documents for that endpoint) and `verify-otp` needs a
  live OTP code a synthetic probe can't obtain. Calling `/auth/refresh`
  with no cookie/body token deterministically returns **401** ("Missing
  refresh token") when the auth code path and its DB dependency are
  healthy — see `backend/routes/auth.py`'s `refresh_access_token()`. A
  timeout, 5xx, or *anything other than 401* means the auth flow itself
  is broken. This is why the check's expected status is 401, not 200 —
  the probe is graded on getting the *expected failure fast*, not on
  succeeding.

- **`GET /api/v1/fares?lat=...&lng=...`** (`backend/routes/fares.py`) is
  the actual rider-facing fare-estimate endpoint — the literal "rider
  tap → price shown" path named in CLAUDE.md's Performance SLAs table.
  The pinned coordinate (52.1332, -106.6700) is central Saskatoon, inside
  an active service area as of this writing; if Saskatoon's service-area
  polygon or launch status ever changes, this coordinate must be updated
  (see `docs/runbooks/saskatoon-launch.md`) or the probe will start
  failing with "no service area" errors that look like a false-positive
  outage. This endpoint reads Redis-cached fare data — a failure here
  can also mean Redis degradation, not a full outage; check
  `docs/runbooks/redis-down.md` before treating it as a P0.

None of the three probes create a ride, move money, or send a real SMS.
Ride booking/dispatch and payment flows are explicitly **out of scope**
for this scaffold — see the "Not covered" section of
`monitoring/synthetic-checks.yaml` for why.

---

## SLA / SLO thresholds (verbatim from CLAUDE.md — do not re-derive)

Pulled directly from CLAUDE.md's **Performance SLAs** table:

| Path | Target P95 | Failure impact |
|---|---|---|
| Fare estimate (rider tap → price shown) | **< 300 ms** | Booking friction |
| Auth token refresh | **< 200 ms** | UX stutter |

And from CLAUDE.md's **KPI Targets** table (cross-reference only, not a
separate threshold):

| Metric | Target | Below-target signal |
|---|---|---|
| P95 fare calc latency | **< 300 ms** | Upstream (Google Maps) or logic bloat |

`/health` has no latency row in either table — treat it as a pure
up/down probe. If a latency SLO is ever wanted for `/health`, add it to
CLAUDE.md's Performance SLAs table first; do not invent a number in the
monitoring layer that isn't traceable back to CLAUDE.md.

---

## Down vs. degraded

- **Down**: the probe gets a non-expected status (anything but the
  documented `expected_status` for that check — note `auth-flow-liveness`
  expects 401, not 200), a timeout, or a connection failure, on **3
  consecutive 1-minute checks**. Page immediately.
- **Degraded**: the probe gets the expected status, but P95 latency over
  a rolling 5-minute window exceeds the SLA threshold in the table above.
  Do not page on a single slow sample — a single Google Maps hiccup on
  `fare-estimate-liveness` is noise, not an incident. Route degraded
  alerts to a lower-urgency channel (see below) rather than paging.

This mirrors CLAUDE.md's Observability Conventions distinction between
"user-visible errors → Sentry + error log" (down) and "degraded-but-
recovered → warning log + metric, never Sentry" (degraded) — apply the
same severity split to synthetic-check alerting.

---

## Alert routing (placeholder — no real integration exists yet)

Per E4's own text, alerts route to **PagerDuty**. Until a human creates
the real service:

- `alert_route: pagerduty-placeholder-integration-key` in
  `monitoring/synthetic-checks.yaml` is a literal placeholder string, not
  a real key. It must never be replaced with a real PagerDuty integration
  key in this repo — vendor-side secrets belong in the vendor's own
  config/secret store, not committed here.
- Suggested routing once real: `down` alerts → the on-call PagerDuty
  service already used for `docs/runbooks/on-call.md`'s P0/P1 rotation;
  `degraded` alerts → a lower-urgency Slack/email channel, not paging,
  consistent with the down/degraded split above.

---

## Vendor options (explicitly not decided here)

This is a **human/budget decision**, not a technical one. Options, listed
without a recommendation:

- **Checkly** — API+browser checks, native PagerDuty integration, IaC
  config (JS/TS).
- **UptimeRobot** — simple HTTP/keyword checks, lower cost, coarser
  latency SLO tooling.
- **Grafana Cloud Synthetic Monitoring** — fits if Spinr already runs
  Grafana for dashboards (see `docs/runbooks` and the Observability
  Conventions section of CLAUDE.md); k6-based checks.

Whichever is chosen, the check definitions in
`monitoring/synthetic-checks.yaml` already have everything needed
(path, method, expected status, timeout, frequency, SLA threshold, and
the down/degraded semantics above) to translate into that vendor's native
config without re-deriving anything from CLAUDE.md again.

---

## Source of truth

`monitoring/synthetic-checks.yaml` is the machine-readable spec this
runbook summarizes. If the two ever disagree, the YAML file's
`sla_source` fields (which cite the exact CLAUDE.md table row) win —
update this runbook to match, not the other way around.
