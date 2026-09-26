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
**Grafana** for searching those logs and viewing the Grafana Cloud metrics.

**Updated 2026-09-25: Grafana is now public.** `fly.toml` has an
`[http_service]` forwarding `https://spinr-metrics-agent-yyz.fly.dev` (or
its custom domain, if one is later added) to Grafana's port 3000. This was
a deliberate choice to make Grafana reachable from any browser without a
Fly account or `fly proxy`. The only gate is Grafana's own admin login —
anonymous access and self-sign-up are both disabled in `entrypoint.sh`, but
a single shared admin/password is a materially weaker perimeter than
requiring Fly account access, so treat `GRAFANA_ADMIN_PASSWORD` with the
same care as a production credential (see "Known gaps" below for the full
tradeoff). `fly proxy` still works too, if you'd rather not put the URL in
a browser history.

```
Fly log stream (NATS, 6PN) ─► Vector ─► Loki (127.0.0.1:3100, /data volume, 168h retention)
                                             ▲
Grafana Cloud Prometheus ◄── (metrics:read) ─ Grafana :3000 ◄── [http_service] ◄── anyone with the URL + admin login
Backend /metrics ─► Alloy ─► Grafana Cloud        (unchanged)
```

| Process | Runs as | Listens on | Data |
|---|---|---|---|
| Alloy (metrics, PID 1) | `alloy` | `0.0.0.0:12345` (pre-existing) | rootfs, **not** `/data` |
| Loki | `alloy` | `127.0.0.1:3100` only | `/data/loki` |
| Vector | `alloy` | nothing inbound | `/data/vector` |
| Grafana | `grafana` | `:3000` — **public**, via `[http_service]` (2026-09-25) | `/data/grafana` |

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
   fly volumes create fly_logs --region yyz --size 5 -a spinr-metrics-agent-yyz
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

Open <https://spinr-metrics-agent-yyz.fly.dev> directly (public, per the
2026-09-25 change above) and log in as `admin`. `fly proxy` still works too
if preferred:

```bash
fly proxy 3000 -a spinr-metrics-agent-yyz
```

then <http://localhost:3000>. Either way, log in as `admin`. Logs are under
**Explore → "Fly logs (Loki, 7 days)"**, for example:

```logql
{app="spinr-backend-yyz"} |= "error"
{app="spinr-backend-yyz"} | json app_level="record.level.name" | app_level="ERROR"
{app="spinr-backend-yyz"} |= "<request_id or ride_id>"
```

