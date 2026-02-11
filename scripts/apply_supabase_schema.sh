#!/usr/bin/env bash
set -euo pipefail

if [ -z "${PG_CONNECTION_STRING:-}" ]; then
  echo "Error: PG_CONNECTION_STRING is not set"
  exit 1
fi

psql "$PG_CONNECTION_STRING" -f "$(dirname "$0")/../backend/supabase_schema.sql"

echo "Schema applied successfully."