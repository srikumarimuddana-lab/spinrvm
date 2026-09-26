# Change Impact & Risk Log

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (session), requested by user |
| Surface(s) | backend (metrics-agent — standalone Fly app; observability only) |
| Domain (Sentry tag) | admin (observability infra) |
| PR / commit link | (uncommitted at time of writing) |
| Related issue or gap ID | Follow-on to the day's other metrics-agent change-log entries. Supabase and Redis metrics (also requested) are explicitly **not** part of this entry — deferred pending a credential-scoping decision (see "What was NOT verified" and README "Server metrics (Fly Prometheus)"). |

## 1. Issue / gap identified

User wanted a server-usage/stats dashboard (CPU, memory, etc.) for the
backend app, alongside Sentry/Supabase/Redis. Investigated first: the
backend's own `/metrics` endpoint (`backend/utils/metrics.py`) is a
hand-rolled counter/gauge module, not `prometheus_client` — it has **no**
process/OS-level metrics (no CPU, memory, disk) by design (its own
docstring: "internal observability for the Supabase/Redis layer", not a
general metrics library). Building that from scratch would mean adding a
new dependency/exporter to the hardened backend image, which CR-2026-002
deliberately keeps minimal.

## 2. Root cause

Not a bug — a net-new capability gap (no server-level metrics existed
anywhere for this app). Resolved without touching the backend image at
all: Fly.io hosts its own Prometheus-compatible metrics endpoint
(`https://api.fly.io/prometheus/<org>/`) with real CPU/memory/disk/network
per machine, for every Fly app in the org, already being collected by Fly
itself with zero configuration on our side.

## 3. Fix / remediation

- New Grafana datasource (`grafana/provisioning-optional/fly-prometheus.yaml`,
  copied into the live provisioning dir by `entrypoint.sh` only when
  `FLY_METRICS_READ_TOKEN` is set — same optional-secret pattern already
  used for the Grafana Cloud metrics datasource).
- Generated a **read-only, org-scoped** Fly token
  (`fly tokens create readonly spinr_backend`) — not a full read-write
  token — and set it as the new secret.
- `entrypoint.sh`: added the conditional provisioning-copy block and passed
  `FLY_METRICS_READ_TOKEN` explicitly into Grafana's `env -i` invocation
  (Grafana runs with a locked-down environment; a var not explicitly
  listed there is invisible to it, confirmed by reading the existing
  `GRAFANA_CLOUD_METRICS_READ_TOKEN` handling before assuming the same
  pattern would work for a new var).
- New dashboard (`grafana/provisioning/dashboards/files/server-metrics.json`):
  CPU/memory/disk % (avg-over-range stats + per-machine time series), load
  average, and network in/out, with `$app`/`$instance` variables.
