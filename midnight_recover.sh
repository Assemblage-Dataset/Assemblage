#!/bin/bash
# Recover the fleet from the nightly external `docker compose stop`.
#
# Why this exists (verified 2026-08-08 by var/docker_events.log, don't re-derive):
# a cron job owned by another user on this host runs
#     /bin/sh -c cd /home/apduly/Assemblage && docker compose stop
# at 00:00:00. `docker compose stop` matches containers by the
# com.docker.compose.project LABEL, not by directory -- and both checkouts live in
# a directory named "Assemblage", so both resolve to project "assemblage" and that
# command stops OUR containers. It hits only database/rabbitmq/coordinator because
# that is the service set their older compose file defines.
#
# assemblage_loop.sh's watchdog restarts those three within ~60s, so by 00:10 infra
# is already back. What is NOT back is dispatch: the coordinator starts a
# buildopt's dispatch thread only when a builder REGISTERS, and the builders never
# restarted, so they keep stale registrations and are never dispatched to again.
# Queues drain to zero and the fleet idles while every container reports healthy.
# That coupling turned a 5-second stop into 17h and 13.7h outages on consecutive
# nights. Restarting the builders forces re-registration and recovers in <20s.
#
# Deliberately CONDITIONAL. An unconditional nightly restart would discard the
# in-flight build on all 8 builders every night (builders ack before building, so
# those tasks are lost, not requeued) even on nights when nothing went wrong.
#
# Install (keep the existing @reboot loop entry):
#     10 0 * * * cd /path/to/Assemblage && /bin/bash ./midnight_recover.sh >/dev/null 2>&1
#
# Manual use:
#     ./midnight_recover.sh --dry-run    # report only
#     ./midnight_recover.sh --force      # restart regardless of the strand check

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"
export PATH="$HOME/.local/bin:$PATH"
mkdir -p var
LOG="$REPO_ROOT/var/midnight_recover.log"
COMPOSE="docker compose -f docker-compose.yml"
INFRA="database rabbitmq minio coordinator"

DRY=0; FORCE=0
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=1 ;;
        --force)   FORCE=1 ;;
        *) echo "unknown arg: $a" >&2; exit 2 ;;
    esac
done

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG"; }

# --- 1. infra ---------------------------------------------------------------
# Normally a no-op: the loop's watchdog has already done this. Kept because this
# script must also work when the loop is not running (it dies on reboot).
running="$($COMPOSE ps --services --filter status=running 2>/dev/null | sort -u)"
missing=""
for svc in $INFRA; do
    printf '%s\n' "$running" | grep -qx "$svc" || missing="$missing $svc"
done
if [ -n "${missing// /}" ]; then
    log "infra down:$missing — starting"
    [ "$DRY" -eq 1 ] || $COMPOSE up -d --no-recreate $missing >>"$LOG" 2>&1
    # give rabbitmq time to accept connections before judging the queues
    for _ in $(seq 1 30); do
        [ "$(docker inspect -f '{{.State.Health.Status}}' assemblage-rabbitmq-1 2>/dev/null)" = healthy ] && break
        sleep 2
    done
fi

# --- 2. is the fleet stranded? ----------------------------------------------
# Signature: every buildopt queue that HAS a consumer has ZERO messages. Under
# healthy dispatch these sit around a dozen deep, so 0 across the board means the
# coordinator has no dispatcher feeding them. Counting only queues with consumers
# ignores the parked buildopts, whose stale backlogs are expected and unrelated.
read -r consumers msgs <<<"$(
    docker exec assemblage-rabbitmq-1 rabbitmqctl list_queues name messages consumers --quiet 2>/dev/null \
      | awk '/^build_opt_/ && $3>0 {c+=$3; m+=$2} END {print (c+0), (m+0)}'
)"
consumers=${consumers:-0}; msgs=${msgs:-0}

if [ "$consumers" -eq 0 ]; then
    log "no builder consumers at all (fleet down?) — nothing to re-register; leaving alone"
    exit 0
fi

if [ "$msgs" -gt 0 ] && [ "$FORCE" -eq 0 ]; then
    log "healthy: $consumers consumers, $msgs queued — no restart needed"
    exit 0
fi

[ "$FORCE" -eq 1 ] && log "--force given" || log "STRANDED: $consumers consumers, 0 queued — restarting builders"

# --- 3. restart the workers (never the coordinator: that re-strands the fleet) --
workers="$($COMPOSE ps --services --filter status=running 2>/dev/null \
            | grep -E '^(builder_rust|builder_[0-9]|scraper_rust)' | sort -u | tr '\n' ' ')"
if [ -z "${workers// /}" ]; then
    log "no running workers matched; nothing to restart"
    exit 0
fi
log "restarting:$(echo " $workers" | sed 's/ $//')"
if [ "$DRY" -eq 1 ]; then
    log "--dry-run: no restart performed"
    exit 0
fi
# shellcheck disable=SC2086
$COMPOSE restart $workers >>"$LOG" 2>&1

# --- 4. verify dispatch actually resumed ------------------------------------
for _ in $(seq 1 36); do
    sleep 5
    read -r c2 m2 <<<"$(
        docker exec assemblage-rabbitmq-1 rabbitmqctl list_queues name messages consumers --quiet 2>/dev/null \
          | awk '/^build_opt_/ && $3>0 {c+=$3; m+=$2} END {print (c+0), (m+0)}'
    )"
    if [ "${m2:-0}" -gt 0 ]; then
        log "recovered: ${c2:-0} consumers, ${m2:-0} queued"
        exit 0
    fi
done
log "WARNING: restarted but queues still empty after 180s — investigate"
exit 1
