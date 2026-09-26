#!/bin/sh
# Runs the Fly per-machine target-discovery loop in the background, then
# execs Alloy in the foreground (PID 1) so Fly's `kill_signal`/healthcheck
# semantics apply to the process that actually matters.
#
# Also starts the optional log stack (README "Logs & Grafana"): Loki (log
# store on the /data volume), Vector (Fly log stream -> Loki) and Grafana
# (UI, reached only via `fly proxy`). Metrics come first: each log-stack
# component runs in its own restart loop in the background, and a missing
# setting or a crash there is reported loudly but never stops Alloy.
#
# Starts as root only to prepare /data and Grafana's provisioning; every
# long-running process is started as an unprivileged user via setpriv.
set -eu

# Fail fast and loud if required config is missing — never start Alloy in a
# half-configured state that would silently scrape nothing or remote_write
# nowhere (CLAUDE.md: "do not silently swallow errors").
: "${BACKEND_APP_NAME:?BACKEND_APP_NAME env var is required (e.g. spinr-backend-yyz)}"
: "${METRICS_AUTH_TOKEN:?METRICS_AUTH_TOKEN env var is required (Fly secret, must match the backend app's own METRICS_AUTH_TOKEN)}"
: "${GRAFANA_REMOTE_WRITE_URL:?GRAFANA_REMOTE_WRITE_URL env var is required (Fly secret)}"
: "${GRAFANA_REMOTE_WRITE_USERNAME:?GRAFANA_REMOTE_WRITE_USERNAME env var is required (Fly secret)}"
: "${GRAFANA_REMOTE_WRITE_API_KEY:?GRAFANA_REMOTE_WRITE_API_KEY env var is required (Fly secret)}"

BASE_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Soft memory caps for the Go processes on this 1 GB machine (fly.toml):
# the Go runtime collects garbage harder as it nears the cap instead of
# growing until the machine is OOM-killed (which would take metrics down
# too). Budget: Alloy 250 + Loki 300 + Grafana 200 + Vector (Rust, ~60,
# uncapped) + VictoriaMetrics 150 + OS ~100 = ~1060 MiB of soft caps on a
# 962 MiB machine, with 512 MB swap as a backstop. These are ceilings, not
# usage: measured peak for the whole machine was 434 MiB (Fly metrics,
# 6h window, 2026-09-25) before VictoriaMetrics was added.
ALLOY_GOMEMLIMIT="250MiB"
LOKI_GOMEMLIMIT="300MiB"
GRAFANA_GOMEMLIMIT="200MiB"
VM_GOMEMLIMIT="150MiB"
# Set to 1 by start_log_stack once VictoriaMetrics is launched; gates the
# local-store Alloy fragments and Grafana datasource below.
LOCAL_STORE=0

supervise() {
  # supervise <name> <cmd...> — restart <cmd> forever, reporting every exit.
  name="$1"
  shift
  while :; do
    echo "[entrypoint] starting ${name}"
    if "$@"; then rc=0; else rc=$?; fi
    if [ "$rc" -eq 0 ]; then
      echo "[entrypoint] WARNING: ${name} exited cleanly (status 0) but should run forever; restarting in 10s" >&2
    else
      echo "[entrypoint] ERROR: ${name} exited with status ${rc}; restarting in 10s" >&2
    fi
    sleep 10
  done
}