- **Every PromQL formula was verified against live data before being put
  in the dashboard** — queried Fly's actual metric names and label shapes
  first (discovered `fly_instance_cpu` is a per-core, per-mode cumulative
  counter needing `rate()`, not a direct gauge — same shape as
  node_exporter's `node_cpu_seconds_total`), then ran each of the four
  formulas (CPU%, memory%, disk%, network rate + the negated "sent" series)
  through the datasource proxy and confirmed plausible real numbers back
  before writing the dashboard JSON. Given the `level`-label bug earlier
  today, formulas are no longer shipped unverified.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `metrics-agent/`.** New datasource + new
  dashboard file + one new Fly secret. No change to the backend app, to
  the log pipeline (Loki/Vector), or to the existing Grafana Cloud metrics
  datasource.
- **New credential, but deliberately low-privilege**: the Fly token is
  `readonly`, scoped to the `spinr_backend` org only — it cannot modify or
  deploy anything, only read metrics/resource metadata. This is a
  materially different risk profile than the Supabase/Redis credentials
  the user also asked for (which would need write-capable-by-default
  keys unless specifically scoped down) — that's exactly why this piece
  was built now and those were deferred.
- Grafana is public (per the same day's earlier change) — this token now
  lives behind that same admin login. Given its read-only/org-scoped
  nature, a compromise exposes infrastructure metrics (machine resource
  usage), not application data, credentials, or write access.

## 5. User-experience effect

None visible to rider/driver/corporate-admin. Affects only internal
Grafana users — a new, genuinely working resource-usage view where there
was none before.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `metrics-agent/grafana/provisioning-optional/fly-prometheus.yaml` | New file — Fly Prometheus datasource | Gated behind the optional secret, same pattern as the existing Grafana Cloud datasource |
| `metrics-agent/entrypoint.sh` | New conditional copy block + `FLY_METRICS_READ_TOKEN` added to Grafana's `env -i` list | Provision the datasource only when the token exists; make the token actually visible to Grafana's locked-down environment |
| `metrics-agent/grafana/provisioning/dashboards/files/server-metrics.json` | New file — the dashboard | CPU/memory/disk/network/load, verified formulas |
| `metrics-agent/fly.toml` | Comment update listing the new optional secret | Keep the secrets inventory accurate |
| `metrics-agent/README.md` | New "Server metrics (Fly Prometheus)" section | Document the token-generation gotcha (already includes `FlyV1 ` prefix), the formula verification, and the explicit Sentry/Supabase/Redis deferral |

## 7. Before / after

```
# Before: no server-level (CPU/mem/disk) metrics existed anywhere for spinr-backend-yyz
```

```
# After: a working, verified dashboard reading Fly's own hosted Prometheus,
# zero new exporters, read-only org-scoped token only
```

## 8. Rollback plan

Remove the new datasource/dashboard files and the `FLY_METRICS_READ_TOKEN`
secret (`fly secrets unset -a spinr-metrics-agent-yyz FLY_METRICS_READ_TOKEN`),
then redeploy. No data or existing config affected — this is a pure
addition with an independent, revocable credential (revoke via `fly
tokens revoke <token>` if compromise is ever suspected, independent of any
other secret on this app).

## 9. Verification performed

- [x] Confirmed the actual metric names/labels via the live datasource
      before writing any panel query (`fly_instance_cpu`, `mode` label
      values, `fly_instance_memory_mem_total`/`mem_available`,
      `fly_instance_filesystem_blocks`/`blocks_avail` + `mount` label,
      `fly_instance_net_recv_bytes`/`net_sent_bytes`,
      `fly_instance_load_average` + `minutes` label).
- [x] Ran all four dashboard formulas directly against the live Fly
      Prometheus datasource via the Grafana proxy API and got plausible
      real values (CPU ~1.3%, memory ~40%, disk ~5.9%, network ~2-3KB/s)
      before shipping — not assumed correct from memory of Prometheus/Fly
      conventions.
- [x] Confirmed the datasource itself is reachable end-to-end
      (`/api/datasources/proxy/uid/fly-prometheus/...` returns real data)
      after deploy, not just that the YAML parses.
- [x] Confirmed both dashboards (`spinr-backend-log-analysis` and
      `spinr-server-metrics`) are provisioned via `GET /api/search`.
- [ ] Not verified: visual rendering in an actual browser (API/data-layer
      verification only, as with the earlier log dashboard).
- [ ] Supabase and Redis metrics are explicitly **not implemented** —
      deferred pending an explicit decision on credential scoping (a
      dedicated read-only Postgres role for Supabase, a restricted Redis
      ACL user for the exporter) rather than reusing the existing
      full-privilege `SUPABASE_SERVICE_ROLE_KEY`/`REDIS_URL`. See README
      "Server metrics (Fly Prometheus)" closing note.

## 10. Sign-off

- [x] Rollback plan is concrete (remove files + secret, redeploy)
- [x] Blast radius stated: isolated to `metrics-agent/`, new credential is
      deliberately read-only/org-scoped
- [x] No silent behavior change — pure addition; Sentry/Supabase/Redis
      were explicitly declined/deferred rather than silently skipped
