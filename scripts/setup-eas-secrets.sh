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
GOOGLE_MAPS_API_KEY="AIzaSyC5i7lhtfXDoyYOB3KdyJtZ-CtKDzM5m9M"

# ── Helper ─────────────────────────────────────────────────────
create_secret() {
    local project_dir="$1"
    local name="$2"
    local value="$3"

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
echo ""
echo "⚠️  The key used here is the existing dev/test key."
echo "   Rotate it in Google Cloud Console before going to production."
echo ""

# Rider app
create_secret "${REPO_ROOT}/rider-app" "EXPO_PUBLIC_GOOGLE_MAPS_API_KEY" "${GOOGLE_MAPS_API_KEY}"

# Driver app
create_secret "${REPO_ROOT}/driver-app" "EXPO_PUBLIC_GOOGLE_MAPS_API_KEY" "${GOOGLE_MAPS_API_KEY}"

echo ""
echo "=== Done ==="
echo ""
echo "Next steps:"
echo "  1. Run 'eas build --profile test' in rider-app/ or driver-app/"
echo "     to verify the key is injected."
echo "  2. Before production: rotate the key in Google Cloud Console,"
echo "     update this script, and re-run."
echo "  3. Consider adding more secrets here as needed:"
echo "     • EXPO_PUBLIC_STRIPE_PUBLISHABLE_KEY"
echo "     • EXPO_PUBLIC_FIREBASE_API_KEY (if using Expo web build)"
echo "     • SENTRY_AUTH_TOKEN (for source map uploads)"
