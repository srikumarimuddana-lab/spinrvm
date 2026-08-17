#!/bin/sh
# Resolves every currently-running machine in the backend Fly app to its
# per-machine .internal address and writes a Prometheus file_sd JSON file
# that Alloy's discovery.file component watches.
#
# WHY per-machine, not the app-level `<app>.internal` name: Fly's app-level
# `.internal` DNS load-balances a single resolution to ONE machine, so a
# scrape config pointed at it only ever sees one of the ≥2 running backend
# machines (docs/adr/010-metrics-aggregation-and-alerting.md's risk note;
# issue #3295's "Risks of making this change" section). Fly additionally
# publishes `vms.<app>.internal`, which resolves to one AAAA record PER
# running machine — that fan-out is what makes per-machine scraping
# possible without a Fly Machines API token.
#
# Ref: Fly private networking docs, "Discover apps by 6PN network" /
# "Instance Discovery" (per-machine `<id>.vm.<region>.<app>.internal` and
# the `vms.<app>.internal` multi-A/AAAA record).
set -u

TARGETS_FILE="/etc/alloy/targets.json"
BACKEND_APP="${BACKEND_APP_NAME:-spinr-backend-yyz}"
INTERVAL="${DISCOVERY_INTERVAL_SECONDS:-30}"

log() {
  # Structured-ish, single line; this container has no app logger, so a
  # plain timestamped line to stdout is the loudest reasonable option
  # (Fly captures stdout/stderr).
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) discover-targets: $*"
}

while true; do
  # AAAA records for vms.<app>.internal — one per running machine, per Fly's
  # documented multi-record discovery name.
  addrs=$(dig AAAA +short "vms.${BACKEND_APP}.internal" 2>/dev/null | grep -E '^[0-9a-fA-F:]+$' || true)

  if [ -z "$addrs" ]; then
    log "WARNING: no addresses resolved for vms.${BACKEND_APP}.internal — leaving previous targets file in place (fails open on stale-but-valid targets, not empty)"
  else
    tmp="${TARGETS_FILE}.tmp"
    {
      echo "["
      echo "  {"
      echo "    \"labels\": {\"provider\": \"fly\", \"job\": \"spinr-backend\"},"
      echo "    \"targets\": ["
      first=1
      for a in $addrs; do
        if [ "$first" -eq 1 ]; then
          first=0
        else
          echo ","
        fi
        printf '      "[%s]:8000"' "$a"
      done
      echo ""
      echo "    ]"
      echo "  }"
      echo "]"
    } > "$tmp"
    mv "$tmp" "$TARGETS_FILE"
    count=$(echo "$addrs" | wc -l | tr -d ' ')
    log "wrote $count target(s) to $TARGETS_FILE"
  fi

  sleep "$INTERVAL"
done
