# Change Impact & Risk Log — 7-day Fly log store + private Grafana on metrics-agent

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-24 |
| Author | Claude Code session (requested by repo owner) |
| Surface(s) | infra — `metrics-agent/` Fly app (`spinr-metrics-agent-yyz`) only |
| Domain (Sentry tag) | n/a (ops tooling; no application code touched) |
| PR / commit link | branch `claude/clever-edison-ezmqz8` |
| Related issue or gap ID | `docs/audit/2026-09-01-path-to-a-grade.md` §2.3 "One log pipeline" (Loki via Alloy, never built) |

## 1. Issue / gap identified

Fly.io logs for the production backend can't be retrieved beyond `fly logs`' short live buffer. Nothing in the repo keeps them, so a question about an incident from a few days ago can't be answered from logs.

## 2. Root cause

Nothing collects logs. `metrics-agent/` only scrapes `/metrics` and remote-writes to Grafana Cloud. The Loki pipeline proposed in the 2026-09-01 audit was never implemented.

## 3. Fix / remediation

The owner decided to reuse the existing `spinr-metrics-agent-yyz` machine rather than add a new app, to self-host rather than use Grafana Cloud Loki, and to allow access only via `fly proxy`, with no public URL. On that machine:
- **Vector** reads the Fly org log stream (NATS `[fdaa::3]:4223`, read-only org token) and sends it to **Loki**.
- **Loki** is bound to `127.0.0.1`, stores data on a new Fly volume at `/data`, and keeps 168h (7 days), enforced by the compactor.
- **Grafana** listens on `:3000` with no public service. It uses an admin password secret, has anonymous access and sign-up off, and is provisioned with the Loki data source plus an optional Grafana Cloud Prometheus data source that needs a `metrics:read` token.
- `entrypoint.sh` starts as root only to prepare `/data` and provisioning. Every process runs as an unprivileged user via `setpriv`, and Loki, Vector and Grafana get a scrubbed environment (`env -i`) holding only their own secrets.
- VM memory goes from 512 MB to 2 GB.

## 4. Risk & impact on existing functionality

- **Blast radius:** single app (`spinr-metrics-agent-yyz`). No backend, rider-app, driver-app, admin-dashboard, DB, ride-state, money or background-loop code is touched. The backend is only read: Fly already publishes its logs to the org log stream, and Vector subscribes to it.
- **Existing consumer of this app: the Alloy metrics pipeline** (Grafana Cloud dashboards and alert rules in `metrics-agent/grafana/`). Risks to it:
  - *Entrypoint now starts as root, then drops to `alloy` via `setpriv`.* Alloy runs as the same user as before. If `setpriv` were missing from the image, Alloy wouldn't start. `util-linux` is now installed explicitly in the Dockerfile to guard against that.
  - *A log-stack failure blocking Alloy.* Mitigated: `start_log_stack` is called inside `if`, so `set -e` can't abort the entrypoint, and each component runs in its own background restart loop. Stub tests confirmed Alloy still starts with a missing volume, missing secrets, a short password, or a failed `chown`.
  - *Memory contention.* Four processes now share one VM, raised to 2 GB. The per-process budget is an estimate, not a measurement.
  - *Deploy ordering.* `deploy-metrics-agent.yml` auto-deploys on merge, and the new `[mounts]` needs the `fly_logs` volume to exist, with the existing machine replaced. If this merges before README steps 1–3, the deploy fails, and the currently running machine is expected to keep serving metrics (flyctl validates mounts before replacing machines — not verified here).
- **Other Fly apps** (`hermes-spinr`, `spinr-burst-controller-yyz`, `spinr-backend-yyz`): their logs are now also stored for 7 days. Nothing about their behaviour changes.

## 5. User-experience effect

Riders, drivers and corporate admins see nothing. Internal ops/engineering gain a private Grafana at `fly proxy 3000 -a spinr-metrics-agent-yyz`. There's no visible change mid-session and no copy or notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `metrics-agent/Dockerfile` | Copy Loki, Vector and Grafana binaries from tag-pinned images; install `util-linux`; add `grafana` user; copy configs; entrypoint runs as root | Ship the log stack in the existing image |
| `metrics-agent/entrypoint.sh` | Add `start_log_stack` (volume/secret checks, supervised background processes, scrubbed env); Alloy and discovery now started via `setpriv` as `alloy` | Run the log stack without risking metrics |
| `metrics-agent/fly.toml` | `[mounts] fly_logs → /data`; memory 512 MB → 2 GB; `LOGS_STACK_ENABLED`, `LOG_STREAM_ORG` env | Persistent log storage, headroom, kill switch |
| `metrics-agent/loki.yaml` (new) | Single-binary Loki, filesystem storage, 168h retention, 127.0.0.1 only | 7-day log store |
| `metrics-agent/vector.toml` (new) | NATS source → remap (label defaults, timestamp parse) → Loki sink | Collect Fly logs |
| `metrics-agent/grafana/provisioning/datasources/loki.yaml` (new) | Loki data source | Search logs in Grafana |
| `metrics-agent/grafana/provisioning-optional/prometheus-grafana-cloud.yaml` (new) | Grafana Cloud Prometheus data source, installed only when the read token is set | View metrics in the same Grafana |
| `metrics-agent/README.md` | "Logs & Grafana" runbook: setup order, access, operation, known gaps | Operator instructions |
| `.github/workflows/deploy-metrics-agent.yml` | New "Verify fly_logs volume exists" step before deploy | A premature merge now fails with the setup instructions instead of a raw flyctl error |

