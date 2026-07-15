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

# Random base64-urlsafe(32 bytes) - stock python3, openssl fallback. Used for
# the Fernet APP_SECRET_KEY and any other "just needs to be random" secret.
gen_secret() {
  python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())" 2>/dev/null \
    || openssl rand -base64 32 2>/dev/null | tr '+/' '-_' | tr -d '='
}

# Fills in KEY= if it's present but blank. Echoes the generated value so the
# caller can reuse it (e.g. the admin password, needed again for the
# reset-admin-pwd step after the stack is up).
fill_if_blank() {
  key="$1"
  ensure_line_exists "$key"
  if grep -q "^${key}=\$" .env; then
    val=$(gen_secret)
    if [ -z "$val" ]; then
      echo "Could not generate ${key} automatically (need python3 or openssl)." >&2
      echo "Set ${key} in .env yourself, then re-run this script." >&2
      exit 1
    fi
    sed -i.bak "s|^${key}=\$|${key}=${val}|" .env
    rm -f .env.bak
    echo "Generated ${key}"
  fi
  grep "^${key}=" .env | head -1 | cut -d= -f2-
}

fill_if_blank APP_SECRET_KEY > /dev/null

# Remote Management (self-hosted RustDesk stack) secrets - generated the same
# way so the feature works out of the box with no manual setup. The admin
# password is captured here because it's needed again below, after the stack
# is up, to set it on the rustdesk-api server itself (which otherwise only
# prints its own random one to the container log). See README "Remote
# Management".
RUSTDESK_ADMIN_PW=$(fill_if_blank RUSTDESK_ADMIN_PASSWORD)
fill_if_blank RUSTDESK_JWT_KEY > /dev/null

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

ensure_line_exists RUSTDESK_RELAY_HOST
ensure_line_exists RUSTDESK_API_PUBLIC_URL

current_public_url=$(grep '^APP_PUBLIC_URL=' .env | head -1 | cut -d= -f2-)
current_relay_host=$(grep '^RUSTDESK_RELAY_HOST=' .env | head -1 | cut -d= -f2-)
if [ -z "$current_public_url" ] || [ "$current_public_url" = "http://localhost:8000" ] \
   || [ -z "$current_relay_host" ] || [ "$current_relay_host" = "localhost" ]; then
  HOST_IP=$(detect_host_ip)
  if [ -n "$HOST_IP" ]; then
    if [ -z "$current_public_url" ] || [ "$current_public_url" = "http://localhost:8000" ]; then
      echo "Detected this host's IP as ${HOST_IP}, setting APP_PUBLIC_URL=http://${HOST_IP}:8000"
      sed -i.bak "s|^APP_PUBLIC_URL=.*|APP_PUBLIC_URL=http://${HOST_IP}:8000|" .env
      rm -f .env.bak
    fi
    # Remote Management agents and the browser's embedded web client reach the
    # RustDesk relay/rendezvous servers and web client on this same host. Same
    # detected IP as APP_PUBLIC_URL - only wrong in the same multi-NIC/routing
    # cases that one is, and flagged the same way at the end.
    if [ -z "$current_relay_host" ] || [ "$current_relay_host" = "localhost" ]; then
      echo "Setting RUSTDESK_RELAY_HOST=${HOST_IP} and RUSTDESK_API_PUBLIC_URL=http://${HOST_IP}:21114"
      sed -i.bak "s|^RUSTDESK_RELAY_HOST=.*|RUSTDESK_RELAY_HOST=${HOST_IP}|" .env
      sed -i.bak "s|^RUSTDESK_API_PUBLIC_URL=.*|RUSTDESK_API_PUBLIC_URL=http://${HOST_IP}:21114|" .env
      rm -f .env.bak
    fi
  else
    echo "Could not auto-detect this host's LAN IP; APP_PUBLIC_URL/RUSTDESK_RELAY_HOST stay at localhost, which guest VMs and remote agents generally can't reach." >&2
    echo "Set APP_PUBLIC_URL and RUSTDESK_RELAY_HOST in .env to this host's real address yourself, then re-run this script." >&2
  fi
