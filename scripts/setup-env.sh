#!/usr/bin/env bash
# setup-env.sh — Bootstrap all .env files for local development.
#
# Usage: bash scripts/setup-env.sh
#
# What it does:
#   1. Copies each *.env.example to .env (or .env.local for admin-dashboard)
#      only when the target file does NOT already exist (safe to re-run).
#   2. Generates a secure random JWT_SECRET and splices it into backend/.env.
#   3. Prints a checklist of values you still need to fill in manually.
#
# Prerequisites: bash, openssl (for secret generation).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
log_warn() { echo -e "${YELLOW}  !${NC} $*"; }
log_err()  { echo -e "${RED}  ✗${NC} $*"; }

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Spinr — Local Dev Environment Bootstrap"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

copy_env() {
    local src="$1"
    local dst="$2"
    local label="$3"

    if [ -f "$dst" ]; then
        log_warn "$label: $dst already exists — skipping (delete it to regenerate)"
    elif [ -f "$src" ]; then
        cp "$src" "$dst"
        log_ok "$label: copied $src → $dst"
    else
        log_err "$label: source $src not found — skipping"
    fi
}

# ── 1. Copy .env.example files ──────────────────────────────────────────────
echo "Step 1: Copying .env.example files"
copy_env "$ROOT/backend/.env.example"           "$ROOT/backend/.env"                    "Backend"
copy_env "$ROOT/driver-app/.env.example"        "$ROOT/driver-app/.env"                 "Driver App"
copy_env "$ROOT/rider-app/.env.example"         "$ROOT/rider-app/.env"                  "Rider App"
copy_env "$ROOT/admin-dashboard/.env.example"   "$ROOT/admin-dashboard/.env.local"      "Admin Dashboard"
echo ""

# ── 2. Generate a secure JWT_SECRET ─────────────────────────────────────────
echo "Step 2: Generating JWT_SECRET for backend"
BACKEND_ENV="$ROOT/backend/.env"
if [ -f "$BACKEND_ENV" ]; then
    if grep -q "replace-with-strong-random-secret" "$BACKEND_ENV" 2>/dev/null; then
        SECRET=$(openssl rand -base64 48 | tr -d '\n')
        # Use | as sed delimiter to avoid issues with / in base64
        sed -i "s|replace-with-strong-random-secret|${SECRET}|" "$BACKEND_ENV"
        log_ok "Generated and spliced JWT_SECRET into backend/.env"
    else
        log_warn "JWT_SECRET in backend/.env appears to already be customized — skipping"
    fi
else
    log_warn "backend/.env not found — skipping JWT_SECRET generation"
fi
echo ""

# ── 3. Print checklist of values that need manual input ─────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Manual Setup Checklist"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  These values require human action and cannot be auto-generated:"
echo ""
echo "  backend/.env"
echo "    [ ] SUPABASE_URL               — Supabase project URL"
echo "    [ ] SUPABASE_SERVICE_ROLE_KEY  — Supabase service-role key"
echo "    [ ] FIREBASE_SERVICE_ACCOUNT_JSON — Firebase Admin SDK JSON (single line)"
echo "    [ ] ADMIN_EMAIL / ADMIN_PASSWORD  — First admin login credentials"
echo "    [ ] SENTRY_DSN (optional)      — Sentry error monitoring DSN"
echo ""
echo "  driver-app/.env  AND  rider-app/.env"
echo "    [ ] EXPO_PUBLIC_BACKEND_URL        — Your backend URL (dev: http://localhost:8000)"
echo "    [ ] EXPO_PUBLIC_GOOGLE_MAPS_API_KEY — Google Maps / Places / Directions API key"
echo "    [ ] EXPO_PUBLIC_FIREBASE_*         — Firebase config for the mobile apps"
echo ""
echo "  admin-dashboard/.env.local"
echo "    [ ] NEXT_PUBLIC_API_URL        — Backend URL (dev: http://localhost:8000)"
echo ""
echo "  Supabase (run once after provisioning a project):"
echo "    [ ] Run:  bash scripts/apply_supabase_schema.sh"
echo "    [ ] Populate Stripe/Twilio keys in the 'settings' table via the admin dashboard"
echo ""
echo "  Firebase:"
echo "    [ ] Verify google-services.json (Android) in driver-app/ and rider-app/"
echo "    [ ] Verify GoogleService-Info.plist (iOS) in driver-app/ and rider-app/"
echo ""
echo "  See READINESS_REPORT.md for full deployment instructions."
echo ""
