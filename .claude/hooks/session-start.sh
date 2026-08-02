#!/usr/bin/env bash
# SessionStart hook — prints sprint header when a new Claude Code session begins.
# Reads .claude/context/sprint-current.md and surfaces the "Sprint goal" + "In-flight" rows.
# Silent if the file is missing or only contains the template placeholders.
#
# Staleness: a sprint file that has not been committed in over $STALE_AFTER_DAYS days,
# or whose body is marked "Sprint COMPLETE", is announced as stale instead of in-flight —
# otherwise every session opens by presenting a finished sprint as the live objective.

set -euo pipefail

SPRINT_FILE=".claude/context/sprint-current.md"
STALE_AFTER_DAYS=21

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

# --- Staleness detection -----------------------------------------------------
# Every lookup below is best-effort: a missing git, a detached worktree, or a
# stat(1) flavour mismatch must never make this hook exit non-zero.

FILE_EPOCH=""
if command -v git >/dev/null 2>&1; then
  FILE_EPOCH=$(git log -1 --format=%ct -- "$SPRINT_FILE" 2>/dev/null || true)
fi
if [[ ! "${FILE_EPOCH:-}" =~ ^[0-9]+$ ]]; then
  # Fall back to filesystem mtime (GNU stat, then BSD stat).
  FILE_EPOCH=$(stat -c %Y "$SPRINT_FILE" 2>/dev/null || stat -f %m "$SPRINT_FILE" 2>/dev/null || true)
fi

NOW_EPOCH=$(date +%s 2>/dev/null || true)

AGE_DAYS=""
if [[ "${FILE_EPOCH:-}" =~ ^[0-9]+$ ]] && [[ "${NOW_EPOCH:-}" =~ ^[0-9]+$ ]] \
   && [ "$NOW_EPOCH" -ge "$FILE_EPOCH" ]; then
  AGE_DAYS=$(( (NOW_EPOCH - FILE_EPOCH) / 86400 ))
fi

MARKED_COMPLETE=0
if grep -qiF 'Sprint COMPLETE' "$SPRINT_FILE"; then
  MARKED_COMPLETE=1
fi

STALE=0
REASONS=""
if [[ -n "$AGE_DAYS" ]] && [ "$AGE_DAYS" -gt "$STALE_AFTER_DAYS" ]; then
  STALE=1
fi
if [ "$MARKED_COMPLETE" -eq 1 ]; then
  STALE=1
fi

if [ "$STALE" -eq 1 ]; then
  if [[ -n "$AGE_DAYS" ]]; then
    REASONS="${AGE_DAYS} days old"
  else
    REASONS="age unknown"
  fi
  if [ "$MARKED_COMPLETE" -eq 1 ]; then
    REASONS="${REASONS}, marked COMPLETE"
  fi

  echo "────────────────────────────────────────────────"
  echo "⚠️   Spinr sprint context looks stale (${REASONS})"
  echo "    Priorities may be out of date — check @ACTION_ITEMS.md for open [ ] items."
  echo "    (historical context: @.claude/context/sprint-current.md)"
  echo "────────────────────────────────────────────────"
  exit 0
fi

echo "────────────────────────────────────────────────"
echo "🚕  Spinr sprint in flight"
echo "    Goal: ${GOAL}"
echo "    (full context: @.claude/context/sprint-current.md)"
echo "────────────────────────────────────────────────"