start_log_stack() {
  if [ "${LOGS_STACK_ENABLED:-true}" != "true" ]; then
    echo "[entrypoint] log stack disabled (LOGS_STACK_ENABLED=${LOGS_STACK_ENABLED}); metrics only"
    return 0
  fi
  # Fly mounts the volume before this entrypoint starts, but the check is
  # given a short grace period anyway in case a machine replace ever lags
  # (observed empty 2026-09-25: not this exact cause that day - the deployed
  # image was stale - but a one-shot check here would permanently disable
  # the log stack for the machine's whole lifetime on a genuine race, with
  # no automatic recovery short of a manual restart).
  mount_wait=0
  while ! mountpoint -q /data; do
    if [ "$mount_wait" -ge 10 ]; then
      echo "[entrypoint] ERROR: /data is not a mounted Fly volume after ${mount_wait}s; NOT starting Loki/Vector/Grafana (logs would be lost on every restart). Create the volume per metrics-agent/README.md." >&2
      return 0
    fi
    sleep 1
    mount_wait=$((mount_wait + 1))
  done

  if ! { chmod 755 /data \
      && mkdir -p /data/loki /data/vector /data/grafana /data/victoria-metrics \
      && chown alloy:alloy /data/loki /data/vector /data/victoria-metrics \
      && chown grafana:grafana /data/grafana \
      && chmod 700 /data/loki /data/vector /data/grafana /data/victoria-metrics; }; then
    echo "[entrypoint] ERROR: could not prepare /data for the log stack; NOT starting Loki/Vector/Grafana." >&2
    return 1
  fi

  # Loki: 127.0.0.1 only, so no auth of its own is needed.
  supervise loki env -i PATH="$BASE_PATH" HOME=/data/loki GOMEMLIMIT="$LOKI_GOMEMLIMIT" \
    /usr/bin/setpriv --reuid=alloy --regid=alloy --init-groups --no-new-privs \
    /usr/local/bin/loki -config.file=/etc/loki/loki.yaml &

  # VictoriaMetrics: local metrics history for this Grafana (Redis, Supabase
  # server metrics, and a copy of the backend's own metrics). 127.0.0.1 only
  # and no auth of its own, same as Loki -- Grafana reaches it locally.
  supervise victoria-metrics env -i PATH="$BASE_PATH" HOME=/data/victoria-metrics \
    GOMEMLIMIT="$VM_GOMEMLIMIT" \
    /usr/bin/setpriv --reuid=alloy --regid=alloy --init-groups --no-new-privs \
    /usr/local/bin/victoria-metrics \
    -storageDataPath=/data/victoria-metrics \
    -retentionPeriod=30d \
    -httpListenAddr=127.0.0.1:8428 \
    -memory.allowedBytes=96MiB \
    -loggerLevel=WARN &
  LOCAL_STORE=1

  # Vector: needs the org slug + a read-only org token for Fly's log stream.
  if [ -n "${LOG_STREAM_ORG:-}" ] && [ -n "${LOG_STREAM_ACCESS_TOKEN:-}" ]; then
    supervise vector env -i PATH="$BASE_PATH" HOME=/data/vector VECTOR_LOG=warn \
      LOG_STREAM_ORG="$LOG_STREAM_ORG" \
      LOG_STREAM_ACCESS_TOKEN="$LOG_STREAM_ACCESS_TOKEN" \
      LOG_STREAM_SUBJECT="${LOG_STREAM_SUBJECT:-logs.>}" \
      /usr/bin/setpriv --reuid=alloy --regid=alloy --init-groups --no-new-privs \
      /usr/local/bin/vector --config /etc/vector/vector.toml &
  else
    echo "[entrypoint] ERROR: LOG_STREAM_ORG / LOG_STREAM_ACCESS_TOKEN not set; NOT starting Vector, so no Fly logs are being collected." >&2
  fi

  # Grafana: never start it with a default or weak admin password — it is
  # reachable from the org's private network, not just via `fly proxy`.
  grafana_admin_password=${GRAFANA_ADMIN_PASSWORD:-}
  if [ "${#grafana_admin_password}" -lt 16 ]; then
    echo "[entrypoint] ERROR: GRAFANA_ADMIN_PASSWORD unset or shorter than 16 chars; NOT starting Grafana." >&2
    return 0
  fi

  metrics_read_token="${GRAFANA_CLOUD_METRICS_READ_TOKEN:-}"
  prom_query_url="${GRAFANA_CLOUD_PROM_QUERY_URL:-${GRAFANA_REMOTE_WRITE_URL%/push}}"
  if [ -n "$metrics_read_token" ]; then
    if ! cp /etc/grafana/provisioning-optional/prometheus-grafana-cloud.yaml \
      /etc/grafana/provisioning/datasources/; then
      echo "[entrypoint] ERROR: could not provision the Grafana Cloud metrics data source; Grafana will show logs only." >&2
    fi
  else
    echo "[entrypoint] WARNING: GRAFANA_CLOUD_METRICS_READ_TOKEN not set; Grafana will show logs only (no metrics data source)." >&2
  fi

  fly_metrics_read_token="${FLY_METRICS_READ_TOKEN:-}"
  if [ -n "$fly_metrics_read_token" ]; then
    if ! cp /etc/grafana/provisioning-optional/fly-prometheus.yaml \
      /etc/grafana/provisioning/datasources/; then
      echo "[entrypoint] ERROR: could not provision the Fly server-metrics data source." >&2
    fi
  else
    echo "[entrypoint] WARNING: FLY_METRICS_READ_TOKEN not set; no Fly server-metrics (CPU/mem/disk) data source." >&2
  fi

  if [ "$LOCAL_STORE" = "1" ]; then
    if ! cp /etc/grafana/provisioning-optional/local-metrics.yaml \
      /etc/grafana/provisioning/datasources/; then
      echo "[entrypoint] ERROR: could not provision the local metrics (VictoriaMetrics) data source." >&2
    fi
  fi

  supabase_monitor_password="${SUPABASE_MONITOR_PASSWORD:-}"
  if [ -n "$supabase_monitor_password" ]; then
    if ! cp /etc/grafana/provisioning-optional/supabase-postgres.yaml       /etc/grafana/provisioning/datasources/; then
      echo "[entrypoint] ERROR: could not provision the Supabase Postgres data source." >&2
    fi
  else
    echo "[entrypoint] WARNING: SUPABASE_MONITOR_PASSWORD not set; no Supabase data source." >&2
  fi

  supervise grafana env -i PATH="$BASE_PATH" HOME=/data/grafana \
    GOMEMLIMIT="$GRAFANA_GOMEMLIMIT" \
    GF_PATHS_HOME=/usr/share/grafana \
    GF_PATHS_DATA=/data/grafana \
    GF_PATHS_PLUGINS=/data/grafana/plugins \
    GF_PATHS_LOGS=/data/grafana/log \
    GF_PATHS_PROVISIONING=/etc/grafana/provisioning \
    GF_LOG_MODE=console \
    GF_SERVER_HTTP_PORT=3000 \
    GF_SECURITY_ADMIN_USER=admin \
    "GF_SECURITY_ADMIN_PASSWORD=$grafana_admin_password" \
    GF_SECURITY_DISABLE_GRAVATAR=true \
    GF_AUTH_ANONYMOUS_ENABLED=false \
    GF_USERS_ALLOW_SIGN_UP=false \
    GF_ANALYTICS_REPORTING_ENABLED=false \
    GF_ANALYTICS_CHECK_FOR_UPDATES=false \
    GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false \
    GF_NEWS_NEWS_FEED_ENABLED=false \
    GF_PLUGINS_PREINSTALL_DISABLED=true \
    GRAFANA_CLOUD_PROM_QUERY_URL="$prom_query_url" \
    GRAFANA_REMOTE_WRITE_USERNAME="$GRAFANA_REMOTE_WRITE_USERNAME" \
    GRAFANA_CLOUD_METRICS_READ_TOKEN="$metrics_read_token" \
    FLY_METRICS_READ_TOKEN="$fly_metrics_read_token" \
    SUPABASE_MONITOR_PASSWORD="$supabase_monitor_password" \
    /usr/bin/setpriv --reuid=grafana --regid=grafana --init-groups --no-new-privs \
    /usr/share/grafana/bin/grafana server --homepath=/usr/share/grafana &
}

