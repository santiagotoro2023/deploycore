#!/bin/sh
# One-command install: copies .env if missing, fills in APP_SECRET_KEY and
# PROJECT_DIR if blank, then builds and starts the stack. Migrations run
# automatically on api container startup (see backend/entrypoint.sh), so
# nothing else is needed after this to reach a working setup wizard at
# http://localhost:5173. Safe to re-run: only fills in blank/missing values,
# never touches ones you've already set.
set -e

cd "$(dirname "$0")/.."
REPO_DIR="$(pwd)"

if [ ! -f .env ]; then
  echo "Creating .env from .env.example"
  cp .env.example .env
fi

# Appends KEY= if the line is entirely missing (older .env files predating
# a given variable), a no-op if it's already present (blank or not).
ensure_line_exists() {
  key="$1"
  if ! grep -q "^${key}=" .env; then
    printf '%s=\n' "$key" >> .env
  fi
}

ensure_line_exists APP_SECRET_KEY
ensure_line_exists PROJECT_DIR

if grep -q '^APP_SECRET_KEY=$' .env; then
  echo "Generating APP_SECRET_KEY"
  # A Fernet key is just base64-urlsafe(32 random bytes), no need for the
  # cryptography package itself to generate one, so this works with any
  # stock python3 (falls back to openssl if python3 isn't present).
  SECRET=$(python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())" 2>/dev/null || openssl rand -base64 32 2>/dev/null | tr '+/' '-_')
  if [ -z "$SECRET" ]; then
    echo "Could not generate APP_SECRET_KEY automatically (need python3 or openssl)." >&2
    echo "Set APP_SECRET_KEY in .env yourself, then re-run this script." >&2
    exit 1
  fi
  sed -i.bak "s|^APP_SECRET_KEY=\$|APP_SECRET_KEY=${SECRET}|" .env
  rm -f .env.bak
fi

if grep -q '^PROJECT_DIR=$' .env; then
  echo "Setting PROJECT_DIR to $REPO_DIR (needed for the self-update feature)"
  sed -i.bak "s|^PROJECT_DIR=\$|PROJECT_DIR=${REPO_DIR}|" .env
  rm -f .env.bak
fi

echo "Building and starting the stack..."
docker compose up -d --build

echo
echo "Done. Open http://localhost:5173 to run the setup wizard."
echo "Edit APP_PUBLIC_URL in .env before deploying real VMs if this host isn't reachable at localhost from guest machines."
