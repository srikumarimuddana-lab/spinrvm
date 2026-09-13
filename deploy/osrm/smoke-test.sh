#!/usr/bin/env bash
# Verify an OSRM server has the Saskatchewan road graph loaded and that
# /match (map-matching) works — the exact call Spinr uses for billable
# distance (backend/utils/route_distance.py).
#
# Usage:
#   OSRM_URL=https://your-osrm.up.railway.app deploy/osrm/smoke-test.sh
#   OSRM_URL=http://localhost:5000            deploy/osrm/smoke-test.sh
#
# Built with EXTRA_REGION_URLS (multi-province)? Add EXPECT_ALBERTA=1 to also
# assert the Alberta half of the merge actually landed:
#   EXPECT_ALBERTA=1 OSRM_URL=... deploy/osrm/smoke-test.sh
#
# Note: OSRM coordinates are {lng},{lat} (longitude first). The traces below
# are real Regina, SK points.

set -euo pipefail
: "${OSRM_URL:?set OSRM_URL to your OSRM base URL, no trailing slash}"
base="${OSRM_URL%/}"
expect_alberta="${EXPECT_ALBERTA:-}"

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

# 3) Multi-province builds only.
#
#    `"code":"Ok"` on an Alberta coordinate proves nothing on its own: OSRM
#    always snaps to the nearest road it HAS, so an SK-only graph happily
#    answers Ok for Edmonton by snapping ~230 km east to the Saskatchewan
#    border. The snap distance is what distinguishes them, and /nearest reports
#    it directly — its `distance` is metres from the query point to the matched
#    road, and it is the only "distance" key in the response, so it parses
#    unambiguously.
#
#    Deliberately NOT a Lloydminster cross-border route: the provincial
#    boundary runs through that city, so SK roads sit a few hundred metres from
#    any AB coordinate there and an SK-only graph would return a short, sane
#    looking route — a false pass. Edmonton is far enough inside Alberta that
#    the two cases differ by three orders of magnitude.
if [ -n "$expect_alberta" ]; then
  echo "→ /nearest  (Edmonton, AB — did the SK+AB merge land?)"
  if ! resp="$(curl -fsS "$base/nearest/v1/driving/-113.4938,53.5461?number=1")"; then
    fail "request failed — is the service up?"
  fi
  echo "  response: $resp"
  if ! echo "$resp" | grep -q '"code":"Ok"'; then
    fail "Edmonton not Ok — EXTRA_REGION_URLS did not reach the build"
  fi
  snap="$(printf '%s' "$resp" | grep -o '"distance":[0-9.]*' | head -1 | cut -d: -f2)"
  if [ -z "$snap" ]; then
    fail "no snap distance in response — cannot confirm the merge"
  fi
  printf '  snapped %.0f m from the query point\n' "$snap"
  # A real Edmonton road is metres away. The SK-border fallback is ~230 km.
  if [ "$(printf '%.0f' "$snap")" -lt 5000 ]; then
    pass "PASS — Alberta is in the graph; the SK+AB merge took"
  else
    fail "snapped $(printf '%.0f' "$snap") m away — that is the SK border, so the graph is still SK-only"
  fi
fi
