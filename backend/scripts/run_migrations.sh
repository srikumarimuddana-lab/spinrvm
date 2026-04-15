#!/usr/bin/env bash
# =============================================================================
# Spinr — Database Migration Runner
# =============================================================================
# Applies all pending SQL migrations in canonical order using a tracking table
# to ensure idempotency (safe to run multiple times — already-applied files
# are skipped).
#
# Usage:
#   PG_CONNECTION_STRING="postgres://..." ./scripts/run_migrations.sh
#   ./scripts/run_migrations.sh --dry-run      # show pending files, no changes
#
# Requirements: psql on PATH, PG_CONNECTION_STRING env var set
#
# Migration order (matches README canonical order):
#   1. supabase_schema.sql         — base DDL for all tables
#   2. sql/01-04_*.sql             — PostGIS, updated_at, features, ride overhaul
#   3. migrations/001-21_*.sql     — incremental feature additions
#   4. migrations/add_profile_image_url.sql — unnumbered trailing migration
#   5. supabase_rls.sql            — Row Level Security policies (applied last)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"

# ---- colour helpers ----------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RESET='\033[0m'
info()    { echo -e "${GREEN}[migrate]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[migrate]${RESET} $*"; }
error()   { echo -e "${RED}[migrate]${RESET} $*" >&2; }

# ---- arg parsing -------------------------------------------------------------
DRY_RUN=false
for arg in "$@"; do
  [[ "$arg" == "--dry-run" ]] && DRY_RUN=true
done

# ---- prerequisites -----------------------------------------------------------
if [[ -z "${PG_CONNECTION_STRING:-}" ]]; then
  error "PG_CONNECTION_STRING is not set. Export it before running this script."
  exit 1
fi

if ! command -v psql &>/dev/null; then
  error "psql not found on PATH. Install postgresql-client."
  exit 1
fi

# ---- ensure tracking table ---------------------------------------------------
info "Ensuring schema_migrations tracking table exists..."
psql "$PG_CONNECTION_STRING" <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
SQL

# ---- helpers -----------------------------------------------------------------
is_applied() {
  local fname="$1"
  local result
  result=$(psql "$PG_CONNECTION_STRING" -tAc \
    "SELECT COUNT(*) FROM schema_migrations WHERE filename = '$fname'")
  [[ "$result" -gt 0 ]]
}

apply_file() {
  local filepath="$1"
  local fname
  fname="$(basename "$filepath")"

  if is_applied "$fname"; then
    info "  SKIP  $fname (already applied)"
    return 0
  fi

  if $DRY_RUN; then
    warn "  PENDING  $fname"
    return 0
  fi

  info "  APPLY $fname ..."
  if psql "$PG_CONNECTION_STRING" -f "$filepath"; then
    psql "$PG_CONNECTION_STRING" -c \
      "INSERT INTO schema_migrations (filename) VALUES ('$fname') ON CONFLICT DO NOTHING"
    info "  OK    $fname"
  else
    error "  FAILED $fname — aborting migration run"
    exit 1
  fi
}

# ---- canonical migration order -----------------------------------------------
declare -a MIGRATION_FILES=(
  # 1. Base schema
  "$BACKEND_DIR/supabase_schema.sql"

  # 2. PostGIS + structural extensions (sql/ directory, applied in order)
  "$BACKEND_DIR/sql/01_postgis_schema.sql"
  "$BACKEND_DIR/sql/02_add_updated_at.sql"
  "$BACKEND_DIR/sql/03_features.sql"
  "$BACKEND_DIR/sql/04_rides_admin_overhaul.sql"

  # 3. Incremental feature migrations (migrations/ directory, numbered order)
  # NOTE: two files share the 10_ prefix — both are applied; 10_disputes first
  # (alphabetical), then 10_service_area. If ordering matters, rename one.
  "$BACKEND_DIR/migrations/001_add_driver_user_id.sql"
  "$BACKEND_DIR/migrations/02_dynamic_documents.sql"
  "$BACKEND_DIR/migrations/03_corporate_accounts_heatmap.sql"
  "$BACKEND_DIR/migrations/04_stripe_payments.sql"
  "$BACKEND_DIR/migrations/05_corporate_accounts.sql"
  "$BACKEND_DIR/migrations/06_cloud_messaging.sql"
  "$BACKEND_DIR/migrations/07_promotions_extra_columns.sql"
  "$BACKEND_DIR/migrations/08_service_area_subregions.sql"
  "$BACKEND_DIR/migrations/09_subscription_plans.sql"
  "$BACKEND_DIR/migrations/10_disputes_table.sql"
  "$BACKEND_DIR/migrations/10_service_area_driver_matching.sql"
  "$BACKEND_DIR/migrations/11_driver_needs_review.sql"
  "$BACKEND_DIR/migrations/12_driver_lifecycle_status.sql"
  "$BACKEND_DIR/migrations/13_driver_notes.sql"
  "$BACKEND_DIR/migrations/14_driver_activity_log.sql"
  "$BACKEND_DIR/migrations/15_ride_aggregate_columns.sql"
  "$BACKEND_DIR/migrations/16_driver_daily_stats.sql"
  "$BACKEND_DIR/migrations/17_corporate_accounts_fk.sql"
  "$BACKEND_DIR/migrations/18_admin_staff.sql"
  "$BACKEND_DIR/migrations/19_wallet.sql"
  "$BACKEND_DIR/migrations/20_quests.sql"
  "$BACKEND_DIR/migrations/21_loyalty.sql"
  "$BACKEND_DIR/migrations/add_profile_image_url.sql"

  # 4. Row Level Security — always last (depends on all tables existing)
  "$BACKEND_DIR/supabase_rls.sql"
)

# ---- run ---------------------------------------------------------------------
$DRY_RUN && warn "DRY RUN — no changes will be made"
info "Running migrations from $BACKEND_DIR"
info "Total files in sequence: ${#MIGRATION_FILES[@]}"
echo ""

APPLIED=0
SKIPPED=0
PENDING=0

for filepath in "${MIGRATION_FILES[@]}"; do
  if [[ ! -f "$filepath" ]]; then
    warn "  MISSING $(basename "$filepath") — file not found, skipping"
    continue
  fi

  fname="$(basename "$filepath")"
  if is_applied "$fname"; then
    SKIPPED=$((SKIPPED + 1))
  else
    PENDING=$((PENDING + 1))
  fi

  apply_file "$filepath"
  [[ $? -eq 0 && ! $(is_applied "$fname" 2>/dev/null) ]] || APPLIED=$((APPLIED + 1))
done

echo ""
if $DRY_RUN; then
  info "Dry run complete. $PENDING file(s) pending, $SKIPPED already applied."
else
  info "Migration run complete. $SKIPPED already applied, $PENDING newly applied."
fi
