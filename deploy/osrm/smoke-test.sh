#!/usr/bin/env bash
# Verify an OSRM server has the Saskatchewan road graph loaded and that
# /match (map-matching) works — the exact call Spinr uses for billable
# distance (backend/utils/route_distance.py).
#
# Usage:
#   OSRM_URL=https://your-osrm.up.railway.app deploy/osrm/smoke-test.sh
#   OSRM_URL=http://localhost:5000            deploy/osrm/smoke-test.sh
#
# Note: OSRM coordinates are {lng},{lat} (longitude first). The traces below
# are real Regina, SK points.

set -euo pipefail
: "${OSRM_URL:?set OSRM_URL to your OSRM base URL, no trailing slash}"
base="${OSRM_URL%/}"

pass() { printf '  \033[0;32m✅ %s\033[0m\n' "$1"; }
fail() { printf '  \033[0;31m❌ %s\033[0m\n' "$1"; exit 1; }

# 1) Is the region loaded at all? /nearest snaps one point to the road graph.
echo "→ /nearest  (is Saskatchewan loaded?)"
if curl -fsS "$base/nearest/v1/driving/-104.6189,50.4452" | grep -q '"code":"Ok"'; then
  pass "region responds"
else
  fail "NoMatch / not Ok — the SK extract isn't loaded (or wrong region)"
fi

# 2) Map-match a short ~2 km Regina trace. gaps=ignore + tidy=true mirror the
#    backend's request options.
echo "→ /match    (map-matching a Regina trace)"
trace="-104.6178,50.4452;-104.6189,50.4378;-104.6205,50.4291"
resp="$(curl -fsS "$base/match/v1/driving/$trace?overview=false&gaps=ignore&tidy=true")"
echo "  response: $resp"
if echo "$resp" | grep -q '"code":"Ok"'; then
  pass "PASS — map-matching is live; backend can bill on road distance"
else
  fail "FAIL — expected code:Ok (check profile=driving, algorithm=mld, region=SK)"
fi
