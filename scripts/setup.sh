#!/bin/sh
# One-command install: copies .env if missing, fills in APP_SECRET_KEY if
# blank and APP_PUBLIC_URL with this host's own detected LAN IP if it's
# still at its default, then builds and starts the stack. Migrations run
# automatically on api container startup (see backend/entrypoint.sh), so
# nothing else is needed after this to reach a working setup wizard at
# https://localhost (self-signed certificate until you upload a real one,
# see README "HTTPS certificate"). Safe to re-run: only fills in
# blank/still-default values, never touches ones you've already changed.
set -e

cd "$(dirname "$0")/.."

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

# Guest VMs call back to this address once Windows Setup finishes (see
# README "One setting worth checking"), so "localhost" is wrong for
# anything but a single-machine test: it's baked into commands that run
# inside the guest itself, where "localhost" means the guest, not this
# host. Best-effort detection of this host's own LAN-facing IP, in order:
# the source address the kernel would actually use to reach the internet
# (most likely to be the address other machines on the LAN can reach too,
# unlike just grabbing the first interface listed), then a portable
# Python fallback doing the same thing via a UDP socket (no packets
# actually sent, connect() on UDP just consults the routing table), then
# hostname -I as a last resort.
detect_host_ip() {
  ip_addr=$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)
  if [ -z "$ip_addr" ]; then
    ip_addr=$(python3 -c "import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('1.1.1.1', 80)); print(s.getsockname()[0]); s.close()" 2>/dev/null)
  fi
  if [ -z "$ip_addr" ]; then
    ip_addr=$(hostname -I 2>/dev/null | awk '{print $1}')
  fi
  printf '%s' "$ip_addr"
}

ensure_line_exists APP_PUBLIC_URL

current_public_url=$(grep '^APP_PUBLIC_URL=' .env | head -1 | cut -d= -f2-)
if [ -z "$current_public_url" ] || [ "$current_public_url" = "http://localhost:8000" ]; then
  HOST_IP=$(detect_host_ip)
  if [ -n "$HOST_IP" ]; then
    echo "Detected this host's IP as ${HOST_IP}, setting APP_PUBLIC_URL=http://${HOST_IP}:8000"
    sed -i.bak "s|^APP_PUBLIC_URL=.*|APP_PUBLIC_URL=http://${HOST_IP}:8000|" .env
    rm -f .env.bak
  else
    echo "Could not auto-detect this host's LAN IP; APP_PUBLIC_URL stays http://localhost:8000, which guest VMs generally can't reach." >&2
    echo "Set APP_PUBLIC_URL in .env to this host's real address yourself, then re-run this script." >&2
  fi
fi

echo "Building and starting the stack..."
docker compose up -d --build

echo
echo "Done. Open https://localhost to run the setup wizard."
echo "Your browser will warn about the certificate at first, it's self-signed by default; Settings -> HTTPS certificate lets you upload a real one."
echo "APP_PUBLIC_URL in .env is set to what this host's IP looked like just now; if your VMs reach this host on a different network/IP than the one detected, or you have multiple NICs, double-check it before deploying real VMs."
