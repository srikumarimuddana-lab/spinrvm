# Change Impact & Risk Log

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (session), requested by user; every production credential/DDL step explicitly approved by the user in-session |
| Surface(s) | backend (metrics-agent Fly app) + **production Supabase Postgres** (new role, migration 489) + **production Upstash Redis** (new ACL user) |
| Domain (Sentry tag) | admin (observability infra) |
| PR / commit link | (uncommitted at time of writing — user commits separately) |
| Related | Same-day metrics-agent entries (vector-copy-path, public-grafana, log-analysis-dashboard, fly-server-metrics) |

## 1. Issue / gap identified

The user wanted Supabase and Redis health (plus Supabase errors, logs and
utilization trends) in the metrics-agent Grafana. That Grafana had no
database/cache visibility at all, and no metrics store of its own to hold
trends — Alloy only remote-wrote to Grafana Cloud, which this Grafana can't
read (`GRAFANA_CLOUD_METRICS_READ_TOKEN` was never set).

## 2. Root cause

Net-new capability, not a bug. The design constraint that shaped it:
Grafana is public (same-day change), so every credential it holds had to be
the minimum possible, and the existing full-privilege credentials
(`SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL`, `REDIS_URL`) were ruled out.

## 3. Fix / remediation

**Local metrics store** — VictoriaMetrics single-node
(`victoriametrics/victoria-metrics:v1.152.0-scratch`, pinned by digest),
loopback-only, `/data/victoria-metrics`, 30-day retention, 96 MiB cache cap.
Alloy writes to it *in addition to* Grafana Cloud. `entrypoint.sh` now
assembles Alloy's config from `config.alloy` + optional fragments
(`alloy-fragments/`) based on what can actually run, falling back to the
untouched base config if assembly fails.

**Supabase (production, project `spinrmobileapp`)**
- New role `grafana_monitor` (created via the Supabase MCP connector; not a
  migration because it carries a password):
  ```sql
  CREATE ROLE grafana_monitor WITH LOGIN PASSWORD '<redacted>' NOSUPERUSER NOCREATEDB
    NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 3;
  ALTER ROLE grafana_monitor SET statement_timeout = '5s';
  ALTER ROLE grafana_monitor SET default_transaction_read_only = on;
  ALTER ROLE grafana_monitor SET idle_in_transaction_session_timeout = '10s';
  ```
  No grants at all. Stored as `SUPABASE_MONITOR_PASSWORD` on metrics-agent.
- **Migration 489** (`backend/migrations/489_monitoring_connection_summary.sql`),
  applied to production via `apply_migration` after the user's explicit
  approval (a first attempt was blocked by the auto-mode classifier as a
  production deploy; not retried until the user said yes):
  `monitoring.connection_summary()` — `SECURITY DEFINER`, pinned
  `search_path`, returns counts/ages only (never query text), executable by
  `grafana_monitor` only. Idempotent, so `run_migrations.py` re-applying it
  when the file lands is harmless.
  **Renumbered 488 → 489 before merge**: `main` gained its own
  `488_drivers_admin_edited_at.sql` in the meantime. Safe to rename because
  `apply_migration` records into Supabase's own migration history (under the
  original name `488_monitoring_connection_summary`), not into the
  `schema_migrations` table `run_migrations.py` keys on — so no applied
  filename changed. When `run_migrations.py` reaches 489 it re-runs the same
  idempotent SQL harmlessly.
- Dashboard "Supabase Postgres (production health)": connections (via the
  function), pool %, oldest open transaction, lock waits, cache hit ratio,
  rollback rate, deadlocks, temp spill, DB size, largest 15 tables, and a
  "Database errors — as seen by the backend" row from Loki.

**Redis (production Upstash `spinr-redis`)**
- New ACL user, password from `ACL GENTOKEN grafana_exporter`:
  `ACL SETUSER grafana_exporter on ><token> resetkeys resetchannels -@all +info +ping`.
  Stored as `REDIS_EXPORTER_PASSWORD`. The admin password was read from
  `fly redis status` into a shell variable only; neither password was ever
  printed (all output passed through a masking filter).
- Alloy `prometheus.exporter.redis` every 60s → local store; dashboard
  "Redis (Upstash) health".

**Supabase server metrics** — `alloy-fragments/supabase.alloy` wired
(Supabase's Prometheus endpoint, basic auth with a dedicated secret key held
by Alloy only). **Inactive until the user sets `SUPABASE_METRICS_SECRET_KEY`.**

**Declined by the user:** a Supabase log drain ($60/month + $0.20/M events +
egress, plus a new public ingest endpoint) — backend-side DB errors in Loki
instead.

## 4. Risk & impact on existing functionality

- **Production Postgres**: one new login role (connection limit 3, 5s
  timeout, read-only default, zero table grants) and one new schema/function.
  Max connections is 90 and ~7 are in use; the role's 3 connections can't
  crowd the app out. Nothing existing was altered — no existing role, table,
  policy, or function was touched. Verified after the migration:
  `grafana_monitor` still reads 0 of 167 app tables, no `public` usage;
  `anon`/`authenticated` can't execute the function or use the schema.
- **Production Redis**: one new ACL user. The `default` user the backend
  uses is untouched. The new user can't read/write any key. Upstash bills
  per command: 60s scrape ≈ 1.4k–3k extra commands/day, under $0.01/day.
