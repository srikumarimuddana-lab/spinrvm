#!/usr/bin/env bash
# ============================================================
# setup-eas-secrets.sh — Register EAS Secrets for Spinr builds
# ============================================================
#
# EAS Build injects secrets as environment variables at build time.
# This script creates the secrets that rider-app and driver-app need
# but which should NOT live in eas.json or any committed file.
#
# Prerequisites:
#   1. npm install -g eas-cli   (or npx eas-cli)
#   2. eas login                (authenticate with your Expo account)
#   3. Both rider-app/ and driver-app/ must be linked to their
#      respective EAS projects (eas.json + app.config.ts projectId).
#
# Usage:
#   ./scripts/setup-eas-secrets.sh
#
# After running, every EAS Build (eas build ...) will automatically
# have EXPO_PUBLIC_GOOGLE_MAPS_API_KEY available as an env var —
# no eas.json `env` block needed.
#
# ⚠️  ROTATE THE KEY BELOW before making the repo public or shipping
# to production. This is the same key that was previously committed
# in eas.json and test-places.js. It works for internal testing but
# should be replaced with a restricted key scoped to your Android
# package name + iOS bundle ID in the Google Cloud Console.
# ============================================================

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────
# Replace this value after rotation. For now it's the existing
# dev/test key so EAS builds keep working without interruption.
GOOGLE_MAPS_API_KEY="${EXPO_PUBLIC_GOOGLE_MAPS_API_KEY:-AIzaSyC5i7lhtfXDoyYOB3KdyJtZ-CtKDzM5m9M}"

# Production backend URL (set via env var; falls back to the Fly.io domain)
BACKEND_URL="${EXPO_PUBLIC_BACKEND_URL:-https://spinr-api.fly.dev}"

# Sentry auth token for source-map uploads during EAS builds.
# Generate at https://sentry.io → Settings → Auth Tokens (project:releases scope).
SENTRY_AUTH_TOKEN_VAL="${SENTRY_AUTH_TOKEN:-}"

# ── Helper ─────────────────────────────────────────────────────
create_secret() {
    local project_dir="$1"
    local name="$2"
    local value="$3"

    if [[ -z "${value}" ]]; then
        echo "  ⚠ Skipping ${name} for ${project_dir} (value is empty)"
        return 0
    fi

    echo "→ Setting secret ${name} for ${project_dir}..."
    (
        cd "${project_dir}"
        # --force overwrites if the secret already exists (safe for rotation).
        eas secret:create \
            --scope project \
            --name "${name}" \
            --value "${value}" \
            --force 2>/dev/null \
        || eas secret:create \
            --scope project \
            --name "${name}" \
            --value "${value}"
    )
    echo "  ✓ ${name} set for ${project_dir}"
}

# ── Main ───────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Spinr EAS Secrets Setup ==="
echo ""
echo "This will create/update the following EAS Secrets:"
echo "  • EXPO_PUBLIC_GOOGLE_MAPS_API_KEY  (rider-app + driver-app)"
echo "  • EXPO_PUBLIC_BACKEND_URL          (rider-app + driver-app)"
if [[ -n "${SENTRY_AUTH_TOKEN_VAL}" ]]; then
echo "  • SENTRY_AUTH_TOKEN                (rider-app + driver-app)"
fi
echo ""
echo "⚠️  The Google Maps key used here is the existing dev/test key."
echo "   Rotate it in Google Cloud Console before going to production."
echo "   Run: EXPO_PUBLIC_GOOGLE_MAPS_API_KEY='AIzaSy...' ./scripts/setup-eas-secrets.sh"
echo ""

for APP_DIR in "${REPO_ROOT}/rider-app" "${REPO_ROOT}/driver-app"; do
    create_secret "${APP_DIR}" "EXPO_PUBLIC_GOOGLE_MAPS_API_KEY" "${GOOGLE_MAPS_API_KEY}"
    create_secret "${APP_DIR}" "EXPO_PUBLIC_BACKEND_URL"          "${BACKEND_URL}"
    create_secret "${APP_DIR}" "SENTRY_AUTH_TOKEN"                "${SENTRY_AUTH_TOKEN_VAL}"
done

echo ""
echo "=== Done ==="
echo ""
echo "Next steps:"
echo "  1. Run 'eas build --profile test' in rider-app/ or driver-app/"
echo "     to verify the secrets are injected."
echo "  2. Before production:"
echo "     a) Rotate EXPO_PUBLIC_GOOGLE_MAPS_API_KEY in Google Cloud Console"
echo "        (restrict to your bundle IDs + package names)."
echo "     b) Set EXPO_PUBLIC_BACKEND_URL to https://api.<yourdomain> once DNS is live."
echo "     c) Add SENTRY_AUTH_TOKEN if Sentry source-map upload is needed."
echo "     d) Re-run this script with the new values."
echo "  3. Apple submission secrets (not EAS secrets — set as GitHub Actions secrets):"
echo "     APPLE_ID, ASC_APP_ID, ASC_DRIVER_APP_ID, APPLE_TEAM_ID"
echo "  4. Google Play submission: place play-service-account.json in each app dir."
echo "     (File is gitignored — do NOT commit it.)"
