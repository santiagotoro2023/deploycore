#!/bin/bash
# Polls the settings table (global scope) for an update_requested flag and,
# when set, pulls the latest code, rebuilds, and restarts the app services.
# Runs with the Docker socket mounted so it can drive the host's dockerd
# directly, and with the repo bind-mounted at the fixed container path
# /repo.
#
# `docker compose` itself, though, needs to run from a path that is BOTH:
#   - readable locally (it reads .env and the Dockerfile build contexts
#     itself, client-side, before ever talking to the daemon), and
#   - identical to the repo's real path on the HOST (the bind-mount
#     sources it hands the daemon for api/worker/frontend's own volumes,
#     like ./backend:/app, are resolved by the daemon against ITS OWN
#     filesystem, which only knows the host's real paths).
# A fixed container path like /repo satisfies the first requirement but
# not the second. Rather than requiring the host path to be hand-
# configured (a PROJECT_DIR .env variable that's easy to forget, goes
# stale if the repo is ever moved, or simply doesn't exist yet on an
# instance that predates this feature), this container asks the Docker
# API for its own bind mount's source to learn that host path itself, then
# symlinks it (inside this container only) to the real /repo mount so both
# requirements are satisfied by the same literal path string, no manual
# configuration required at all.
# See README.md "Updating" for the trade-offs of this design.
set -uo pipefail

PSQL_URL=$(printf '%s' "$DATABASE_URL" | sed 's/+asyncpg//')
POLL_INTERVAL=5
CHECK_INTERVAL=300
CONTAINER_REPO_DIR=/repo

DISCOVERY_DEBUG=""

discover_host_repo_dir() {
  local container_id inspect_out inspect_err inspect_status resolved
  container_id=$(cat /etc/hostname 2>/dev/null || hostname)
  inspect_err=$(mktemp)
  inspect_out=$(docker inspect "$container_id" \
    --format '{{ range .Mounts }}{{ if eq .Destination "/repo" }}{{ .Source }}{{ end }}{{ end }}' \
    2>"$inspect_err")
  inspect_status=$?
  resolved="$inspect_out"
  if [ -z "$resolved" ]; then
    DISCOVERY_DEBUG="container_id='$container_id' docker_inspect_exit=$inspect_status docker_inspect_stderr='$(tr '\n' ' ' < "$inspect_err")'"
  fi
  rm -f "$inspect_err"
  printf '%s' "$resolved"
}

sql_escape() { printf '%s' "$1" | sed "s/'/''/g"; }

psql_exec() {
  psql "$PSQL_URL" -t -A -q -v ON_ERROR_STOP=1 -c "$1" 2>/dev/null
}

wait_for_settings_table() {
  # On a genuinely fresh install, postgres itself comes up (and its own
  # healthcheck passes) well before the api container's entrypoint finishes
  # running Alembic migrations, since that's a separate container with no
  # ordering guarantee relative to this one. Every upsert_setting call below
  # swallows psql's stderr, so writing to a table that doesn't exist yet
  # would fail silently and never be retried, permanently leaving
  # git_available/update_status unset, exactly the "works on an existing
  # instance, not on a brand new one" gap this closes.
  local i
  for i in $(seq 1 60); do
    if psql "$PSQL_URL" -t -A -q -c "SELECT to_regclass('public.settings')" 2>/dev/null | grep -q settings; then
      return 0
    fi
    sleep 2
  done
  echo "Timed out waiting for the settings table to exist (migrations never completed?)." >&2
  return 1
}

upsert_setting() {
  local key="$1" value_json="$2" escaped exists
  escaped=$(sql_escape "$value_json")
  exists=$(psql_exec "SELECT 1 FROM settings WHERE scope='global' AND key='$key' LIMIT 1")
  if [ "$exists" = "1" ]; then
    psql_exec "UPDATE settings SET value='$escaped'::jsonb, updated_at=now() WHERE scope='global' AND key='$key'"
  else
    psql_exec "INSERT INTO settings (id, scope, key, value, created_at, updated_at) VALUES (gen_random_uuid(), 'global', '$key', '$escaped'::jsonb, now(), now())"
  fi
}

get_setting_raw() {
  psql_exec "SELECT value::text FROM settings WHERE scope='global' AND key='$1'"
}

current_commit_short() {
  git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo ""
}

set_status() {
  local stage="$1" error="${2:-}" json
  json=$(jq -nc --arg stage "$stage" --arg error "$error" --arg commit "$(current_commit_short)" \
    '{stage: $stage, error: (if $error == "" then null else $error end), commit: (if $commit == "" then null else $commit end)}')
  upsert_setting update_status "$json"
}

idle_forever() {
  while true; do sleep 3600; done
}

if ! wait_for_settings_table; then
  # Can't safely idle_forever here the normal way (that relies on settings
  # writes succeeding elsewhere too), but there's nothing more productive
  # to do than keep the container alive so `docker compose logs updater`
  # is still inspectable instead of a crash-looping container.
  idle_forever
fi

if [ ! -d "$CONTAINER_REPO_DIR/.git" ]; then
  echo "Not a git checkout ($CONTAINER_REPO_DIR/.git missing), self-update disabled." >&2
  upsert_setting git_available 'false'
  upsert_setting update_status '{"stage": "disabled", "error": "not running from a git checkout", "commit": null}'
  idle_forever
