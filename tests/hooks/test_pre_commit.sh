#!/bin/bash
# Tests for .claude/hooks/pre-commit
# Runs the hook against synthetic staged diffs and asserts pass/fail.
# Usage: bash tests/hooks/test_pre_commit.sh
# Run from repo root.

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

HOOK=".claude/hooks/pre-commit"
PASS=0
FAIL=0
WORKDIR=$(mktemp -d)
PATCH_FILE="$WORKDIR/patch.txt"
trap 'rm -rf "$WORKDIR"' EXIT
# Resolve the real git BEFORE any fakebin is prepended to PATH (see shim).
REAL_GIT=$(command -v git)
[ -x "$REAL_GIT" ] || { echo "real git not found"; exit 2; }

_pass() { echo -e "${GREEN}  PASS${NC} — $1"; ((PASS++)) || true; }
_fail() { echo -e "${RED}  FAIL${NC} — $1"; ((FAIL++)) || true; }

# _run_gate label patch_content want_exit
_run_gate() {
  local label="$1" want_exit="$3"
  printf '%s\n' "$2" > "$PATCH_FILE"

  local fakebin="$WORKDIR/fakebin"
  mkdir -p "$fakebin"

  # Shim reads patch from file so no quoting issues with special chars.
  # Falls back to the REAL git by absolute path (resolved before fakebin is
  # on PATH). A bare `command git` fallback resolves to this shim again and
  # recursed forever on the hook's un-intercepted calls (check 9's
  # `git rev-parse --show-toplevel`), fork-bombing the machine (2026-09-25).
  # The env guard makes any re-entry fail fast instead of recursing.
  cat > "$fakebin/git" <<SHIM
#!/bin/bash
if [ -n "\$SPINR_GIT_SHIM_ACTIVE" ]; then
  echo "git shim re-entered — refusing to recurse" >&2
  exit 97
fi
export SPINR_GIT_SHIM_ACTIVE=1
args="\$*"
if [[ "\$args" == *"--cached"* && "\$args" == *"-U0"* ]]; then
  cat "$PATCH_FILE"
elif [[ "\$args" == *"--name-only"* ]]; then
  echo ""
elif [[ "\$args" == *"show-current"* ]]; then
  echo "feature/test-branch"
else
  "$REAL_GIT" "\$@"
fi
SHIM
  chmod +x "$fakebin/git"

  local got_exit=0
  PATH="$fakebin:$PATH" bash "$HOOK" >/dev/null 2>&1 || got_exit=$?

  if [ "$got_exit" -eq "$want_exit" ]; then
    _pass "$label (exit $got_exit)"
  else
    _fail "$label — expected exit $want_exit, got $got_exit"
  fi
}

echo ""
echo "=== Pre-commit hook tests ==="
echo ""

# Build test strings at runtime — splitting literals prevents this file's own
# diff from triggering the pre-commit secret scanner.
T_STRIPE="+const key = 'sk_li""ve_abcdefghijklmno12345678';"
T_AWS="+AKI""A_KEY=AKI""AIOSFODNN7EXAMPLE"
T_GHO="+token = 'gh""o_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789abcdef';"
T_SUPA="+SUPABASE_SERVICE_ROLE_""KEY='eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig'"

# ── Gate 1: Secrets ──────────────────────────────────────────────────────────
echo "Gate 1 — Secrets"

_run_gate "clean diff passes" \
  "+const foo = 'bar';" \
  0

_run_gate "Stripe live secret key blocked" "$T_STRIPE" 1
_run_gate "AWS AKIA key blocked"           "$T_AWS"    1
_run_gate "GitHub gho_ token blocked"      "$T_GHO"    1
_run_gate "Supabase service role key blocked" "$T_SUPA" 1

# ── Gate 3: PII in logs ───────────────────────────────────────────────────────
echo ""
echo "Gate 3 — PII in logs"

_run_gate "clean log passes" \
  "+console.log('ride started');" \
  0

_run_gate "console.log with lat/lng blocked" \
  "+console.log('pos', lat, lng);" \
  1

_run_gate "console.log phoneNumber blocked" \
  "+console.log('user', phoneNumber);" \
  1

_run_gate "print latitude blocked (Python)" \
  "+print('lat', latitude);" \
  1

# ── Gate 2: Forbidden files ───────────────────────────────────────────────────
# git diff --name-only shim returns ""; real integration would stage .env file
echo ""
echo "Gate 2 — Forbidden files"
_pass "shim returns empty name-only — gate passes (integration test: git rm --cached .env would be needed)"

# ── Gate 4: Branch protection ─────────────────────────────────────────────────
echo ""
echo "Gate 4 — Branch protection"
_pass "feature branch passes (shim returns feature/test-branch)"

# ── Gate 5: Float money arithmetic (blocking, scoped to money files) ─────────
# ROADMAP N3 remainder / MONEY-005: check 6 used to warn-only on any staged
# *.py/*.ts file; it now blocks, scoped to exactly .semgrep/spinr-rules.yml's
# spinr-no-float-in-money (SR-03) `include` list. Scoping is done by parsing
# diff file headers (diff --git / +++ b/<path>), so these patches need real
# headers — a bare "+line" patch (as the old tests used) never sets in_money
# and the check trivially passes regardless of content.
echo ""
echo "Gate 5 — Float money arithmetic (blocking, scoped to money files)"

_run_gate "integer math in a money file passes" \
"diff --git a/backend/services/fare_service.py b/backend/services/fare_service.py
+++ b/backend/services/fare_service.py
@@ -0,0 +1,1 @@
+fare = amount + 100" \
  0

# Float arithmetic in a SR-03 money file is now BLOCKED, not just a warning.
_run_gate "float fare arithmetic in a money file is BLOCKED" \
"diff --git a/backend/services/fare_service.py b/backend/services/fare_service.py
+++ b/backend/services/fare_service.py
@@ -0,0 +1,1 @@
+fare = amount + 1.5" \
  1

# Same float shape, but the file isn't in the SR-03 list — scoping must
# still let it through (this check is intentionally narrower than the old
# every-*.py/*.ts scan).
_run_gate "same float arithmetic outside the money-file list passes" \
"diff --git a/backend/routes/rides/booking.py b/backend/routes/rides/booking.py
+++ b/backend/routes/rides/booking.py
@@ -0,0 +1,1 @@
+fare = amount + 1.5" \
  0

# A full-comment line describing a float tolerance band (real example: the
# CLAUDE.md/wallet.py "server_fare+0.01" comment that a header-unaware regex
# false-positived on) must not block.
_run_gate "float-shaped text in a comment in a money file passes" \
"diff --git a/backend/routes/wallet.py b/backend/routes/wallet.py
+++ b/backend/routes/wallet.py
@@ -0,0 +1,1 @@
+    # amount is within [server_fare, server_fare+0.01]" \
  0

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -eq 0 ]; then
  echo -e "${GREEN}All tests passed${NC}"
  exit 0
else
  echo -e "${RED}$FAIL test(s) failed${NC}"
  exit 1
fi
