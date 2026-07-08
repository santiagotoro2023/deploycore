#!/bin/bash
# Polls the settings table (global scope) for an update_requested flag and,
# when set, pulls the latest code, rebuilds, and restarts the app services.
# Runs with the Docker socket mounted so it can drive the host's dockerd
# directly, and with the repo bind-mounted at PROJECT_DIR (same absolute
# path inside this container as on the host, set in .env) so both git
# operations and the nested `docker compose` calls resolve paths correctly.
# See README.md "Updating" for the trade-offs of this design.
set -uo pipefail

PSQL_URL=$(printf '%s' "$DATABASE_URL" | sed 's/+asyncpg//')
POLL_INTERVAL=5
CHECK_INTERVAL=300
REPO_DIR="${PROJECT_DIR:-}"

sql_escape() { printf '%s' "$1" | sed "s/'/''/g"; }

psql_exec() {
  psql "$PSQL_URL" -t -A -q -v ON_ERROR_STOP=1 -c "$1" 2>/dev/null
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

if [ -z "$REPO_DIR" ] || [ ! -d "$REPO_DIR" ]; then
  echo "PROJECT_DIR is not set correctly (got: '$REPO_DIR'), self-update disabled." >&2
  upsert_setting git_available 'false'
  upsert_setting update_status '{"stage": "disabled", "error": "PROJECT_DIR is not set in .env", "commit": null}'
  idle_forever
fi

# The repo is bind-mounted from the host, usually owned by the host user,
# while this container runs as root: git refuses to operate on a repo it
# doesn't own unless told it's safe to.
git config --global --add safe.directory "$REPO_DIR"

if [ ! -d "$REPO_DIR/.git" ]; then
  echo "Not a git checkout ($REPO_DIR/.git missing), self-update disabled."
  upsert_setting git_available 'false'
  set_status disabled
  idle_forever
fi

upsert_setting git_available 'true'
echo "Using repo at: $REPO_DIR"

# PROJECT_DIR is the same absolute path inside this container as on the
# host (both docker-compose.yml's bind mount and this container's env var
# are set from the same PROJECT_DIR value), so both the compose file read
# and the volume/env_file paths it resolves line up correctly without any
# extra --project-directory juggling.
COMPOSE_ARGS=(-f "$REPO_DIR/docker-compose.yml")

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

  now_ts=$(date +%s)
  if [ $((now_ts - last_check)) -ge $CHECK_INTERVAL ]; then
    refresh_commit_status
    last_check=$now_ts
  fi

  sleep "$POLL_INTERVAL"
done