**Do not filter on the `level` label for real severity** (a previous
version of this doc suggested `{app="spinr-backend-yyz", level="error"}` —
that was wrong, corrected 2026-09-25). `level` is Vector's/Fly's own
stream-based classification and is `info` for every single backend log
line, because the backend's loguru sink writes everything to stderr
regardless of severity (`server.py`'s `logger.add(sys.stderr, ...)`). The
backend's real severity lives inside the JSON line itself, at
`record.level.name` (loguru's `serialize=True` output) — extract it
explicitly as shown above, and give the extracted field a name other than
`level` (e.g. `app_level`), or Loki silently renames yours to
`level_extracted` to avoid colliding with the pre-existing `level` label,
which is an easy, silent way to end up querying the wrong field. There's
also a **"Backend log analysis" dashboard** (provisioned, see
"Dashboards" below) that does this correctly and shows the counts/trend/
drill-down without hand-writing LogQL.

### Dashboards

`grafana/provisioning/dashboards/files/backend-log-analysis.json` (added
2026-09-25) is auto-provisioned on every deploy — no manual import needed.
It has: INFO/WARNING/ERROR/CRITICAL count stats over the dashboard's time
range, a raw-text crash/segfault-signature stat (for interpreter-level
crashes that bypass loguru entirely), a stacked time-series of log volume
by severity, a drill-down raw-logs panel filterable by an `$app`/`$level`
dropdown, and two "further analysis" panels: one for loguru-captured
exceptions (`record.exception`) and one for raw segfault-style text
matches. It's a Grafana **file-provisioned** dashboard
(`allowUiUpdates: true` in `dashboards.yaml`), so UI edits are allowed but
won't survive a redeploy unless also saved back to the JSON file — treat
the file as the source of truth, same as the datasource config.

Labels available: `app`, `region`, `instance` (Fly machine ID), `level`.
Metrics are under the "Spinr metrics (Grafana Cloud)" data source.

### Server metrics (Fly Prometheus)

Added 2026-09-25. A second dashboard, **"Server metrics (Fly CPU/memory/
disk/network)"**, shows real machine-level resource usage for `$app`
(default `spinr-backend-yyz`): CPU %, memory %, disk %, network in/out,
and load average, per machine, with a `$instance` variable to isolate one
machine or view all.

This is **not** a new exporter — it's Fly.io's own hosted Prometheus
endpoint (`https://api.fly.io/prometheus/spinr_backend/`), which already
has CPU/memory/disk/network for every machine in the org with zero scrape
config on our side. Added as a Grafana datasource
(`grafana/provisioning-optional/fly-prometheus.yaml`, copied into
`provisioning/datasources/` by `entrypoint.sh` only when
`FLY_METRICS_READ_TOKEN` is set — same optional-secret pattern as the
Grafana Cloud metrics datasource above).

- **Token**: `fly tokens create readonly spinr_backend` (20-year expiry by
  default; add `-x <duration>` for a shorter one). The printed value
  **already includes its own `FlyV1 ` prefix** — store the whole string as
  the secret, don't add another prefix.
  ```bash
  fly secrets set -a spinr-metrics-agent-yyz \
    "FLY_METRICS_READ_TOKEN=FlyV1 <the rest of what the command printed>"
  ```
- **This is machine/OS-level metrics only** (CPU, memory, disk, network) —
  not the app's own custom counters (dispatch latency, fare calc duration,
  payment settlement, etc. per `CLAUDE.md`'s metric-naming conventions).
  Those are a separate thing: Alloy already remote-writes them to Grafana
  Cloud (the original ADR-010 MVP), but viewing them **in this
  self-hosted Grafana** needs `GRAFANA_CLOUD_METRICS_READ_TOKEN`, which is
  still unset as of this writing — see "Logs & Grafana" secrets above. In
  the meantime they're visible directly in Grafana Cloud's own UI.
- **PromQL note**: `fly_instance_cpu` is a per-core, per-mode cumulative
  counter (`mode` label: `idle`, `user`, `system`, ...) — the same shape as
  node_exporter's `node_cpu_seconds_total`. CPU used % is `rate()` over
  everything-but-idle divided by `rate()` over all modes, not a direct
  gauge read. All four dashboard formulas (CPU/memory/disk/network) were
  verified against live data before shipping, not written from memory of
  Fly's metric names.
- **Sentry was explicitly skipped** (2026-09-25): no native Grafana
  datasource without installing a plugin, which conflicts with this image's
  `GF_PLUGINS_PREINSTALL_DISABLED=true`. Supabase and Redis were deferred
  here and then built the same day with scoped-down credentials — next
  section.

### Supabase, Redis & the local metrics store

Added 2026-09-25. Three more dashboards, each on a credential scoped to the
minimum — none of them uses `SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL` or
`REDIS_URL`. Full record, including the exact SQL/ACL commands and every
verification: `docs/change-log/2026-09-25-metrics-agent-supabase-redis-metrics.md`.

**Local metrics store (VictoriaMetrics).** This Grafana had no metrics
database of its own — Alloy only remote-wrote to Grafana Cloud, which this
Grafana can't read (`GRAFANA_CLOUD_METRICS_READ_TOKEN` is unset). A
single-node VictoriaMetrics (`127.0.0.1:8428`, no auth, loopback only,
`/data/victoria-metrics`, 30-day retention, `-memory.allowedBytes=96MiB`) now
runs under the same `supervise` loop as Loki. Alloy writes to it in addition
to Grafana Cloud (backend metrics go to both, unchanged for Grafana Cloud;
Redis/Supabase go to the local store only, so Grafana Cloud's series count
doesn't grow). Grafana datasource: "Local metrics (VictoriaMetrics, 30
days)". Measured 57 MiB RSS; whole machine ~600 of 962 MiB.

How the optional pieces switch on: `entrypoint.sh` assembles
`/etc/alloy/generated.alloy` at boot from `config.alloy` plus
`alloy-fragments/*.alloy` — `local-store.alloy` only if VictoriaMetrics
started, `redis.alloy` / `supabase.alloy` only if their secret is set. A
missing secret is one `WARNING` line at boot, not an auth error every
scrape. If assembly itself fails, Alloy falls back to the untouched
`config.alloy` (backend → Grafana Cloud only), so metrics never stop.

**"Supabase Postgres (production health)" dashboard.** Datasource connects
as the dedicated `grafana_monitor` role (secret `SUPABASE_MONITOR_PASSWORD`):
LOGIN, CONNECTION LIMIT 3, `statement_timeout 5s`,
`default_transaction_read_only`, SELECT on **0** of 167 app tables, no
`public` schema usage, **not** in `pg_monitor`/`pg_read_all_stats` (those
would expose every session's query text, which can carry PII). Connection
counts come from `monitoring.connection_summary()` (migration 489) — a
`SECURITY DEFINER` function returning counts/ages only, executable by
`grafana_monitor` and nothing else (`anon`/`authenticated` verified denied).
Without it, Postgres hides other sessions' `backend_type` from an
unprivileged role, and a naive count reported **1** connection vs the real
7. The dashboard also has a "Database errors — as seen by the backend" row
(Loki): WARNING/ERROR/CRITICAL backend lines mentioning Supabase, PostgREST
(`PGRST`/`APIError`), "Database operation failed" or GOAWAY. Supabase's
*own* Postgres/API logs are **not** here — that needs a Supabase log drain
($60/month + $0.20 per million events + egress, Pro plan), which was
declined; use Supabase's Log Explorer for those.

**"Redis (Upstash) health" dashboard.** Redis is Fly-managed Upstash
(`spinr-redis`, pay-as-you-go). Alloy's built-in Redis exporter connects as
the ACL user `grafana_exporter`: `+info +ping` only, `resetkeys
resetchannels` — verified NOPERM for GET, SET, DEL, KEYS, SCAN, CONFIG, ACL,
FLUSHDB. Scraped every **60s**, because Upstash bills per command. Upstash
quirks found the hard way, all of which matter if you ever recreate this:
- The password **must** come from `ACL GENTOKEN grafana_exporter` (username
  required); Upstash rejects any self-chosen `>password`.
- Upstash rejects per-subcommand grants (`+latency|latest`), and `LATENCY` /
  `SLOWLOG` don't exist on Upstash at all (the exporter logs that once).
- **`INFO` is answered by two nodes (primary + replica) with separate
  counters**, and each scrape lands on either one. A plain `rate()` reads
  every switch as a counter reset — it reported ~69,000 commands/sec when
  the real figure was ~0.2/s. The dashboard follows each node's counter
  separately (rolling 5m max / min) and only counts a minute's increment
  when both nodes were visible before and after it. If you write new Redis
  panels, copy that pattern; don't use a bare `rate()` on `redis_*_total`.
- `used_memory` from Upstash reads a few KB for ~100 keys — not a real
  figure, so memory is deliberately not on the dashboard.

To recreate the Redis user (Fly-internal host; tunnel first):
```bash
fly proxy 16379:6379 fly-spinr-redis.upstash.io -o spinr_backend &
# as the default user (password from `fly redis status spinr-redis`):
ACL GENTOKEN grafana_exporter           # -> <token>
ACL SETUSER grafana_exporter on ><token> resetkeys resetchannels -@all +info +ping
fly secrets set -a spinr-metrics-agent-yyz REDIS_EXPORTER_PASSWORD=<token>
```

**Supabase server metrics (CPU/memory/disk/IO trends) — wired, awaiting a
key.** `alloy-fragments/supabase.alloy` scrapes Supabase's own Prometheus
endpoint (`/customer/v1/privileged/metrics`, basic auth `service_role` /
`SUPABASE_METRICS_SECRET_KEY`) every 60s into the local store. The key is
held by **Alloy only**, never by the public Grafana. It must be a
*dedicated* secret key (Supabase → Settings → API Keys → Secret keys →
"grafana-metrics") so it can be revoked without touching the backend:
```bash
fly secrets set -a spinr-metrics-agent-yyz SUPABASE_METRICS_SECRET_KEY=sb_secret_...
```
A trend dashboard for it gets built once real series exist to verify
metric names against.

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
- **Sizing is a guess.** The 1 GB VM (with 512 MB swap and per-process
  `GOMEMLIMIT` caps in `entrypoint.sh`) and the 5 GB volume have not been
  measured against real log
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
  **This bit us 2026-09-25:** `Dockerfile`'s `COPY --from=vector /usr/bin/vector
  …` was wrong — the `timberio/vector:0.45.0-distroless-static` image ships
  the binary at `/usr/local/bin/vector`, not `/usr/bin/vector` — so every
  `deploy-metrics-agent.yml` run since this feature merged failed the
  Docker build outright (`"/usr/bin/vector": not found`). The machine kept
  running whatever image had last deployed successfully — an older,
  pre-log-stack `entrypoint.sh` with no Loki/Vector/Grafana code at all —
  so the `fly_logs` volume sat at ~24 KB used with none of `/data/loki`,
  `/data/vector`, `/data/grafana` ever created, and nothing surfaced the
  gap: `fly status`/`fly releases` showed a healthy "complete" deploy the
  whole time, because that deploy really did succeed — it just wasn't
  running the code anyone thought it was. Fixed by correcting the COPY
  source path. If `/data` looks suspiciously empty after a deploy, check
  `deploy-metrics-agent.yml`'s run history before assuming a runtime issue
  — a build that silently fell back to an old image looks identical to a
  healthy deploy from `fly releases` alone.
  `entrypoint.sh`'s one-shot `mountpoint -q /data` check (no retry) was
  also hardened into a 10s-bounded wait loop at the same time, since a
  genuine mount race on a machine replace would otherwise disable the log
  stack for that machine's entire lifetime with only a single easily-missed
  `ERROR` line to show for it.
- **Public exposure (2026-09-25).** Grafana is now reachable from the
  public internet at `https://spinr-metrics-agent-yyz.fly.dev`, gated only
  by its own admin/password login — anonymous access and self-sign-up are
  disabled, but there's no MFA, no rate-limit beyond Grafana's own defaults,
  and no IP allowlist. `GRAFANA_ADMIN_PASSWORD` is now effectively an
  internet-facing credential guarding 7 days of backend logs; rotate it if
  it's ever shared over an insecure channel, and treat a "Grafana login
  failed" spike the same as any other credential-stuffing signal. The
  pre-existing Alloy UI on `:12345` (no auth at all) is **not** exposed by
  `[http_service]` — it remains reachable only from other apps on the org's
  private 6PN network or via `fly proxy`, same as before.
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
