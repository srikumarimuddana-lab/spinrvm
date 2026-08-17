#!/bin/sh
# Runs the Fly per-machine target-discovery loop in the background, then
# execs Alloy in the foreground (PID 1) so Fly's `kill_signal`/healthcheck
# semantics apply to the process that actually matters.
set -eu

# Fail fast and loud if required config is missing — never start Alloy in a
# half-configured state that would silently scrape nothing or remote_write
# nowhere (CLAUDE.md: "do not silently swallow errors").
: "${BACKEND_APP_NAME:?BACKEND_APP_NAME env var is required (e.g. spinr-backend-yyz)}"
: "${METRICS_AUTH_TOKEN:?METRICS_AUTH_TOKEN env var is required (Fly secret, must match the backend app's own METRICS_AUTH_TOKEN)}"
: "${GRAFANA_REMOTE_WRITE_URL:?GRAFANA_REMOTE_WRITE_URL env var is required (Fly secret)}"
: "${GRAFANA_REMOTE_WRITE_USERNAME:?GRAFANA_REMOTE_WRITE_USERNAME env var is required (Fly secret)}"
: "${GRAFANA_REMOTE_WRITE_API_KEY:?GRAFANA_REMOTE_WRITE_API_KEY env var is required (Fly secret)}"

/usr/local/bin/discover-targets.sh &

exec /bin/alloy run /etc/alloy/config.alloy \
  --storage.path=/var/lib/alloy/data \
  --server.http.listen-addr=0.0.0.0:12345
