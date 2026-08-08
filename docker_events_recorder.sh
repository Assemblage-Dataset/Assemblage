#!/bin/bash
# Identify WHAT stops assemblage's infra containers at ~00:00 local, every day.
#
# Established so far (2026-08-07), so don't re-derive it:
#   - database, rabbitmq and coordinator are stopped *cleanly* within ~1.4s of
#     each other at 00:00:02 local, in dependency order (coordinator, then db,
#     then rabbitmq). Postgres logs "received fast shutdown request"; rabbitmq
#     stops its vhost gracefully. Nothing crashes, nothing OOMs.
#   - It is NOT assemblage_loop.sh: ensure_infra_up only ever runs
#     `compose up -d --no-recreate`, never a stop.
#   - It is NOT cliu57's crontab (only the @reboot line), /etc/cron.d,
#     /etc/cron.daily, logrotate (no docker config) or dpkg-db-backup.
#   - coordinator carries restart:unless-stopped and still stayed down, which
#     only happens on an explicit `docker stop` -- so this is deliberate, from
#     something outside this project. The host is shared (another workload runs
#     nixos/nix containers).
#
# `docker events` gives WHAT and WHEN but never WHO -- the daemon does not record
# the caller. So on each stop/die of a watched container this snapshots the
# process table immediately, while the caller is still alive: `docker stop` blocks
# until the container is down (0.4-3.4s here), which is the window we exploit.
# `ps -ef` shows other users' command lines, so a `docker stop ...` or
# `docker compose stop ...` from any account should appear in the snapshot.
#
# Run detached, it survives logout but NOT a reboot:
#   nohup setsid /bin/bash ./docker_events_recorder.sh >/dev/null 2>&1 &
# Deliberately at the repo root, not in var/: var/ has been wiped at least once,
# which is what destroyed the previous copy of this recorder before it ever fired.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"
mkdir -p var
LOG="$REPO_ROOT/var/docker_events.log"

# The three that actually get stopped. minio/builders/scraper have never been
# touched by this, so keeping them out of the filter keeps the log readable.
WATCH_RE='assemblage-db|assemblage-rabbitmq-1|assemblage-coordinator-1'

log() { echo "$(date '+%Y-%m-%d %H:%M:%S.%3N') $*" >>"$LOG"; }

snapshot() {
    local why="$1"
    {
        echo "================ SNAPSHOT: $why"
        echo "--- date: $(date '+%F %T.%3N %Z') ---"
        echo "--- processes mentioning docker/compose ---"
        ps -ef 2>/dev/null | grep -iE 'docker|compose|podman' | grep -v grep
        echo "--- clients on the docker socket (own procs only unless root) ---"
        ss -xp 2>/dev/null | grep -i docker | head -20
        echo "--- recently started processes (top 15 by start time) ---"
        ps -eo pid,ppid,user,lstart,cmd --sort=-start_time 2>/dev/null | head -16
        echo "================ END SNAPSHOT"
    } >>"$LOG" 2>&1
}

log "recorder starting (watching: $WATCH_RE)"
trap 'log "recorder exiting"; exit 0' TERM INT

# --detach-keys is irrelevant here; the stream ends only if dockerd restarts, so
# wrap in a loop to survive that.
while true; do
    docker events --filter 'event=stop' --filter 'event=die' --filter 'event=kill' \
                  --format '{{.Time}} {{.Action}} {{.Actor.Attributes.name}}' 2>>"$LOG" |
    while read -r ts action name; do
        case "$name" in
            *assemblage-db*|*assemblage-rabbitmq*|*assemblage-coordinator*)
                log "EVENT $action $name (docker ts=$ts)"
                snapshot "$action $name"
                ;;
        esac
    done
    log "event stream ended (dockerd restart?); reconnecting in 5s"
    sleep 5
done
