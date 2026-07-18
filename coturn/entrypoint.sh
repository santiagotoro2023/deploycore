#!/bin/sh
# TURN_HOST (Settings -> Remote Management, or .env at install time) can be
# an IP OR a domain name - the docs explicitly recommend a domain for a
# stable address. But coturn's own --external-ip flag, which is what makes
# relay candidates usable through a home router/NAT (the actual "reach this
# from anywhere" case TURN exists for), needs a literal IP: STUN/TURN relay
# candidate advertisement happens at the IP level, no DNS resolution
# involved. Without this, self-hosting behind a router with the ports
# correctly forwarded would still silently fail the TURN relay path - coturn
# would advertise its own container-internal Docker address instead of the
# real reachable one, and that's true for a same-LAN install too (a bridge
# network's container IP is never the host's own LAN-reachable address
# either) - not just the internet-facing case. Resolved once at container
# start, since TURN_HOST can change (Settings) and this container gets
# recreated when it does.
set -u

resolved=$(getent hosts "${TURN_HOST:-}" 2>/dev/null | awk '{print $1}' | head -1)

# A loopback resolution (the shipped default, TURN_HOST=localhost, before
# scripts/setup.sh or a real value has been set) would be actively wrong to
# advertise - no other machine can reach 127.0.0.1 on this host. Skip
# --external-ip entirely in that case; coturn's own default behavior is no
# worse than today. scripts/setup.sh always overwrites this with a real
# detected LAN IP on a normal install, so this case should be rare/transient.
case "$resolved" in
  127.*|"")
    echo "coturn: TURN_HOST='${TURN_HOST:-}' didn't resolve to a usable address - starting without --external-ip (only breaks the TURN relay path for a host reached through NAT; direct/LAN connections are unaffected)." >&2
    exec turnserver -n --listening-port=3478 --realm=deploycore \
      --user="${TURN_USERNAME}:${TURN_PASSWORD}" \
      --min-port=49160 --max-port=49200 --no-cli
    ;;
  *)
    echo "coturn: advertising external IP ${resolved} (resolved from TURN_HOST=${TURN_HOST})"
    exec turnserver -n --listening-port=3478 --realm=deploycore \
      --user="${TURN_USERNAME}:${TURN_PASSWORD}" \
      --min-port=49160 --max-port=49200 --no-cli \
      --external-ip="${resolved}"
    ;;
esac
