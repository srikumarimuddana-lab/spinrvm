# metrics-agent

Standalone Fly app implementing ADR-010's metrics-aggregation MVP (Option B —
**not** colocated in `backend/`'s Docker image; see
[docs/adr/010-metrics-aggregation-and-alerting.md](../docs/adr/010-metrics-aggregation-and-alerting.md)
and the Option A/B tradeoff in issue
[#3295](https://github.com/srikumarimuddana-lab/spinrvm/issues/3295)).

This app runs [Grafana Alloy](https://grafana.com/docs/alloy/latest/) (the
successor to Grafana Agent) as a separate Fly Machine. It:

1. Discovers every machine in the backend's Fly app (`spinr-backend-yyz`,
   per-machine — see "Fly `.internal` DNS problem" below) every 30s.
2. Scrapes each backend machine's `/metrics` endpoint over Fly's private
   `.internal` 6PN network, sending the `METRICS_AUTH_TOKEN` bearer header.
3. Remote-writes the scraped series to Grafana Cloud, tagged
   `provider="fly"` per ADR-010 §4 (so loop-driven per-provider metrics are
   never accidentally summed with a future Railway agent).

**This app is separate from `backend/`'s Dockerfile/fly.toml by design** —
Option B keeps a new third-party binary (Alloy) off the hardened,
digest-pinned backend runtime image (see CR-2026-002 / `backend/Dockerfile`'s
"pip removed from runtime image" comment and `docker-image-scan` Trivy gate),
at the cost of the small Fly-discovery glue described below.

## Why Grafana Alloy (not vector or legacy grafana-agent)

- **grafana-agent (legacy)** is in maintenance mode; Grafana's own docs
  point new deployments at Alloy. No reason to pick the deprecated one.
- **vector** is a fine general log/metrics router, but scrape→remote_write to
  a Prometheus-compatible backend is not its primary shape — its Prometheus
  scrape + `prometheus_remote_write` sink work, but the config is less
  idiomatic than Alloy's, which is purpose-built by the same vendor as the
  destination (Grafana Cloud) for exactly this pattern. Alloy's
  `prometheus.scrape` → `prometheus.remote_write` pipeline is the documented,
  first-party path for "scrape a `/metrics` endpoint, ship to Grafana Cloud,"
  which is precisely ADR-010 §1's chosen shape — fewer moving parts to get
  wrong, and first-party support if something breaks.

## Fly `.internal` DNS problem — how this is solved

Per ADR-010's risk note and the issue's "Risks of making this change"
section: resolving `spinr-backend-yyz.internal` from `.internal` DNS
load-balances to **one** machine per connection, not all of them, so a naive
scrape config only ever sees one of the ≥2 running backend machines and
silently misses the rest.

This is solved with **per-machine addressing**, not the Fly Machines API
directly (no extra API-token scope needed beyond what 6PN already grants):
Fly automatically publishes a private DNS name shaped
`<machine-id>.vm.<region>.<app>.internal` for every running machine, and also
publishes a `_machines._internal` **outputs several A/AAAA records at once**
for `vms.<app>.internal` — one address per currently-running machine, unlike
the load-balanced `<app>.internal` name. `discover-targets.sh` resolves
`vms.spinr-backend-yyz.internal` via `dig AAAA` and writes one Prometheus
`file_sd` target per resolved address to `/etc/alloy/targets.json` every 30s.
Alloy's `discovery.file` component watches that file and hot-reloads targets
without a restart, so machines that come up under Fly's autoscaling pool
(`docs/runbooks/capacity-scaling.md`, up to 8 machines) are picked up on the
next refresh, and suspended/removed machines drop out the same way.

This avoids needing a `FLY_API_TOKEN` Machines-API secret at all — DNS-based
discovery over the existing 6PN network is sufficient and is the pattern
Fly's own docs recommend for this exact "scrape all machines" case.

## What's implemented here (verified, does not require live credentials)

- `Dockerfile` — pulls the official `grafana/alloy` image, adds `dig`
  (from `bind-tools`) for the discovery script, copies in
  `config.alloy` and `discover-targets.sh`. **Not built/pushed in this
  session** (no Fly registry credentials) — Dockerfile syntax was checked
  with `docker build --check`-equivalent reasoning (see PR body for exact
  verification performed) but not a live `docker build`.