fi

echo "Building and starting the stack..."
docker compose up -d --build

# The rustdesk-api server creates its admin account with a RANDOM password on
# first run (only printed to its own container log), so DeployCore couldn't log
# in to it without help. This sets that password to the one in .env, which the
# DeployCore backend already reads - after this, Remote Management works with no
# manual step. Best-effort and idempotent (re-running just re-sets the same
# password): retried because the account only exists once the server has
# finished its own first-run init, which lags the container starting. Never
# fails the whole install over it - the Remote Management tab's setup banner
# also surfaces this if it didn't take.
if [ -n "$RUSTDESK_ADMIN_PW" ]; then
  echo "Configuring Remote Management admin account..."
  reset_ok=""
  i=0
  while [ "$i" -lt 30 ]; do
    if docker compose exec -T -w /app rustdesk ./apimain reset-admin-pwd "$RUSTDESK_ADMIN_PW" >/dev/null 2>&1; then
      reset_ok="yes"
      break
    fi
    i=$((i + 1))
    sleep 2
  done
  if [ -n "$reset_ok" ]; then
    echo "Remote Management admin account configured."
  else
    echo "Note: couldn't set the Remote Management admin password automatically yet." >&2
    echo "The Remote Management tab will guide you if it still needs it; or run:" >&2
    echo "  docker compose exec -w /app rustdesk ./apimain reset-admin-pwd \"\$(grep '^RUSTDESK_ADMIN_PASSWORD=' .env | cut -d= -f2-)\"" >&2
  fi
fi

echo
echo "Done. Open https://localhost to run the setup wizard."
echo "Your browser will warn about the certificate at first, it's self-signed by default; Settings -> HTTPS certificate lets you upload a real one."
echo "APP_PUBLIC_URL in .env is set to what this host's IP looked like just now; if your VMs reach this host on a different network/IP than the one detected, or you have multiple NICs, double-check it before deploying real VMs."

# --- Remote Management from the internet ---
# Everything that CAN be automated already is (secrets, admin account, LAN
# address). Reaching agents from *outside* this network needs two things this
# script can't do for you - forward ports on your router/firewall, and pick a
# public address - so it detects your public IP and prints exactly what to do.
# Best-effort: skipped silently if there's no outbound internet.
PUBLIC_IP=$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || curl -fsS --max-time 5 https://ifconfig.me 2>/dev/null || true)
current_relay_host=$(grep '^RUSTDESK_RELAY_HOST=' .env | head -1 | cut -d= -f2-)
if [ -n "$PUBLIC_IP" ] && [ "$PUBLIC_IP" != "$current_relay_host" ]; then
  echo
  echo "----------------------------------------------------------------------"
  echo "Remote Management works on your local network right now. To control"
  echo "machines from ANYWHERE (over the internet):"
  echo
  echo "  1. Forward these ports on your router/firewall to this host:"
  echo "       21114-21119 TCP  and  21116 UDP"
  echo "  2. Point RUSTDESK_RELAY_HOST and RUSTDESK_API_PUBLIC_URL in .env at"
  echo "     your public address (detected public IP: ${PUBLIC_IP}), e.g.:"
  echo "       RUSTDESK_RELAY_HOST=${PUBLIC_IP}"
  echo "       RUSTDESK_API_PUBLIC_URL=http://${PUBLIC_IP}:21114"
  echo "     (a domain name pointed at this host works too, and is tidier)"
  echo "  3. Re-run:  docker compose up -d"
  echo
  echo "  Full step-by-step (router, cloud firewalls, DNS): see the Wiki ->"
  echo "  Remote Management -> \"Network & firewall setup\"."
  echo "----------------------------------------------------------------------"
fi