- **metrics-agent**: a new process (VictoriaMetrics, 57 MiB measured; whole
  machine ~600 of 962 MiB). Grafana Cloud remote_write is unchanged — the
  backend scrape now fans out to both, and a local-store failure can't block
  Grafana Cloud (separate remote_write queues).
- **No backend, rider-app, driver-app or admin-dashboard change.** Blast
  radius: metrics-agent + two new, isolated, least-privilege production
  credentials + one additive DB function.

## 5. User-experience effect

None for riders, drivers or corporate admins. Internal Grafana users get
three new dashboards.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/489_monitoring_connection_summary.sql` | New — aggregate-only connection stats function | Accurate connection counts without granting query-text visibility |
| `metrics-agent/Dockerfile` | VictoriaMetrics binary (digest-pinned); `alloy-fragments/` copied in | Local metrics store; optional Alloy pieces |
| `metrics-agent/entrypoint.sh` | Start VictoriaMetrics; provision local/Supabase datasources; assemble Alloy config from fragments with fallback | Everything optional switches on only when it can work |
| `metrics-agent/config.alloy` | Backend scrape forward list gets a marker the entrypoint fills in | Copy backend metrics to the local store when it's running |
| `metrics-agent/alloy-fragments/{local-store,redis,supabase}.alloy` | New | Local remote_write; Upstash exporter; Supabase metrics scrape |
| `metrics-agent/grafana/provisioning-optional/{local-metrics,supabase-postgres}.yaml` | New datasources | Gated on the store running / the secret being set |
| `metrics-agent/grafana/provisioning/dashboards/files/{supabase-postgres,redis}.json` | New dashboards | — |
| `metrics-agent/fly.toml`, `metrics-agent/README.md` | Secrets inventory; new README section | Document credentials, Upstash quirks, recreate steps |

## 7. Before / after

```
Before: no DB or cache visibility; no metrics history in the self-hosted Grafana.
After:  Supabase + Redis dashboards on least-privilege credentials; 30-day local
        metrics history; Supabase server metrics one secret away.
```

## 8. Rollback plan

Each piece rolls back independently, none needs a code revert:
- **Redis user**: `ACL DELUSER grafana_exporter` (as `default`), then
  `fly secrets unset -a spinr-metrics-agent-yyz REDIS_EXPORTER_PASSWORD`
  (the fragment then drops out on the restart).
- **Supabase role/function**:
  ```sql
  DROP FUNCTION IF EXISTS monitoring.connection_summary();
  DROP SCHEMA IF EXISTS monitoring;
  DROP ROLE IF EXISTS grafana_monitor;
  ```
  then `fly secrets unset -a spinr-metrics-agent-yyz SUPABASE_MONITOR_PASSWORD`.
- **Supabase server metrics**: revoke the `grafana-metrics` secret key in the
  Supabase dashboard; unset `SUPABASE_METRICS_SECRET_KEY`.
- **Local store**: `LOGS_STACK_ENABLED=false` stops it along with the log
  stack; or redeploy the previous image
  (`registry.fly.io/spinr-metrics-agent-yyz:deployment-01M3DKVX6HFSWKZHSFX8KBM9GH`,
  release v24).
No live application data is written by any of this, so no data-level
remediation is ever needed.

## 9. Verification performed

- [x] `grafana_monitor` privileges proven by query, not assumed: 0/167 tables,
      no `public` usage, 0 executable security-definer functions (before
      489), not in `pg_monitor`; after 489, executes only the new function.
- [x] Caught a wrong number before calling it done: the first "client
      connections" panel reported **1** (restricted role only sees its own
      session's `backend_type`) vs the real 7 — confirmed by comparing both
      views, fixed via migration 489, re-verified at 7.
- [x] Redis ACL user proven by command: PING/INFO OK; GET, SET, DEL, KEYS,
      SCAN, CONFIG, ACL LIST, FLUSHDB all NOPERM.
- [x] Caught a second wrong number: naive Redis `rate()` read ~69,000
      commands/sec; raw samples showed two alternating counter sets
      (8,279,799 → 1,331,078 → 8,279,895). Per-node query rebuilt and
      re-verified at ~0.2–0.9 commands/sec.
- [x] Every panel of both new dashboards executed through Grafana's own
      query API from the *deployed* dashboard JSON, all returning data.
- [x] Alloy config: `alloy fmt` (real v1.4.3 binary) passed on both the
      assembled and the base variants; every attribute checked against the
      v1.4 reference docs. A full local `alloy run` load test was **declined
      by the permission system** and not attempted again — so the first
      real load test was the deploy itself, done with the previous image
      recorded for rollback and all processes checked running afterwards.
- [x] Post-deploy: Alloy running `generated.alloy`, VictoriaMetrics/Loki/
      Vector/Grafana all up, Redis series and backend copy both landing in
      the local store.
- [ ] **Not verified**: the Supabase server-metrics scrape (needs the user's
      key); visual browser rendering of the dashboards (API-level only).

## 10. Sign-off

- [x] Rollback plan is concrete and per-component
- [x] Blast radius stated: two new isolated least-privilege credentials, one
      additive function, metrics-agent only
- [x] Production-touching steps each individually approved by the user
