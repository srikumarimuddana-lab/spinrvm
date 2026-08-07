# ADR-010: Cross-replica metrics aggregation and production alerting

**Date:** 2026-08-02
**Status:** Proposed

---

## Context

`backend/utils/metrics.py` is a 186-line in-process Prometheus-style counter /
gauge / histogram registry. Its own module docstring is explicit about the
limitation:

> **Per-process only.** Each backend replica keeps its own counters. A
> Prometheus scraper reading /metrics from each replica adds them up in the
> server-side view — we do NOT aggregate across replicas here.

`backend/server.py`'s `/metrics` endpoint (line 236) exposes that per-process
state in Prometheus text exposition format, gated by `METRICS_AUTH_TOKEN`
(fail-closed in production if unset — good, and already correct; nothing to
fix there). Real call sites already emit SLA-relevant series:

| Metric | Emitted from |
|---|---|
| `spinr_dispatch_offer_sent_total` | `routes/rides/matching.py:726` |
| `spinr_dispatch_offer_accepted_total` | `routes/drivers/ride_flow.py:293` |
| `spinr_dispatch_offer_to_accept_duration_ms` | `routes/drivers/ride_flow.py:315` |
| `spinr_dispatch_presence_filter_failed_total` | `services/dispatch_service.py:363`, `routes/rides/matching.py:327,347` |
| `spinr_fare_calc_duration_ms` | `routes/rides/estimates.py:657` (decorator) |
| `spinr_fare_directions_duration_ms` | `routes/rides/estimates.py:313` |
| `spinr_payment_settlement_total{outcome=...}` | `services/payment_service.py:664` |
| `spinr_payment_fare_attribution_mismatch_total` | `utils/stripe_reconcile.py:210` |
| `spinr_ws_fanout_duration_ms` | `socket_manager.py:319-327` |
| `spinr_redis_used_memory_bytes` / `spinr_redis_maxmemory_bytes` | `server.py` `/metrics` handler (Redis `INFO` on each scrape) |