- `fly.toml` — new standalone app `spinr-metrics-agent-yyz`, same region as
  the backend (`yyz`) so 6PN latency to scrape targets is minimal. No public
  HTTP service is exposed — this app makes only outbound connections
  (scrape backend over 6PN, remote_write to Grafana Cloud over the public
  internet); `[http_service]` is intentionally omitted.
- `config.alloy` — Alloy pipeline: `discovery.file` (reads
  `/etc/alloy/targets.json`) → `prometheus.scrape` (15s interval, bearer
  auth from `METRICS_AUTH_TOKEN`, `provider="fly"` label) →
  `prometheus.remote_write` (Grafana Cloud endpoint + basic auth from Fly
  secrets, both placeholders — see below).
- `discover-targets.sh` — resolves per-machine addresses and writes the
  `file_sd` JSON; runs in a loop inside the container (`entrypoint.sh`)
  alongside Alloy itself.
- `grafana/dashboard-panel.json` — the ADR-010 §5 step-4 panel
  (dispatch-latency P95), importable once a Grafana Cloud account exists.
- `grafana/alert-rules.yaml` — the two ADR-010 §5 step-5 alert rules
  (dispatch-latency breach, payment-failure-rate breach) in Grafana
  Alerting's provisioning YAML format, routed to a placeholder contact point
  matching the existing `ALERT_WEBHOOK_URL` Slack channel.

## What's blocked on human-provided credentials / access (not done here)

This session has no Grafana Cloud account, no remote-write endpoint/API key,
and no Fly deploy credentials for a *new* app. The following are **not**
done and must be completed by a human:

1. **Create (or confirm) a Grafana Cloud account** and obtain its Prometheus
   remote-write endpoint URL + an API key (Grafana calls this a "Cloud
   Access Policy token" scoped to `metrics:write`).
2. **Run `fly launch` / `fly apps create`** for this new app from
   `metrics-agent/` (this repo does not run `fly launch` non-interactively
   here — no Fly org/token in this session):
   ```
   cd metrics-agent
   fly apps create spinr-metrics-agent-yyz --org <spinr-fly-org>
   ```
3. **Set Fly secrets** on the new app (names only — values are real
   credentials from step 1, never commit them):
   ```
   fly secrets set -a spinr-metrics-agent-yyz \
     METRICS_AUTH_TOKEN=<same value as backend's METRICS_AUTH_TOKEN secret> \
     GRAFANA_REMOTE_WRITE_URL=<GRAFANA_CLOUD_REMOTE_WRITE_URL> \
     GRAFANA_REMOTE_WRITE_USERNAME=<GRAFANA_CLOUD_PROMETHEUS_USERNAME> \
     GRAFANA_REMOTE_WRITE_API_KEY=<GRAFANA_CLOUD_API_KEY>
   ```
   Verify first that `METRICS_AUTH_TOKEN` is actually set on the backend app
   (`fly secrets list -a spinr-backend-yyz`) — ADR-010 §5 step 3. If it's
   unset, the backend's `/metrics` already fails closed (503) in production,
   so this agent would get nothing to scrape until it's set there too.
4. **`fly deploy -a spinr-metrics-agent-yyz`** from `metrics-agent/`.
5. **Import** `grafana/dashboard-panel.json` and `grafana/alert-rules.yaml`
   into the Grafana Cloud account from step 1 (UI import or
   `grafanactl`/Terraform if preferred — not scripted here since there's no
   account to target).
6. **Point the alert contact point at the real `ALERT_WEBHOOK_URL`** value
   (`grafana/alert-rules.yaml` ships with a placeholder webhook URL to be
   replaced with the same Slack webhook `loop_watchdog` already uses).
7. **Smoke-test** against real traffic once deployed — confirm the
   dashboard panel shows non-empty data and that a synthetic breach (or a
   `Test rule` in Grafana Alerting) actually fires before treating either
   alert as production-ready (ADR-010 §5 step 7 / issue #3295 implementation
   plan step 7).

Railway is explicitly out of scope for this MVP (ADR-010 §4 / §5, and
`ACTION_ITEMS.md` C5 — Railway is currently drifting from `main` with
blocked deploys; standing up monitoring against a known-stale build would
just be noise to suppress later).
