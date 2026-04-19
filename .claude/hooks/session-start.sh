#!/bin/bash
set -euo pipefail

# Only run in Claude Code remote (web) sessions
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

VENV_DIR="/opt/spinr-venv"

echo "==> Setting up Python virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi

echo "==> Installing backend Python dependencies..."
"$VENV_DIR/bin/pip" install -r backend/requirements.txt --quiet
"$VENV_DIR/bin/pip" install ruff pytest-cov pytest-asyncio pytest-env --quiet

# Persist venv activation for the session
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export VIRTUAL_ENV=\"$VENV_DIR\"" >> "$CLAUDE_ENV_FILE"
  echo "export PATH=\"$VENV_DIR/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi

echo "==> Installing frontend dependencies (Expo/React Native)..."
(cd frontend && yarn install --frozen-lockfile --silent)

echo "==> Installing driver-app dependencies..."
(cd driver-app && yarn install --silent)

echo "==> Installing rider-app dependencies..."
(cd rider-app && yarn install --silent)

echo "==> Installing admin-dashboard dependencies (Next.js)..."
(cd admin-dashboard && npm install --silent)

echo "==> Installing shared package dependencies..."
(cd shared && yarn install --silent)

echo "==> Session start complete."