Meanwhile `CLAUDE.md` publishes hard P95 SLA targets (dispatch offer → driver
notification < 2 s, fare estimate < 300 ms, WS fan-out < 100 ms) and KPI
targets (match rate ≥ 85%, payment success ≥ 99%). **None of these can be
computed today.** Backend runs on Fly.io (a pool of 8 machines with
`min_machines_running=2`; the 6 suspended ones are resumed by Fly's proxy when
running machines exceed `soft_limit` — see `docs/runbooks/capacity-scaling.md`.
Note: when this ADR was written the fleet was a fixed 2 machines with no
elasticity at all, and this paragraph's original "autoscaled, scales up under
load" description was inaccurate) with Railway as a parallel deploy target per
[ADR-007](007-fly-primary-railway-standby.md). Each replica's counters live
and die with that process — there is no server-side view that sums them, so
every SLA/KPI number in `CLAUDE.md` today is **aspirational, not measured**.

This is not hypothetical. `ACTION_ITEMS.md` item **B6** (measure Directions
latency, re-tune the fare-estimate wait budget) already hit this exact wall:
the instrumentation (`spinr_fare_directions_duration_ms`) was added and is
correctly recording, but the item is explicitly blocked — *"pull the p99 from
`/metrics` (or wherever it's scraped to)"* — because there is nowhere that
durably holds more than one replica's, and more than one scrape interval's,
worth of samples. This ADR exists to unblock B6 and every future
"what's our real P95" question the same way.

**What this ADR does not do:** it changes no source code and adds no
dependency. It is a design for the next phase of work, scoped so the first
slice (§5) is implementable in under a day once approved.

---

## Decision

### 1. Aggregation approach

| Option | What it requires | Fit for Fly autoscaling | Fit for a small team |
|---|---|---|---|
| **(a) Prometheus server scrapes every Fly instance** | We stand up and operate a Prometheus server (storage, retention, HA, upgrades) plus Fly-specific service discovery — Fly machine IPs churn on every deploy and autoscale event, so this needs a `file_sd`/DNS-based discovery job against Fly's internal `*.internal` 6PN network, kept in sync by us. | Poor without extra plumbing: a scale-to-zero or short-lived autoscaled machine can start, serve, and stop between two scrape intervals — its samples are lost entirely, not just delayed. Undercounts exactly the bursty traffic (surge, dispatch storms) we most want visibility into. | Poor: we would own a stateful TSDB, Alertmanager, and Grafana as new production infrastructure — backups, upgrades, disk sizing, on-call for the monitoring stack itself. |
| **(b) Push to a managed backend** (recommended) | A lightweight agent process per Fly machine (e.g. Grafana Agent / Prometheus in agent mode) that scrapes `localhost:$PORT/metrics` on a short interval (15–30 s) and remote-writes to a managed Prometheus-compatible SaaS (Grafana Cloud, or an equivalent already-managed TSDB). | Good: the agent runs *inside* the same machine as the app, so there is no discovery problem — it always finds its own app on localhost. Because it pushes rather than waits to be pulled, a machine that's about to scale down has already shipped its data instead of losing it to a missed scrape. | Good: no TSDB, no Alertmanager, no Grafana instance for us to operate. Dashboards/alerting/retention are the vendor's problem. Free-tier volume at Spinr's current metric cardinality (a few dozen distinct series × 2 providers) is comfortably within typical free-tier series limits. |
| **(c) Full OpenTelemetry SDK export (OTLP)** | Add `opentelemetry-*` Python dependencies, instrument (or wrap) every call site, run an OTel Collector or push OTLP directly to a vendor. OTel is a transport/data-model standard, not a backend — you still have to pick and pay for a destination, so it doesn't remove the vendor decision, it adds a migration on top of it. | Fine, same push model as (b). | Heaviest lift of the three: new dependency, new instrumentation surface, and it unifies metrics *and* traces *and* logs — valuable, but far more than "make today's counters aggregate." |

**Recommendation: (b), specifically the "Prometheus in agent mode → managed
Prometheus-compatible backend" pattern**, for example Grafana Agent (or
Grafana Alloy, its successor) shipping to **Grafana Cloud**. Reasoning:

- It requires **zero changes to `metrics.py` or any call site**. The agent
  scrapes the exact same `/metrics` endpoint that already exists, using the
  same `METRICS_AUTH_TOKEN` bearer auth that's already enforced in
  production. The Prometheus text format `render_prometheus()` emits today is
  already what the agent expects — no format migration.
- It solves the Fly autoscaling problem *by construction*: colocated agent,
  no service discovery, no missed short-lived-machine samples (push, not
  pull).
- It is the smallest ops footprint for a small team: nothing new to patch,
  back up, or keep highly available ourselves. Grafana Cloud's own
  Alertmanager and dashboarding come for free with the same account.
- It is reversible and additive: if the vendor choice is wrong later, the
  only thing to change is the agent's remote-write target — the app-side
  `/metrics` contract doesn't move, so no application redeploy is required to
  switch backends.
- (c) is not rejected outright — it's the right long-term answer once
  distributed *tracing* (e.g. root-causing a specific slow dispatch offer
  across dispatch → WS fan-out → push) becomes the priority. But it's strictly
  more work to reach "we finally have a real P95" than (b), and CLAUDE.md's
  own testing-conventions section flags Spinr as mid-live-testing — the
  fastest safe path to a real number wins here.

### 2. Are histograms needed, and what changes in `metrics.py`?

**Histograms are needed for a real P95 — and `metrics.py` already has them.**
`observe()`, `time_ms()`, `timed()`, and `DEFAULT_MS_BUCKETS` (lines 71-126,
44-46) implement cumulative-bucket histograms, and `render_prometheus()`
(lines 153-175) already emits `_bucket{le=...}` / `_sum` / `_count` in valid
Prometheus histogram exposition format. `DEFAULT_MS_BUCKETS` — `(5, 10, 25,
50, 100, 250, 500, 1000, 2500, 5000, 10000)` ms — is explicitly commented as
"tuned for millisecond latencies around the SLA table in CLAUDE.md." Several
of the metrics in the table above (`spinr_dispatch_offer_to_accept_duration_ms`,
`spinr_fare_calc_duration_ms`, `spinr_fare_directions_duration_ms`,
`spinr_ws_fanout_duration_ms`) are histograms today, not gauges or counters.

So the honest framing is: **the local instrumentation was never the gap. The
gap is entirely aggregation (§1).** Prometheus histogram buckets are
additively aggregatable across time series — summing `_bucket{le=X}` counts
across replicas and then running `histogram_quantile()` on the summed buckets
gives a mathematically correct cross-fleet P95. This is *not* the same as
averaging each replica's own P95, which is a well-known Prometheus footprint
mistake and must not be done — a query like `avg(spinr_x_p95{...})` is wrong
even if such a per-replica p95 existed; the correct query is always
`histogram_quantile(0.95, sum(rate(..._bucket[5m])) by (le))`.

One real (non-blocking) gap worth flagging for a future pass, not required
for §5: `DEFAULT_MS_BUCKETS` has a gap between `1000` and `2500` — the
dispatch SLA threshold of exactly `2000` ms sits inside that gap, so
`histogram_quantile()` will linearly interpolate across a wide bucket rather
than reading a bucket boundary that lines up with the SLA. Once real traffic
volume justifies the precision, add an explicit `2000` bucket to whichever
metric backs the dispatch-latency alert (§3) — a one-line change, and safe to
defer since it only affects interpolation precision, not correctness of
direction (breach vs. no breach).

No dependency addition, no API change, no call-site change is required to
get real P95s once §1 ships. This is good news to report back: the
instrumentation debt here is smaller than it looks from the SLA table alone.

### 3. Alert rules

All expressions assume the histogram/counter names already emitted today,
labeled with the `provider` label described in §4. Windows are chosen to
avoid single-sample noise while staying inside the failure-impact column from
CLAUDE.md's SLA table.

| Rule | Expression (PromQL, post-aggregation) | Threshold | Window | Severity | Ties to |
|---|---|---|---|---|---|
| Dispatch latency breach | `histogram_quantile(0.95, sum(rate(spinr_dispatch_offer_to_accept_duration_ms_bucket{provider="fly"}[5m])) by (le))` | `> 2000` (ms) | sustained 5 m | P1 (page) | SLA: dispatch offer→accept P95 < 2 s; failure impact "ride abandonment" |
| Payment failure rate | `sum(rate(spinr_payment_settlement_total{outcome="failed"}[10m])) / sum(rate(spinr_payment_settlement_total[10m]))` | `> 0.01` (i.e. breaches 99%) | sustained 10 m, **and** ≥ 20 samples in-window (guard against alerting on 1-of-1 failures at low volume) | P1 (page) | KPI: payment success rate ≥ 99% |
| SOS unacknowledged | *Not a Prometheus metric* — direct query against `safety_incidents`: any row with `status` in the open set and `created_at` older than the threshold with no acknowledgement | unacknowledged **> 90 s** | evaluated every 30 s (not a 5 m rate window — SOS is not a rate metric) | **P0 (immediate page + escalation/re-page if still open at 90 s)** | Safety — "SOS ... offers one-tap 911; it never auto-dials" means our own paging path is the only backstop; near-zero tolerance |
| WS fan-out latency | `histogram_quantile(0.95, sum(rate(spinr_ws_fanout_duration_ms_bucket[5m])) by (le))` | `> 100` (ms) | sustained 5 m → warn; sustained 15 m → page | P2 → P1 escalation | SLA: WS fan-out P95 < 100 ms; failure impact "missed state updates" |
| Background-loop stall | Existing in-app `loop_watchdog` (`core/lifespan.py:519-566`, `utils/loop_alert.check_and_alert`) already pages via `ALERT_WEBHOOK_URL` on stale heartbeats — **keep this as the primary path**, since it doesn't depend on the metrics/remote-write pipeline being healthy. Additionally expose heartbeats as a gauge (`spinr_loop_heartbeat_timestamp_seconds{loop=...}`) so `time() - spinr_loop_heartbeat_timestamp_seconds > 2 × expected_interval` gives dashboard visibility and a second, independent alert path. | loop-specific (2× its own interval) | continuous | P1 | "Any new loop must be replay-safe" — a stalled loop (e.g. `stuck_ride_sweeper`, `offer_expiry_reaper`) is a silent correctness regression, not just a metrics gap |
| Match rate (KPI, not incident-grade) | `sum(rate(spinr_dispatch_offer_accepted_total{provider="fly"}[1h])) / sum(rate(spinr_dispatch_offer_sent_total{provider="fly"}[1h]))` | `< 0.85` | 1 h | P3 (business-hours notify, not page) | KPI: match rate ≥ 85%; below-target signal "dispatch radius too tight or driver supply gap" |
| Presence-filter degradation | `sum(rate(spinr_dispatch_presence_filter_failed_total[5m])) > 0` | any sustained nonzero rate | 5 m | P2 | Already logged as `logger.warning` at the call site — this turns a buried log line into an actionable signal instead of something only found by grepping logs after the fact |

SOS deliberately gets its own path rather than riding on the Prometheus
pipeline: `utils/safety_paging.py` already pages on-call at incident creation
(`page_on_call`), but there is currently no re-alert if the page goes
unacknowledged, and no metric at all tracks acknowledgement latency. Fixing
that is real backend work (a new field/column read, likely a small addition
to the existing `safety_checkin_loop`-style background-loop pattern per the
`spinr-background-loop` skill's replay-safety contract) — flagged here as a
required follow-up, not something this design-only ADR implements.

### 4. Avoiding double-counting across Fly primary / Railway standby

Per ADR-007, Railway and Fly both auto-deploy from `main` and both run **all
16 background loops concurrently at all times**, regardless of which one is
receiving live traffic via the `api-spinr.spinr.ca` Cloudflare CNAME. That's
correct and intentional for side effects (loops use atomic DB claims /
idempotency keys), but it matters a great deal for metrics:

- **Request-driven counters** (`spinr_dispatch_offer_sent_total`,
  `spinr_payment_settlement_total`, fare-calc histograms, WS fan-out) are
  only incremented by whichever provider is actually receiving traffic —
  under normal operation, exactly one of Fly/Railway is "hot," so summing
  across providers is safe in steady state. But it must not be assumed safe
  blindly: tag every series with a `provider` label (`fly` | `railway`,
  set by the local agent's static config, not application code) so this can
  be verified rather than assumed, and so a **split-brain DNS state** (stale
  resolver cache serving some clients from the wrong provider) is visible as
  simultaneous nonzero traffic on both labels instead of silently blended
  into one number.
- **Loop-driven metrics** (heartbeats, retry-loop counters, reconciliation
  counts) run on *both* providers *by design*, all the time. Summing these
  across `provider` double-counts by definition — a loop tick alert or a
  reconciliation-count dashboard must always be evaluated **per provider**
  (`by (provider, loop)`), never summed, or a healthy Fly loop will mask a
  dead Railway loop and vice versa.
- **Current reality, not a healthy-standby assumption:** `ACTION_ITEMS.md`
  item **C5** states Railway's `deploy-backend.yml` is blocked by a GitHub
  Environment protection rule with no expiry/owner, so **Railway has been
  silently drifting from `main`** since the pause. This directly changes
  what "per-provider" alerting should do today:
  - Loop-heartbeat alerts scoped to `provider="railway"` should be
    **suppressed/muted, not fixed**, while C5 is open. A ticking heartbeat
    from a stale, non-deploying build is not evidence the *current* code is
    healthy — treating it as a green light would be actively misleading, not
    just uninformative. Scope all loop-stall alerting to `provider="fly"`
    only until C5 is resolved and the ADR-007 failover drill (C1) is re-run.
  - Add one more alert, not in the original ask but a direct consequence of
    C5: **any nonzero `spinr_dispatch_offer_sent_total{provider="railway"}`
    while C5 remains open is itself a P1** — it means the CNAME silently
    pointed (or fell back) to a stale, drifted build serving real dispatch
    traffic, which is exactly the failure ADR-007 was written to prevent.
    This turns a documented but easy-to-forget risk into something that
    pages instead of being rediscovered during an incident.

### 5. Minimum-viable first increment (< 1 day)

Scoped to be genuinely completable by one engineer in under a day, and to
require no backend code change or dependency addition — only infra/agent
config plus vendor-side alert rules:

1. Create a Grafana Cloud account (or confirm one already exists) and note
   its Prometheus remote-write endpoint + API key.
2. On the Fly app only (Railway explicitly deferred — see §4, C5), add a
   Grafana Agent process alongside the existing backend process (Fly
   supports multi-process machines via `[processes]` in `fly.toml`) scraping
   `http://localhost:$PORT/metrics` every 15 s with the existing
   `METRICS_AUTH_TOKEN` bearer header, remote-writing to Grafana Cloud with
   static labels `provider=fly`, `instance=$FLY_ALLOC_ID`.
3. Confirm `METRICS_AUTH_TOKEN` is actually set in Fly's production secrets
   (it fails closed already if unset — this step is verification, not a
   code change).
4. In Grafana Cloud, build one panel:
   `histogram_quantile(0.95, sum(rate(spinr_dispatch_offer_to_accept_duration_ms_bucket{provider="fly"}[5m])) by (le))`.
   This is the first time in the project's history this number will be a
   measured fact instead of an assumption — worth calling out to
   stakeholders on its own.
5. Add exactly two alert rules from the §3 table to start: **dispatch
   latency breach** and **payment failure rate** — the two with the clearest
   "ride abandonment" / "money" business impact. Route them to the same
   channel `ALERT_WEBHOOK_URL` already feeds (reuse the existing paging path
   instead of standing up a second one).

**Deliberately deferred past the first day** (each is either a real backend
code change, or depends on something not yet resolved):
- WS fan-out and match-rate alert rules (mechanically identical to the two
  above — cheap to add once the agent is live, just sequenced after the
  first two to keep day-one scope tight).
- SOS unacknowledged alerting (§3) — needs new backend instrumentation, not
  just an alert rule; not something the "no source code changes" constraint
  of this ADR phase can deliver.
- Railway agent deployment — blocked behind resolving C5 first, per §4;
  standing up monitoring on a known-drifting build would just be more noise
  to suppress later.
- Full KPI-table dashboard build-out (driver utilization, weekly retention,
  support response time) — these aren't backend-metrics problems at all
  (retention/support come from other systems); out of scope for this ADR.

### 6. Cost estimate

Grafana Cloud publishes a free tier (as of general public information: a
meaningful allotment of active metric series and a two-week-class retention
window) that is very likely sufficient at Spinr's current cardinality — the
metrics table in this ADR's Context section lists roughly a dozen distinct
metric names, each with low label cardinality (a handful of `outcome`/`path`/
`format` label values, times 2 for `provider`), which lands in the low
hundreds of active series, well under typical free-tier ceilings. If usage
grows past the free tier, Grafana Cloud's paid tier is usage-based (priced
per active series / per GB ingested), commonly discussed in the tens of
dollars per month at small-team scale.

**This number should not be treated as final** — pricing pages change, and
this ADR could not reach Grafana's live pricing page from this environment
to confirm current figures at write time. Before committing budget, pull a
live quote from Grafana Cloud (or whichever managed backend is finally
selected) using Spinr's actual current label cardinality, and get sign-off
per this repo's normal spend-approval process. Worst case if the free tier
is exceeded unexpectedly: Grafana Cloud degrades gracefully (drops
aggregation resolution or blocks new series) rather than failing closed on
the app side — the `/metrics` endpoint and local histograms keep working
regardless of vendor billing state, so there's no production-availability
risk from a billing surprise.

---

## Consequences

**Positive:**
- Every P95 in the CLAUDE.md SLA table becomes something we can actually
  query, instead of a number nobody has verified. Unblocks `ACTION_ITEMS.md`
  B6 directly, and the same pattern unblocks every future "is our dispatch
  latency actually meeting SLA" question without another design cycle.
- Zero required changes to `metrics.py`, call sites, or the `/metrics`
  endpoint — the histogram/counter work already done across dispatch,
  payments, and fare code (including the B6 instrumentation) pays off
  immediately once §1 ships, rather than needing to be redone against a new
  instrumentation library.
- Turns two already-buried signals — the presence-filter-failed warning log
  and Railway's silent drift (C5) — into things that page instead of things
  someone has to remember to grep for.
- Reversible: switching managed backends later only touches the agent's
  remote-write config, not application code.

**Negative / trade-offs:**
- Introduces a new production dependency: the managed metrics vendor's
  uptime becomes part of the alerting path's reliability. Mitigated for the
  single most safety-critical case (SOS, §3) by design — that alert is
  specified as a direct DB check, not dependent on the Prometheus pipeline —
  and for background-loop stalls by keeping the existing in-app
  `loop_watchdog` as the primary path rather than replacing it.
- Adds a small per-machine resource cost (the agent process) on every Fly
  replica — expected to be minor (a scrape-and-forward agent, not a TSDB)
  but not yet measured against Fly's `shared-cpu-1x`/`1gb` sizing from
  ADR-007; worth a quick check during rollout that it doesn't push memory
  pressure into the existing 2-worker Uvicorn footprint.
- Alert-rule quality depends on getting the `provider` label and the
  per-provider (never-summed) loop-metric convention right from day one —
  §4 documents this explicitly because getting it wrong produces exactly the
  kind of false-confidence failure (Railway's stale heartbeat masking a real
  Fly problem) this ADR is trying to prevent.
- Cost is not yet a firm number (§6) — needs a live quote before this
  becomes a committed budget line, not just an architecture decision.

**Explicitly not yet measured, and this ADR does not claim otherwise:**
match rate, driver cancellation rate, driver utilization, weekly active
driver retention, safety incident rate, and support ticket response time
from the CLAUDE.md KPI table remain unmeasured after this ADR ships its
first increment — some (driver utilization, retention, support response)
aren't backend-metrics problems at all and need their own data sources. The
SLA table's dispatch/fare/WS latency numbers and the payment-success KPI are
the ones this design makes measurable; the rest stay explicitly open, and
should not be read as "solved" once §5 ships.