# Called inside `if` so `set -e` can never abort the entrypoint from here:
# a log-stack failure must not stop Alloy from starting below.
if ! start_log_stack; then
  echo "[entrypoint] ERROR: log stack setup failed; continuing with metrics only." >&2
fi

/usr/bin/setpriv --reuid=alloy --regid=alloy --init-groups --no-new-privs \
  /usr/local/bin/discover-targets.sh &

# Alloy's config = config.alloy + optional fragments, chosen at boot by which
# pieces can actually work: the local-store fragments only when
# VictoriaMetrics was started, and each credentialed source only when its
# secret is set (so a missing secret is one WARNING line here, not an auth
# error every scrape).
ALLOY_CONFIG=/etc/alloy/config.alloy
assemble_alloy_config() {
  out=/etc/alloy/generated.alloy
  cp /etc/alloy/config.alloy "$out" || return 1
  if [ "$LOCAL_STORE" != "1" ]; then
    echo "[entrypoint] WARNING: local metrics store not running; Redis/Supabase metrics off, backend metrics go to Grafana Cloud only." >&2
  else
    sed -i 's#/\*LOCAL_STORE_FORWARD\*/#, prometheus.remote_write.local.receiver#' "$out" || return 1
    cat /etc/alloy/fragments/local-store.alloy >> "$out" || return 1
    if [ -n "${REDIS_EXPORTER_PASSWORD:-}" ]; then
      cat /etc/alloy/fragments/redis.alloy >> "$out" || return 1
    else
      echo "[entrypoint] WARNING: REDIS_EXPORTER_PASSWORD not set; no Redis metrics." >&2
    fi
    if [ -n "${SUPABASE_METRICS_SECRET_KEY:-}" ]; then
      cat /etc/alloy/fragments/supabase.alloy >> "$out" || return 1
    else
      echo "[entrypoint] WARNING: SUPABASE_METRICS_SECRET_KEY not set; no Supabase server metrics." >&2
    fi
  fi
  chown alloy:alloy "$out" || return 1
  ALLOY_CONFIG="$out"
}
if ! assemble_alloy_config; then
  echo "[entrypoint] ERROR: could not assemble Alloy config from fragments; running the base config.alloy (backend -> Grafana Cloud only)." >&2
  ALLOY_CONFIG=/etc/alloy/config.alloy
fi

GOMEMLIMIT="$ALLOY_GOMEMLIMIT"
export GOMEMLIMIT
exec /usr/bin/setpriv --reuid=alloy --regid=alloy --init-groups --no-new-privs \
  /bin/alloy run "$ALLOY_CONFIG" \
  --storage.path=/var/lib/alloy/data \
  --server.http.listen-addr=0.0.0.0:12345
