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

# Remote Management's coturn (STUN/TURN) secret - generated the same way so
# the feature works out of the box with no manual setup. Unlike the old
# RustDesk-based version, there's no separate admin account to configure
# after the fact: coturn reads this straight from the environment at
# container start. See README "Remote Management".
fill_if_blank TURN_PASSWORD > /dev/null

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

ensure_line_exists TURN_HOST

current_public_url=$(grep '^APP_PUBLIC_URL=' .env | head -1 | cut -d= -f2-)
current_turn_host=$(grep '^TURN_HOST=' .env | head -1 | cut -d= -f2-)
if [ -z "$current_public_url" ] || [ "$current_public_url" = "http://localhost:8000" ] \
   || [ -z "$current_turn_host" ] || [ "$current_turn_host" = "localhost" ]; then
  HOST_IP=$(detect_host_ip)
  if [ -n "$HOST_IP" ]; then
    if [ -z "$current_public_url" ] || [ "$current_public_url" = "http://localhost:8000" ]; then
      echo "Detected this host's IP as ${HOST_IP}, setting APP_PUBLIC_URL=http://${HOST_IP}:8000"
      sed -i.bak "s|^APP_PUBLIC_URL=.*|APP_PUBLIC_URL=http://${HOST_IP}:8000|" .env
      rm -f .env.bak
    fi
    # Only matters for a host that isn't on this server's own LAN - Shadow's
    # WebRTC path always tries a direct connection first. Same detected IP
    # as APP_PUBLIC_URL - only wrong in the same multi-NIC/routing cases
    # that one is, and flagged the same way at the end.
    if [ -z "$current_turn_host" ] || [ "$current_turn_host" = "localhost" ]; then
      echo "Setting TURN_HOST=${HOST_IP}"
      sed -i.bak "s|^TURN_HOST=.*|TURN_HOST=${HOST_IP}|" .env
      rm -f .env.bak
    fi
  else
    echo "Could not auto-detect this host's LAN IP; APP_PUBLIC_URL/TURN_HOST stay at localhost, which guest VMs and remote agents generally can't reach." >&2
    echo "Set APP_PUBLIC_URL and TURN_HOST in .env to this host's real address yourself, then re-run this script." >&2
  fi
fi

echo "Building and starting the stack..."
docker compose up -d --build

echo
echo "Done. Open https://localhost to run the setup wizard."
echo "Your browser will warn about the certificate at first, it's self-signed by default; Settings -> HTTPS certificate lets you upload a real one."
echo "APP_PUBLIC_URL in .env is set to what this host's IP looked like just now; if your VMs reach this host on a different network/IP than the one detected, or you have multiple NICs, double-check it before deploying real VMs."

# --- Remote Management from the internet ---
# Everything that CAN be automated already is (secrets, LAN address). Only a
# host that ISN'T on this server's own LAN needs anything below at all -
# Shadow's WebRTC path always tries a direct connection first, and Connect's
# RDP traffic is tunneled through the agent's own outbound connection rather
# than dialed into directly. Reaching such a host from *outside* this network
# needs two things this script can't do for you - forward ports on your
# router/firewall, and pick a public address - so it detects your public IP
# and prints exactly what to do. Best-effort: skipped silently if there's no
# outbound internet.
PUBLIC_IP=$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || curl -fsS --max-time 5 https://ifconfig.me 2>/dev/null || true)
current_turn_host=$(grep '^TURN_HOST=' .env | head -1 | cut -d= -f2-)
if [ -n "$PUBLIC_IP" ] && [ "$PUBLIC_IP" != "$current_turn_host" ]; then
  echo
  echo "----------------------------------------------------------------------"
  echo "Remote Management works on your local network right now, no setup"
  echo "needed. To reach a machine that ISN'T on this network:"
  echo
  echo "  1. Forward these ports on your router/firewall to this host:"
  echo "       3478 TCP+UDP   and   49160-49200 UDP"
  echo "  2. Point TURN_HOST in .env at your public address"
  echo "     (detected public IP: ${PUBLIC_IP}), e.g.:"
  echo "       TURN_HOST=${PUBLIC_IP}"
  echo "     (a domain name pointed at this host works too, and is tidier)"
  echo "  3. Re-run:  docker compose up -d"
  echo
  echo "  Full step-by-step (router, cloud firewalls, DNS): see the Wiki ->"
  echo "  Remote Management -> \"Network & firewall setup\"."
  echo "----------------------------------------------------------------------"
fi
