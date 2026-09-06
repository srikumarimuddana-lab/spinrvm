#!/bin/sh
# Resolves every currently-running machine in the backend Fly app to its
# per-machine .internal address and writes a Prometheus file_sd JSON file
# that Alloy's discovery.file component watches.
#
# WHY query `<app>.internal` directly (not `vms.<app>.internal`): an
# earlier version of this script queried `vms.<app>.internal` for AAAA
# records, believing it published one AAAA record per running machine.
# That's wrong — confirmed via `dig` against the real spinr-backend-yyz
# app, 2026-09-05: `vms.<app>.internal` is a TXT record listing
# "<machine-id> <region>" pairs (e.g. "863994ce20016d yyz,894526a9005298
# yyz"), not an address record, and querying it as AAAA either times out
# or returns nothing depending on the resolver. The plain `<app>.internal`
# name, by contrast, really does return one AAAA record per running
# machine in a single answer (also confirmed via a live `dig AAAA
# spinr-backend-yyz.internal` — both backend machines' addresses came
# back in one response) — so no two-step TXT-then-per-machine lookup is
# needed at all. The original per-machine-scrape *goal* still holds
# (docs/adr/010-metrics-aggregation-and-alerting.md's risk note; issue
# #3295's "Risks of making this change" section: a single scrape target
# pointed at the hostname would only ever reach one machine per TCP
# connection) — it's achieved here because Alloy's prometheus.scrape
# connects to each literal IP this script writes out, never to the
# hostname itself.
#
# Ref: Fly private networking docs, "Discover apps by 6PN network" /
# "Instance Discovery".
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
  # AAAA records for <app>.internal — one per running machine, returned in a
  # single answer (verified via a live `dig`, see the header comment above).
  addrs=$(dig AAAA +short "${BACKEND_APP}.internal" 2>/dev/null | grep -E '^[0-9a-fA-F:]+$' || true)

  if [ -z "$addrs" ]; then
    log "WARNING: no addresses resolved for ${BACKEND_APP}.internal — leaving previous targets file in place (fails open on stale-but-valid targets, not empty)"
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
