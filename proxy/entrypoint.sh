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

	# Lets a browser trust EVERY certificate this instance ever issues with
	# ONE install, instead of clicking through a per-site warning separately
	# for each origin - relevant if a real certificate isn't in use (self-
	# signed mode only; not served at all once one is uploaded). The
	# official Caddy Docker image sets XDG_DATA_HOME=/data (this container's
	# caddy_data volume), so `tls internal`'s own local CA root always lands
	# at exactly this path.
	handle /ca.crt {
		root * /data/caddy/pki/authorities/local
		rewrite * /root.crt
		file_server
		header Content-Disposition "attachment; filename=deploycore-ca.crt"
	}

	# The embedded RustDesk web client (rustdesk-api's own "webclient2",
	# lejianwen/rustdesk-api's Flutter-web build - confirmed via its actual
	# source, http/router/router.go's `g.StaticFS("/webclient2", ...)`) is
	# proxied at the EXACT SAME PATH it's built to expect, on THIS SAME
	# origin - not a separate port, not a different sub-path. Two real bugs
	# found getting here, in order: (1) loading it directly as its own
	# http://<host>:21114 URL is a plain HTTP iframe inside this HTTPS-served
	# app, exactly the "mixed active content" browsers block by default -
	# confirmed live as a black screen with no visible error. (2) A dedicated
	# :8444 HTTPS port fixed that but introduced its own problem: any
	# separate origin needs its own certificate trust decision, which a
	# browser flatly refuses to let you make from INSIDE an embedded iframe
	# at all (the same restriction that stops a malicious page tricking
	# someone into trusting a bad cert) - so Connect/Shadow silently failed
	# for anyone who hadn't separately visited/trusted that port first, with
	# no way to do so from inside the session itself. (3) An EARLIER same-
	# origin attempt, proxied under a DIFFERENT sub-path (/rustdesk-webclient/)
	# with the prefix stripped before forwarding, also failed - not because
	# same-origin is impossible, but because that path didn't match what the
	# Flutter build's own base-href was compiled for (/webclient2/), so its
	# root-relative asset/API references resolved incorrectly. Proxying the
	# IDENTICAL path it already expects avoids that entirely - confirmed via
	# its own source this needs no rewriting.
	handle /webclient2/* {
		reverse_proxy rustdesk:21114
	}

	# The one specific API call webclient2 needs for anonymous, share-token-
	# based sessions (redeeming the token DeployCore mints server-side, see
	# services/remote_desktop.py's create_session_url()) - confirmed via
	# lejianwen/rustdesk-api's actual source (http/router/api.go's
	# WebClientRoutes(): `frg.POST("/shared-peer", w.SharedPeer)`, registered
	# OUTSIDE any auth-gated group, unlike its sibling /server-config routes
	# which need a login this anonymous flow never has). Confirmed this
	# doesn't collide with anything DeployCore's own API uses (grepped this
	# app's own route definitions directly) - DeployCore's own auth lives at
	# /api/auth/*, not a bare /api/login, so there's no ambiguity for Caddy
	# to resolve between the two apps' /api/* namespaces at all past this one
	# specific path.
	handle /api/shared-peer {
		reverse_proxy rustdesk:21114
	}

	reverse_proxy frontend:5173
}

# webclient2's own JS dials these two directly as wss://<host>:21118 and
# :21119 (the ID/relay rendezvous connections, offset +2/+3 from
# RUSTDESK_RELAY_HOST's 21116/21117 - confirmed via connection.ts's
# getrUriFromRs()), never through a path under :443, so they need their own
# TLS-terminating listeners here rather than a handle block above. Same cert
# as :443 - one trust decision covers both, since it's the same host.
:21118 {
	$cert_block
	reverse_proxy rustdesk:21118
}

:21119 {
	$cert_block
	reverse_proxy rustdesk:21119
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
