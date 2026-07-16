#!/bin/bash
# Terminates HTTPS for the whole app and redirects plain HTTP to it. By
# default that's Caddy's own `tls internal`, a zero-config self-signed
# certificate backed by a local CA it generates once and reuses (persisted
# under /data, a named volume, so restarts don't churn out a new one).
# THIS PART MUST NEVER DEPEND ON POSTGRES BEING UP: it's what a user sees
# on the very first boot of a fresh install, before migrations have
# necessarily even finished, and it's also the only thing standing between
# them and a working HTTPS port at all, so it starts immediately no matter
# what Postgres is doing.
#
# Which certificate to use beyond that default is controlled from Settings
# -> HTTPS certificate, which writes a `tls_mode` row straight to Postgres
# the same way every other setting does (see
# backend/app/api/routes/settings.py). This container has no other way to
# learn about that change, so, same idea as updater/update.sh polling for
# update_requested, it polls the settings table on an interval in the
# background and reloads Caddy's config when what it finds no longer
# matches what's currently running. If Postgres or psql is ever
# unreachable, that polling just quietly finds nothing to do, self-signed
# (or whatever was last loaded) keeps serving either way.
set -u

CERT_DIR=/data/tls
CADDYFILE=/etc/caddy/Caddyfile
POLL_INTERVAL=5
PSQL_URL=$(printf '%s' "${DATABASE_URL:-}" | sed 's/+asyncpg//')

mkdir -p "$CERT_DIR"

# Best-effort: empty output (no psql binary, DATABASE_URL unset, Postgres
# unreachable, settings table not migrated yet, whatever) just means
# render_caddyfile below falls back to self-signed, never a hard failure.
current_mode() {
  command -v psql >/dev/null 2>&1 || return 0
  [ -n "$PSQL_URL" ] || return 0
  psql "$PSQL_URL" -t -A -q -c \
    "SELECT value #>> '{}' FROM settings WHERE scope='global' AND key='tls_mode'" 2>/dev/null | tr -d '"'
}

render_caddyfile() {
  local cert_block
  if [ "$(current_mode)" = "uploaded" ] && [ -f "$CERT_DIR/uploaded-cert.pem" ] && [ -f "$CERT_DIR/uploaded-key.pem" ]; then
    cert_block="tls $CERT_DIR/uploaded-cert.pem $CERT_DIR/uploaded-key.pem"
  else
    # Plain `tls internal` on a hostless address only ever issues ONE
    # static certificate, covering localhost/127.0.0.1, decided once at
    # startup. Anything else, a LAN IP, a port-forwarded public IP, a
    # hostname, gets no certificate at all and a fatal TLS alert
    # (SSL_ERROR_INTERNAL_ERROR_ALERT in Firefox). `on_demand` is Caddy's
    # documented fix for exactly this: it issues a locally-trusted
    # certificate per incoming SNI, on the fly, so this works no matter
    # what address/hostname is used to reach it, not just localhost.
    cert_block=$(printf 'tls internal {\n\t\ton_demand\n\t}')
  fi
  cat > "$CADDYFILE" <<EOF
:80 {
	redir https://{host}{uri} permanent
}

:443 {
	$cert_block
	reverse_proxy frontend:5173
}

# The embedded RustDesk web client (rustdesk-api's own webclient2, port
# 21114) gets its OWN HTTPS listener, sharing the same certificate strategy,
# rather than being loaded directly as its own http://<host>:21114 URL or
# proxied under a sub-path of :443. Two real bugs found the hard way, in
# order: (1) a plain HTTP iframe inside this HTTPS-served app is exactly the
# "mixed active content" browsers block by default - confirmed live as the
# cause of a black screen with no visible error, the RustDesk protocol's own
# WebSocket connections never got a chance to open at all. (2) Proxying it
# under a /rustdesk-webclient/ sub-path of :443 (the first fix attempted)
# just traded that for an endless loading spinner - webclient2's own JS
# makes absolute, root-relative calls (e.g. /api/...) that only resolve
# correctly when the app is actually served from its origin's root, not a
# sub-path a browser has no way to know to prepend to those requests.
# A dedicated port with NO path rewriting sidesteps both: see
# services/remote_desktop.py's public_url_for(), which builds this port's
# own URL using the Settings-configured Remote Management host (the same
# address already used for the relay/rendezvous ports).
:8444 {
	$cert_block
	reverse_proxy rustdesk:21114
}
EOF
}

# What would trigger a reload: the mode itself, plus the cert/key files'
# mtimes so re-uploading a replacement while already in "uploaded" mode
# (a renewal, say) is picked up too, not just a mode flip.
reload_signature() {
  local mtimes
  mtimes=$(stat -c '%Y' "$CERT_DIR/uploaded-cert.pem" "$CERT_DIR/uploaded-key.pem" 2>/dev/null | tr '\n' '-')
  printf '%s' "$(current_mode):$mtimes"
}

render_caddyfile
caddy run --config "$CADDYFILE" --adapter caddyfile &
CADDY_PID=$!
trap 'kill -TERM "$CADDY_PID" 2>/dev/null' TERM INT

last_signature=$(reload_signature)
while kill -0 "$CADDY_PID" 2>/dev/null; do
  sleep "$POLL_INTERVAL"
  signature=$(reload_signature)
  if [ "$signature" != "$last_signature" ]; then
    render_caddyfile
    caddy reload --config "$CADDYFILE" --adapter caddyfile || echo "Caddy reload failed" >&2
    last_signature="$signature"
  fi
done
wait "$CADDY_PID"
