#!/bin/sh
# One-command install: copies .env if missing, fills in APP_SECRET_KEY if
# blank, then builds and starts the stack. Migrations run automatically on
# api container startup (see backend/entrypoint.sh), so nothing else is
# needed after this to reach a working setup wizard at http://localhost:5173.
set -e

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "Creating .env from .env.example"
  cp .env.example .env
fi

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

echo "Building and starting the stack..."
docker compose up -d --build

echo
echo "Done. Open http://localhost:5173 to run the setup wizard."
echo "Edit APP_PUBLIC_URL in .env before deploying real VMs if this host isn't reachable at localhost from guest machines."
