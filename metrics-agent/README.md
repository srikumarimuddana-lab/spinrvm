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
- `grafana/alert-rules.yaml` — the ADR-010 §5 step-5 alert rules
  (dispatch-latency breach, payment-failure-rate breach, plus the
  insurance-period write-failure rule added per ACTION_ITEMS.md C55) in
  Grafana Alerting's provisioning YAML format, routed to a placeholder Email
  contact point (decided 2026-09-06 — see the file's own header comment for
  why Email over a new Slack webhook).

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
6. **Point the alert contact point at real email address(es)**
   (`grafana/alert-rules.yaml` ships with an `<ALERT_EMAIL_TO>` placeholder;
   each address must be an accepted member of the Grafana Cloud org before
   Grafana's Email integration will send to it — see the file's header
   comment).
7. **Smoke-test** against real traffic once deployed — confirm the
   dashboard panel shows non-empty data and that a synthetic breach (or a
   `Test rule` in Grafana Alerting) actually fires before treating either
   alert as production-ready (ADR-010 §5 step 7 / issue #3295 implementation
   plan step 7).

Railway is explicitly out of scope for this MVP (ADR-010 §4 / §5, and
`ACTION_ITEMS.md` C5 — Railway is currently drifting from `main` with
blocked deploys; standing up monitoring against a known-stale build would
just be noise to suppress later).

## Logs & Grafana (7-day Fly log store on this machine)

Added 2026-09-24. This machine also stores the last **7 days of Fly logs**
(every app in the org — the same feed `fly logs` shows) and runs a
**private Grafana** for searching those logs and viewing the Grafana Cloud
metrics. Nothing here is public: there is still no `[http_service]`, and
Grafana is reached only through `fly proxy`.

```
Fly log stream (NATS, 6PN) ─► Vector ─► Loki (127.0.0.1:3100, /data volume, 168h retention)
                                             ▲
Grafana Cloud Prometheus ◄── (metrics:read) ─ Grafana :3000 ◄── fly proxy ◄── you
Backend /metrics ─► Alloy ─► Grafana Cloud        (unchanged)
```

| Process | Runs as | Listens on | Data |
|---|---|---|---|
| Alloy (metrics, PID 1) | `alloy` | `0.0.0.0:12345` (pre-existing) | rootfs, **not** `/data` |
| Loki | `alloy` | `127.0.0.1:3100` only | `/data/loki` |
| Vector | `alloy` | nothing inbound | `/data/vector` |
| Grafana | `grafana` | `:3000` (6PN only — no public service) | `/data/grafana` |

Metrics come first: `entrypoint.sh` starts each log-stack process in its
own restart loop and **never** lets a log-stack failure stop Alloy. A
missing volume or secret skips that component with an `ERROR` line in
`fly logs -a spinr-metrics-agent-yyz` instead of crashing the machine.

### One-time setup (in this order — before this config is deployed)

`deploy-metrics-agent.yml` auto-deploys on every push to `main` touching
`metrics-agent/**`, and `fly.toml` now declares a `[mounts]` volume, so
steps 1–3 must be done **before** the change is merged.

1. **Create the volume** (Toronto, same region as the machine):
   ```bash
   fly volumes create fly_logs --region yyz --size 10 -a spinr-metrics-agent-yyz
   ```
2. **Set the new secrets**, staged so nothing restarts yet:
   ```bash
   pw=$(openssl rand -base64 24); echo "$pw"   # save this in the password manager
   fly secrets set --stage -a spinr-metrics-agent-yyz \
     LOG_STREAM_ACCESS_TOKEN="$(fly tokens create readonly spinr_backend)" \
     GRAFANA_ADMIN_PASSWORD=$pw \
     GRAFANA_CLOUD_METRICS_READ_TOKEN='<Grafana Cloud access policy token with metrics:read>'
   ```
   - `spinr_backend` is the org slug from `infra/burst_controller/fly.toml`
     and `fly.toml`'s `LOG_STREAM_ORG`; confirm with `fly orgs list`.
   - `GRAFANA_ADMIN_PASSWORD` must be ≥16 characters or Grafana is not
     started. It only applies the **first** time Grafana starts (see
     "Rotate the Grafana password" below).
   - `GRAFANA_CLOUD_METRICS_READ_TOKEN` is optional — without it Grafana
     shows logs only. The existing `GRAFANA_REMOTE_WRITE_API_KEY` is
     `metrics:write` only and cannot be used for reading. The query URL is
     derived from `GRAFANA_REMOTE_WRITE_URL` (`…/api/prom/push` →
     `…/api/prom`); override with `GRAFANA_CLOUD_PROM_QUERY_URL` if needed.