## 7. Before / after

```sh
# Before (entrypoint.sh, image USER alloy)
/usr/local/bin/discover-targets.sh &
exec /bin/alloy run /etc/alloy/config.alloy ...
```

```sh
# After (image USER root; entrypoint drops privileges)
if ! start_log_stack; then echo "[entrypoint] ERROR: ..." >&2; fi
/usr/bin/setpriv --reuid=alloy --regid=alloy --init-groups --no-new-privs \
  /usr/local/bin/discover-targets.sh &
exec /usr/bin/setpriv --reuid=alloy --regid=alloy --init-groups --no-new-privs \
  /bin/alloy run /etc/alloy/config.alloy ...
```

## 8. Rollback plan

- **Log stack misbehaving, metrics fine:** `fly secrets set LOGS_STACK_ENABLED=false -a spinr-metrics-agent-yyz`. The machine restarts running Alloy only, with no code revert or redeploy. Stored logs remain on the volume.
- **Metrics broken by the new image:** `fly deploy -a spinr-metrics-agent-yyz --image <previous image ref from fly releases>` or `fly releases rollback`. The previous config has no `[mounts]`, so rolling back to it may require destroying the volume-attached machine first. The volume itself can be kept, or deleted with `fly volumes destroy` if the feature is abandoned.
- No live data (rides, Stripe, wallets) is involved, so there's nothing to reverse there.

## 9. Verification performed

- `sh -n` / `dash -n` on `entrypoint.sh`. TOML parsing of `fly.toml` and `vector.toml`, and YAML parsing of `loki.yaml` and both Grafana provisioning files (Python `tomllib`/`yaml`).
- **Stub-binary behaviour test of `entrypoint.sh`** (scratch harness; `setpriv`, `mountpoint`, `chown` and every binary stubbed) across 7 scenarios: full config; no log-stack secrets; short Grafana password and no read token; volume not mounted; kill switch off; missing Alloy secret (must fail, and did); `chown` on `/data` failing. In every non-failing scenario Alloy and discovery started. Grafana received only `GF_*` plus its data-source vars, with no `LOG_STREAM_*` or `METRICS_AUTH_TOKEN`. Loki received no secrets. Vector received only `LOG_STREAM_*`. The derived query URL was `…/api/prom`.
  - This test caught and fixed a real bug: under `set -e`, a failed `cp`/`chown` in the log setup aborted the entrypoint before Alloy started.
- `spinr-cicd-infra-reviewer` agent run against the diff. Fixed in a follow-up commit: Grafana tree now `COPY --chown=grafana` (the upstream UID 472 doesn't exist in this image); `chmod 755 /data`; `supervise` logs a clean exit as WARNING rather than ERROR; deploy workflow guards on the volume existing. Not changed: (a) dropping the `instance` Loki label — the Loki sink uses the `text` codec, so the ID wouldn't be queryable from the body, and Fly keeps machine IDs across rolling deploys, so the number of streams stays small; (b) secrets briefly in `env`'s argv at process start — accepted as low risk and documented in the README's known gaps.

## 10. What was NOT verified

- **No image build and no deploy.** The session had no Docker daemon, registry access, or Fly token. The Dockerfile's `COPY --from` paths (`/usr/bin/loki`, `/usr/bin/vector`, `/usr/share/grafana`) and the existence of the chosen tags are unconfirmed.
- **Configs not run against the real Loki 3.4.2, Vector 0.45.0 or Grafana 11.6.0.** The Go module proxy and crates.io were also blocked, so neither `loki -verify-config` nor a VRL compile was possible. The first deploy is the real validation.
- **Images pinned by tag, not digest**, contrary to this directory's Alloy convention. Versions are ~1.5 years old because they're the newest that could be confirmed without registry access.
- **Fly NATS log-stream auth with a `fly tokens create readonly` org token** is taken from Fly's log-shipper pattern, not tested. The org slug `spinr_backend` is taken from `infra/burst_controller/fly.toml`.
- **flyctl behaviour when adding `[mounts]` to an app whose existing machine has no volume** — the README prescribes destroying the machine first to avoid depending on it.
- **Memory headroom, disk sizing (10 GB), and log volume** are not measured.
- **PII content of existing backend logs** not audited. It's now retained for 7 days, in-region (`yyz`).
