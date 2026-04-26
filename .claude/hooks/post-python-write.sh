#!/usr/bin/env bash
# PostToolUse hook — runs ruff format + ruff check --fix on backend Python files
# that Claude just wrote or edited. Silent on success, advisory on failure.
# Never blocks the turn (exit 0 always).
#
# Rationale: keeps the diff clean with project style without a manual pass.

set -u

INPUT=$(cat || true)
[ -z "$INPUT" ] && exit 0

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null || true)
[ -z "$FILE_PATH" ] && exit 0

# Only act on Python files inside the backend tree
case "$FILE_PATH" in
  */backend/*.py|backend/*.py) ;;
  *) exit 0 ;;
esac

# Only act if the file still exists (could have been deleted by the edit)
[ -f "$FILE_PATH" ] || exit 0

# Only act if ruff is available — silent skip otherwise
command -v ruff >/dev/null 2>&1 || exit 0

# Format first, then autofix lints. Capture output; only surface on failure.
FORMAT_OUT=$(ruff format "$FILE_PATH" 2>&1)
FORMAT_RC=$?
CHECK_OUT=$(ruff check --fix --quiet "$FILE_PATH" 2>&1)
CHECK_RC=$?

if [ "$FORMAT_RC" -ne 0 ] || [ "$CHECK_RC" -ne 0 ]; then
  {
    echo "ruff post-write advisory on ${FILE_PATH}:"
    [ -n "$FORMAT_OUT" ] && echo "$FORMAT_OUT"
    [ -n "$CHECK_OUT" ]  && echo "$CHECK_OUT"
  } >&2
fi

exit 0
