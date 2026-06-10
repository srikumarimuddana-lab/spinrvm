#!/usr/bin/env bash
# PreToolUse hook — surfaces the migration checklist whenever Claude is about to
# write or edit a file under backend/migrations/.
# Reads a JSON payload on stdin (Claude Code hook contract). Exits 0 with a stderr
# message so the user sees the warning but the tool still runs. Never blocks.

set -euo pipefail

INPUT=$(cat || true)
[ -z "$INPUT" ] && exit 0

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null || true)
[ -z "$FILE_PATH" ] && exit 0

case "$FILE_PATH" in
  */backend/migrations/*.sql|backend/migrations/*.sql)
    cat >&2 <<'EOF'
⚠️  Migration file about to be written/edited.
   Checklist (from CLAUDE.md + .claude/context/regulatory-sk.md):
     • Filename is next free NN_ — never reuse or edit a merged migration
     • RLS policies shipped in the SAME file for any new user-data table
     • Reversibility comment at top of file
     • Forward-compatible against in-flight prod traffic
     • Indexes added for any new WHERE / ORDER BY pattern
     • Money functions are SECURITY DEFINER + explicit search_path
     • No ON DELETE CASCADE on retention-sensitive tables (trips, incidents, periods)
   Run /migration-check before committing.
EOF
    ;;
esac

exit 0
