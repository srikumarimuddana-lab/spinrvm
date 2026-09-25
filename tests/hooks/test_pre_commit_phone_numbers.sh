#!/bin/bash
# Tests for check 12 of .claude/hooks/pre-commit (real phone numbers in added lines).
# End-to-end against a THROWAWAY git repo (git init in a temp dir) using the
# real git binary — no fake `git` on PATH (a PATH shim that falls back to
# `command git` resolves to itself and fork-bombs). Every hook run is wrapped
# in `timeout 60`.
# Usage: bash tests/hooks/test_pre_commit_phone_numbers.sh   (run from repo root)
#
# Every non-fictional number below is ASSEMBLED AT RUNTIME from 3/4-digit
# pieces, so this file never contains a phone-shaped literal — otherwise the
# hook (and the matching gitleaks rule) would flag this file itself. The
# pieces form a made-up number, not anyone's real one.

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

SRC_ROOT=$(pwd)
HOOK="$SRC_ROOT/.claude/hooks/pre-commit"
[ -f "$HOOK" ] || { echo "run from repo root ($HOOK not found)"; exit 2; }
PASS=0
FAIL=0
WORKDIR=$(mktemp -d)
REPO="$WORKDIR/repo"
OUT_FILE="$WORKDIR/out.txt"
trap 'rm -rf "$WORKDIR"' EXIT

_pass() { echo -e "${GREEN}  PASS${NC} — $1"; ((PASS++)) || true; }
_fail() { echo -e "${RED}  FAIL${NC} — $1"; ((FAIL++)) || true; }

# Throwaway repo: feature branch (check 5), the real .gitleaks.toml (check 1
# passes --config .gitleaks.toml if gitleaks is installed), one base commit.
git init -q "$REPO"
cd "$REPO" || exit 2
git config user.email "test@example.com"
git config user.name "hook test"
git config commit.gpgsign false
git checkout -q -b feature/phone-hook-test
cp "$SRC_ROOT/.gitleaks.toml" .gitleaks.toml
printf 'line %s\n' 1 2 3 4 5 6 7 8 9 > notes.md
git add -A && git commit -q --no-verify -m base

# _reset — drop all staged/unstaged changes back to the base commit
_reset() { git reset -q --hard && git clean -fdq; }

# _run label want_exit — runs the real hook once against the current index
_run() {
  local label="$1" want_exit="$2" got_exit=0
  timeout 60 bash "$HOOK" > "$OUT_FILE" 2>&1 || got_exit=$?
  if [ "$got_exit" -eq "$want_exit" ]; then
    _pass "$label (exit $got_exit)"
  else
    _fail "$label — expected exit $want_exit, got $got_exit"
    grep -iE "phone|BLOCKED" "$OUT_FILE" | sed 's/^/        /'
  fi
}

# _stage_new label want_exit path content — new file with one line, staged
_stage_new() {
  _reset
  mkdir -p "$(dirname "$3")"
  printf '%s\n' "$4" > "$3"
  git add "$3"
  _run "$1" "$2"
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

echo ""
echo "=== Pre-commit check 12 (phone numbers) tests ==="
echo ""
echo "Blocked shapes"
_stage_new "+1 AAA EEE LLLL"   1 a.md "call +1 $A $E $L"
_stage_new "(AAA) EEE-LLLL"    1 a.md "tel: ($A) $E-$L"
_stage_new "AAA-EEE-LLLL"      1 a.md "phone: $A-$E-$L."
_stage_new "AAAEEELLLL"        1 a.py "PHONE = \"$FULL\""
_stage_new "+1AAAEEELLLL"      1 a.py "PHONE = \"+1$FULL\""
_stage_new "AAA.EEE.LLLL"      1 a.md "$A.$E.$L"
_stage_new "1-AAA-EEE-LLLL"    1 a.md "1-$A-$E-$L"
_stage_new "555 exchange outside 01xx still blocked" 1 a.md "$A-555-$L"
_stage_new "one real among fictional" 1 a.md "$A-555-0142 and $A-$E-$L"

echo ""
echo "Masked file:line output"
_reset
printf 'line %s\n' 1 2 3 4 5 6 7 8 9 10 "driver cell $A $E $L" > notes.md
git add notes.md
_run "added line in an existing doc blocked" 1
_out_has   "reports file:line with last 4 only" "notes.md:11: ***-***-$L"
_out_lacks "never echoes the full number (digits)" "$FULL"
_out_lacks "never echoes the full number (formatted)" "$A $E $L"

echo ""
echo "Allowed"
_stage_new "fictional 555-0100..0199, any area code" 0 a.md \
  "$A-555-0142 (416) 555-0199 +1${A}5550100 ${A}.555.0150"
_stage_new "UUID/hex fragment" 0 a.md "id 550e8400-e29b-41d4-a716-${FULL}00 and 0x$FULL"
_stage_new "Unix timestamps (s and ms)" 0 a.md "ts 1727000000 ms 1727000000123"
_stage_new "numeric ID longer than 11 digits" 0 a.md "id 12${FULL}99"
_stage_new "11 digits not starting with 1" 0 a.md "id 2${FULL}"
_stage_new "Stripe-style ID" 0 a.md "pi_3$FULL cus_$FULL"
_stage_new "decimal fraction" 0 a.md "lat 52.$FULL and ${FULL}.5"
_stage_new "int32/uint32 limits" 0 a.md "MAX = 2147483647; UMAX = 4294967295"
_stage_new "bare ID after '-' (GitHub comment URL)" 0 a.md "see pull/1#issuecomment-$FULL"
_stage_new "exchange starting 0/1 is not NANP" 0 a.md "$A-123-$L"
_stage_new "lockfile skipped" 0 admin/package-lock.json "\"x\": \"$FULL\""
_stage_new "yarn.lock skipped" 0 yarn.lock "x $FULL"
_stage_new "svg skipped" 0 icon.svg "<path d=\"M $A $E $L\"/>"

# Removed line: commit a number (bypassing the hook, throwaway repo only),
# then stage its deletion — deletions must never be flagged.
_reset
printf 'OLD = "%s"\nkeep = 1\n' "$FULL" > old.py
git add old.py && git commit -q --no-verify -m "fixture with number"
printf 'keep = 1\n' > old.py
git add old.py
_run "removed line is not scanned" 0
git reset -q --hard HEAD~1

echo ""
echo "Fail closed"
# A broken awk (not git) earlier on PATH: exits 2, cannot recurse.
_reset
printf 'nothing phone-shaped here\n' > a.md
git add a.md
BROKEN_AWK_BIN="$WORKDIR/brokenawk"
mkdir -p "$BROKEN_AWK_BIN"
printf '#!/bin/sh\nexit 2\n' > "$BROKEN_AWK_BIN/awk"
chmod +x "$BROKEN_AWK_BIN/awk"
got_exit=0
PATH="$BROKEN_AWK_BIN:$PATH" timeout 60 bash "$HOOK" > "$OUT_FILE" 2>&1 || got_exit=$?
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