fi

# PROJECT_DIR is an optional manual override, checked first: set it in
# .env only if the automatic discovery below can't work in your specific
# Docker setup (a socket proxy that blocks `docker inspect`, for example).
# Leave it unset otherwise, discovery just works without it.
if [ -n "${PROJECT_DIR:-}" ]; then
  HOST_REPO_DIR="$PROJECT_DIR"
else
  HOST_REPO_DIR="$(discover_host_repo_dir)"
fi

if [ -z "$HOST_REPO_DIR" ]; then
  echo "Could not determine this repo's path on the host, self-update disabled. Debug: $DISCOVERY_DEBUG" >&2
  upsert_setting git_available 'false'
  upsert_setting update_status "$(jq -nc --arg debug "$DISCOVERY_DEBUG" \
    '{stage: "disabled", error: ("could not resolve the repo'"'"'s host path via the Docker API (set PROJECT_DIR in .env to override). " + $debug), commit: null}')"
  idle_forever
fi

# Make the discovered host path resolve, inside this container only, to the
# same bind-mounted files /repo already points at. Everything below
# operates on $REPO_DIR (the host path) rather than $CONTAINER_REPO_DIR
# directly, so docker compose's own local file reads and the daemon's
# bind-mount resolution agree on one path string.
REPO_DIR="$HOST_REPO_DIR"
if [ ! -e "$REPO_DIR" ]; then
  mkdir -p "$(dirname "$REPO_DIR")"
  ln -s "$CONTAINER_REPO_DIR" "$REPO_DIR"
fi

# The repo is bind-mounted from the host, usually owned by the host user,
# while this container runs as root: git refuses to operate on a repo it
# doesn't own unless told it's safe to. Needed for both the real mount and
# the symlinked path above, since git resolves symlinks when checking this.
git config --global --add safe.directory "$CONTAINER_REPO_DIR"
git config --global --add safe.directory "$REPO_DIR"

upsert_setting git_available 'true'
echo "Using repo at (host path, symlinked to /repo inside this container): $REPO_DIR"

COMPOSE_ARGS=(--project-directory "$REPO_DIR" -f "$REPO_DIR/docker-compose.yml")

refresh_commit_status() {
  local branch remote_ref latest behind now_iso
  branch=$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
  [ -z "$branch" ] && return
  git -C "$REPO_DIR" fetch origin "$branch" --quiet 2>/dev/null || return
  remote_ref="origin/$branch"
  latest=$(git -C "$REPO_DIR" rev-parse --short "$remote_ref" 2>/dev/null || echo "")
  behind=$(git -C "$REPO_DIR" rev-list --count "HEAD..$remote_ref" 2>/dev/null || echo "0")
  now_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  upsert_setting current_commit "\"$(current_commit_short)\""
  [ -n "$latest" ] && upsert_setting latest_commit "\"$latest\""
  upsert_setting commits_behind "$behind"
  upsert_setting checked_at "\"$now_iso\""
}

run_update() {
  set_status pulling
  local branch
  branch=$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)

  if ! git -C "$REPO_DIR" fetch origin "$branch" > /tmp/update.log 2>&1; then
    set_status failed "git fetch failed: $(tail -c 500 /tmp/update.log)"
    return
  fi
  if ! git -C "$REPO_DIR" pull --ff-only origin "$branch" > /tmp/update.log 2>&1; then
    set_status failed "git pull failed (not a fast-forward, or another conflict): $(tail -c 500 /tmp/update.log)"
    return
  fi

  set_status building
  if ! docker compose "${COMPOSE_ARGS[@]}" build api worker frontend > /tmp/update.log 2>&1; then
    set_status failed "build failed: $(tail -c 500 /tmp/update.log)"
    return
  fi

  set_status restarting
  if ! docker compose "${COMPOSE_ARGS[@]}" up -d --no-deps api worker frontend > /tmp/update.log 2>&1; then
    set_status failed "restart failed: $(tail -c 500 /tmp/update.log)"
    return
  fi

  set_status finalizing
  local i ok=0
  for i in $(seq 1 60); do
    if curl -sf http://api:8000/api/health > /dev/null 2>&1; then
      ok=1
      break
    fi
    sleep 2
  done
  if [ "$ok" != "1" ]; then
    set_status failed "restarted, but api did not become healthy within 2 minutes"
    return
  fi

  refresh_commit_status
  set_status done
}

refresh_commit_status
last_check=$(date +%s)

echo "Updater ready, polling for update_requested every ${POLL_INTERVAL}s"
while true; do
  requested=$(get_setting_raw update_requested)
  if [ "$requested" = "true" ]; then
    run_update
    upsert_setting update_requested 'false'
  fi

  check_requested=$(get_setting_raw check_requested)
  if [ "$check_requested" = "true" ]; then
    refresh_commit_status
    upsert_setting check_requested 'false'
    last_check=$(date +%s)
  fi

  now_ts=$(date +%s)
  if [ $((now_ts - last_check)) -ge $CHECK_INTERVAL ]; then
    refresh_commit_status
    last_check=$now_ts
  fi

  sleep "$POLL_INTERVAL"
done
