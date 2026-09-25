#!/bin/bash
# Tests for check 12 of .claude/hooks/pre-commit (real phone numbers in added lines).
# Runs the hook against synthetic staged diffs via a git shim (same approach as
# tests/hooks/test_pre_commit.sh) and asserts block/allow plus masked output.
# Usage: bash tests/hooks/test_pre_commit_phone_numbers.sh   (run from repo root)
#
# Every non-fictional number below is ASSEMBLED AT RUNTIME from 3/4-digit
# pieces, so this file never contains a phone-shaped literal — otherwise the
# hook (and the matching gitleaks rule) would flag this file itself. The
# pieces form a made-up number, not anyone's real one.

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

HOOK=".claude/hooks/pre-commit"
PASS=0
FAIL=0
WORKDIR=$(mktemp -d)
PATCH_FILE="$WORKDIR/patch.txt"
OUT_FILE="$WORKDIR/out.txt"
trap 'rm -rf "$WORKDIR"' EXIT

_pass() { echo -e "${GREEN}  PASS${NC} — $1"; ((PASS++)) || true; }
_fail() { echo -e "${RED}  FAIL${NC} — $1"; ((FAIL++)) || true; }

FAKEBIN="$WORKDIR/fakebin"
mkdir -p "$FAKEBIN"
# Resolve the real git BEFORE the shim is on PATH. A shim fallback of
# `command git` finds the shim itself again (command only skips functions,
# not PATH) and recurses forever on any call it does not intercept — e.g.
# check 9's `git rev-parse --show-toplevel`.
REAL_GIT=$(command -v git)
cat > "$FAKEBIN/git" <<SHIM
#!/bin/bash
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
chmod +x "$FAKEBIN/git"

# _run label patch want_exit — runs the whole hook, output captured to $OUT_FILE
_run() {
  local label="$1" want_exit="$3" got_exit=0
  printf '%s\n' "$2" > "$PATCH_FILE"
  PATH="$FAKEBIN:$PATH" bash "$HOOK" > "$OUT_FILE" 2>&1 || got_exit=$?
  if [ "$got_exit" -eq "$want_exit" ]; then
    _pass "$label (exit $got_exit)"
  else
    _fail "$label — expected exit $want_exit, got $got_exit"
    grep -i "phone" "$OUT_FILE" | sed 's/^/        /'
  fi
}

_out_has() {
  if grep -qF -- "$2" "$OUT_FILE"; then _pass "$1"; else _fail "$1 — output lacks: $2"; fi
}
_out_lacks() {
  if grep -qF -- "$2" "$OUT_FILE"; then _fail "$1 — output contains: $2"; else _pass "$1"; fi
}

# Made-up number pieces (area / exchange / line) — see header.
A=306
E=234
L=5678
FULL="$A$E$L"

# _diff path line... — one-file staged diff with real headers, hunk at line 10
_diff() {
  local path="$1"; shift
  printf 'diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n@@ -9,0 +10,%s @@\n' \
    "$path" "$path" "$path" "$path" "$#"
  printf '+%s\n' "$@"
}

echo ""
echo "=== Pre-commit check 12 (phone numbers) tests ==="
echo ""
echo "Blocked shapes"
_run "+1 AAA EEE LLLL"     "+call +1 $A $E $L"        1
_run "(AAA) EEE-LLLL"      "+tel: ($A) $E-$L"         1
_run "AAA-EEE-LLLL"        "+phone: $A-$E-$L."        1
_run "AAAEEELLLL"          "+PHONE = \"$FULL\""       1
_run "+1AAAEEELLLL"        "+PHONE = \"+1$FULL\""     1
_run "AAA.EEE.LLLL"        "+$A.$E.$L"                1
_run "1-AAA-EEE-LLLL"      "+1-$A-$E-$L"              1
_run "555 exchange outside 01xx still blocked" "+$A-555-$L" 1
_run "one real among fictional" "+$A-555-0142 and $A-$E-$L" 1

echo ""
echo "Masked file:line output"
_run "doc diff blocked" "$(_diff docs/notes.md 'intro line' "driver cell $A $E $L")" 1
_out_has   "reports file:line with last 4 only" "docs/notes.md:11: ***-***-$L"
_out_lacks "never echoes the full number (digits)" "$FULL"
_out_lacks "never echoes the full number (formatted)" "$A $E $L"

echo ""
echo "Allowed"
_run "fictional 555-0100..0199, any area code" \
  "+$A-555-0142 (416) 555-0199 +1${A}5550100 ${A}.555.0150" 0
_run "UUID/hex fragment" "+id 550e8400-e29b-41d4-a716-${FULL}00 and 0x$FULL" 0
_run "Unix timestamps (s and ms)" "+ts 1727000000 ms 1727000000123" 0
_run "numeric ID longer than 11 digits" "+id 12${FULL}99" 0
_run "11 digits not starting with 1" "+id 2${FULL}" 0
_run "Stripe-style ID" "+pi_3$FULL cus_$FULL" 0
_run "decimal fraction" "+lat 52.$FULL and ${FULL}.5" 0
_run "int32/uint32 limits" "+MAX = 2147483647; UMAX = 4294967295" 0
_run "bare ID after '-' (GitHub comment URL)" "+see pull/1#issuecomment-$FULL" 0
_run "exchange starting 0/1 is not NANP" "+$A-123-$L" 0
_run "removed line is not scanned" "$(printf 'diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -3,1 +2,0 @@\n-OLD = \"%s\"' "$FULL")" 0
_run "lockfile skipped" "$(_diff admin-dashboard/package-lock.json "\"x\": \"$FULL\"")" 0
_run "yarn.lock skipped" "$(_diff yarn.lock "x $FULL")" 0
_run "svg skipped" "$(_diff shared/assets/icon.svg "<path d=\"M $A $E $L\"/>")" 0
_run "same number in a .py file is NOT skipped" "$(_diff backend/x.py "P = \"$FULL\"")" 1

echo ""
echo "Fail closed"
BROKEN_AWK_BIN="$WORKDIR/brokenawk"
mkdir -p "$BROKEN_AWK_BIN"
printf '#!/bin/sh\nexit 2\n' > "$BROKEN_AWK_BIN/awk"
chmod +x "$BROKEN_AWK_BIN/awk"
printf '%s\n' "+nothing phone-shaped here" > "$PATCH_FILE"
got_exit=0
PATH="$BROKEN_AWK_BIN:$FAKEBIN:$PATH" bash "$HOOK" > "$OUT_FILE" 2>&1 || got_exit=$?
if [ "$got_exit" -eq 1 ]; then _pass "awk failure blocks instead of passing (exit 1)"; else _fail "awk failure — expected exit 1, got $got_exit"; fi
_out_has "says the scan could not run" "phone-number scan could not run"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -eq 0 ]; then
  echo -e "${GREEN}All tests passed${NC}"
  exit 0
else
  echo -e "${RED}$FAIL test(s) failed${NC}"
  exit 1
fi
