#!/usr/bin/env bash
# SessionStart hook — prints sprint header when a new Claude Code session begins.
# Reads .claude/context/sprint-current.md and surfaces the "Sprint goal" + "In-flight" rows.
# Silent if the file is missing or only contains the template placeholders.

set -euo pipefail

SPRINT_FILE=".claude/context/sprint-current.md"
[ -f "$SPRINT_FILE" ] || exit 0

# Skip if the sprint file has not been filled in (template-only content).
if ! grep -qE '^\|\s*[A-Za-z0-9]' "$SPRINT_FILE" && \
   ! grep -qE '^## Sprint goal\s*$' "$SPRINT_FILE"; then
  exit 0
fi

# Extract the sprint goal (one sentence under "## Sprint goal")
GOAL=$(awk '/^## Sprint goal/{flag=1; next} /^## /{flag=0} flag' "$SPRINT_FILE" \
       | sed -e 's/^_.*_$//' -e '/^$/d' | head -1)

# If goal is still the placeholder italic, treat as empty
if [[ -z "${GOAL:-}" ]]; then
  exit 0
fi

echo "────────────────────────────────────────────────"
echo "🚕  Spinr sprint in flight"
echo "    Goal: ${GOAL}"
echo "    (full context: @.claude/context/sprint-current.md)"
echo "────────────────────────────────────────────────"
