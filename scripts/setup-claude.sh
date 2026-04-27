#!/usr/bin/env bash
# setup-claude.sh — one-time per-clone bootstrap for Claude Code in Spinr.
#
# Idempotent: safe to re-run. Never overwrites a customised settings.local.json
# or a pre-commit hook that differs from the shipped version without asking.
#
# What it does:
#   1. Installs .claude/hooks/pre-commit into .git/hooks/pre-commit (secret scan)
#   2. Creates .claude/settings.local.json from .claude/mcp.example.json if missing
#   3. Checks that ruff and jq are on PATH (advisory — hooks degrade gracefully)
#   4. Reminds about the ANTHROPIC_API_KEY GitHub repo secret
#
# Does NOT touch: settings.json, CLAUDE.md, sprint-current.md, or anything
# outside the current clone.

set -euo pipefail

# Resolve repo root regardless of where the script is run from
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Colours (fall back to empty when stdout isn't a tty)
if [ -t 1 ]; then
  BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
else
  BLUE=''; GREEN=''; YELLOW=''; RED=''; NC=''
fi

step() { echo -e "${BLUE}==>${NC} $*"; }
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}!${NC} $*"; }
skip() { echo -e "  ${YELLOW}·${NC} $*"; }
fail() { echo -e "  ${RED}✗${NC} $*"; }

# Guard: must be inside a git work tree
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  fail "Not inside a git repository — run this from a Spinr clone."
  exit 1
fi
GIT_DIR="$(git rev-parse --git-dir)"

# ---------------------------------------------------------------------------
# 1. Pre-commit hook
# ---------------------------------------------------------------------------
step "Installing pre-commit hook"
SRC_HOOK=".claude/hooks/pre-commit"
DST_HOOK="$GIT_DIR/hooks/pre-commit"

if [ ! -f "$SRC_HOOK" ]; then
  fail "$SRC_HOOK not found — repo may be shallow or broken"
  exit 1
fi

if [ ! -f "$DST_HOOK" ]; then
  cp "$SRC_HOOK" "$DST_HOOK"
  chmod +x "$DST_HOOK"
  ok "Installed $DST_HOOK"
elif cmp -s "$SRC_HOOK" "$DST_HOOK"; then
  skip "Already up to date ($DST_HOOK)"
else
  # Existing hook differs — don't clobber a user's customisation silently
  warn "$DST_HOOK differs from $SRC_HOOK"
  if [ -t 0 ]; then
    read -r -p "    Overwrite? [y/N] " reply
    case "$reply" in
      y|Y|yes|YES)
        cp "$SRC_HOOK" "$DST_HOOK"
        chmod +x "$DST_HOOK"
        ok "Overwrote $DST_HOOK"
        ;;
      *)
        skip "Left existing hook in place"
        ;;
    esac
  else
    skip "Non-interactive shell — left existing hook in place (re-run interactively to overwrite)"
  fi
fi

# ---------------------------------------------------------------------------
# 2. settings.local.json scaffold
# ---------------------------------------------------------------------------
step "Preparing .claude/settings.local.json"
LOCAL_SETTINGS=".claude/settings.local.json"
MCP_TEMPLATE=".claude/mcp.example.json"

if [ -f "$LOCAL_SETTINGS" ]; then
  skip "Already exists — not touching your local config"
elif [ ! -f "$MCP_TEMPLATE" ]; then
  warn "$MCP_TEMPLATE missing — skipping scaffold"
else
  cp "$MCP_TEMPLATE" "$LOCAL_SETTINGS"
  ok "Created $LOCAL_SETTINGS from $MCP_TEMPLATE"
  warn "Edit it to fill in real MCP credentials, or delete it if you don't want MCP servers"
fi

# ---------------------------------------------------------------------------
# 3. Tooling check
# ---------------------------------------------------------------------------
step "Checking hook dependencies"
MISSING=0
for tool in jq ruff; do
  if command -v "$tool" >/dev/null 2>&1; then
    ok "$tool: $(command -v "$tool")"
  else
    warn "$tool not found — hooks that need it will skip silently"
    MISSING=1
  fi
done
if [ "$MISSING" = "1" ]; then
  echo "    Install tips:"
  echo "      jq   → brew install jq        (macOS) / apt install jq        (Debian)"
  echo "      ruff → pip install ruff        (or 'pip install -r backend/requirements.txt')"
fi

# ---------------------------------------------------------------------------
# 4. Reminders (human actions we can't automate)
# ---------------------------------------------------------------------------
step "Manual follow-ups"
cat <<EOF
  • GitHub repo secret ANTHROPIC_API_KEY must be set for
    .github/workflows/claude-review.yml to run on PRs.
  • Fill in .claude/context/sprint-current.md at the start of every sprint —
    the SessionStart hook surfaces it in every Claude session.
  • See .claude/README.md for the full layout and permissions model.
EOF

echo ""
ok "Claude Code setup complete for this clone"