3. **Replace the existing machine** so the new one can attach the volume
   (a Fly volume can't be attached to an already-created machine). This
   causes a metrics gap of a few minutes, until step 4 finishes:
   ```bash
   fly machine list -a spinr-metrics-agent-yyz
   fly machine destroy <machine-id> --force -a spinr-metrics-agent-yyz
   ```
4. **Deploy**: merge the change (auto-deploy), or run the
   "Deploy metrics-agent to Fly.io" workflow, or `fly deploy` from
   `metrics-agent/`.
5. **Verify**:
   ```bash
   fly logs -a spinr-metrics-agent-yyz          # expect "starting loki/vector/grafana", no ERROR lines
   fly ssh console -a spinr-metrics-agent-yyz -C "df -h /data"
   ```

### Open Grafana

```bash
fly proxy 3000 -a spinr-metrics-agent-yyz
```

Then open <http://localhost:3000> and log in as `admin`. Logs are under
**Explore → "Fly logs (Loki, 7 days)"**, for example:

```logql
{app="spinr-backend-yyz"} |= "error"
{app="spinr-backend-yyz", level="error"}
{app="spinr-backend-yyz"} |= "<request_id or ride_id>"
```

Labels available: `app`, `region`, `instance` (Fly machine ID), `level`.
Metrics are under the "Spinr metrics (Grafana Cloud)" data source.

### Operate

- **Turn the log stack off** (metrics keep running, no code revert):
  `fly secrets set LOGS_STACK_ENABLED=false -a spinr-metrics-agent-yyz`
  (a secret overrides the `[env]` value of the same name). Unset it to
  turn the log stack back on.
- **Disk full**: `fly volumes extend <volume-id> --size 20 -a spinr-metrics-agent-yyz`
  (no data loss). Loki can't write while the disk is full, but Alloy doesn't
  use `/data`, so metrics are unaffected.
- **Rotate the Grafana password**: `GRAFANA_ADMIN_PASSWORD` is only read
  when Grafana's database is first created. Change it afterwards in the
  Grafana UI (Profile → Change password), or:
  ```bash
  fly ssh console -a spinr-metrics-agent-yyz -C \
    "setpriv --reuid=grafana --regid=grafana --init-groups env GF_PATHS_DATA=/data/grafana /usr/share/grafana/bin/grafana cli --homepath /usr/share/grafana admin reset-admin-password '<new>'"
  ```

### Known gaps (read before relying on this for an incident)

- **No replay.** Fly's log stream is fire-and-forget: lines emitted while
  Vector or this machine is down (including during step 3/4 above and every
  deploy of this app) are **not** backfilled.
- **Single copy.** The volume lives on one host's disk. If the host fails,
  recovery is from Fly's daily volume snapshots (kept 5 days by default),
  so up to a day of logs can be lost.
- **Volume size is a guess.** 10 GB has not been measured against real log
  volume from up to 8 backend machines — check `df -h /data` after the
  first few days.
- **Images are pinned by tag, not digest** (`Dockerfile` explains why).
  The Loki/Vector/Grafana versions were the newest the authoring session
  could confirm without registry access; bump them and add digest pins.
- **Not validated against the real binaries.** The Loki config, Vector
  config/VRL and Grafana provisioning were written without being able to
  run Loki/Vector/Grafana (no registry or module-proxy access in that
  session). `entrypoint.sh`'s control flow was tested with stub binaries.
  The first deploy is the real test — watch `fly logs` for config errors.
- **Private network reachability.** Grafana (password-protected) and the
  pre-existing Alloy UI on `:12345` (no auth) are reachable from any app on
  the org's private 6PN network, not only via `fly proxy`.
- **Secrets briefly visible as process arguments.** `entrypoint.sh` passes
  each process's secrets through `env -i KEY=VALUE …` so no process inherits
  another's secrets; for the instant before `env` execs, those values are in
  its argv (`/proc/<pid>/cmdline`). Only root, `alloy` and `grafana` exist on
  this machine, so this was accepted as low risk.
- **PII retention.** Anything the backend already logs is now kept and
  searchable for 7 days. Logs stay in Fly's Toronto region (`yyz`), but
  CLAUDE.md forbids GPS, phone numbers, emails and names in logs — any leak
  there is now a 7-day leak, not a transient one.
- **Railway is not covered** — only apps on Fly.
