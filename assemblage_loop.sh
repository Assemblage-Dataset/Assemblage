#!/usr/bin/env bash
# Assemblage babysitter loop.
#
# Every RESTART_INTERVAL_S (default 6h): restart the running BUILDER/SCRAPER
# containers to shed leaked memory and pick up bind-mounted code changes. Once
# per calendar day: run the daily dataset pipeline WITHOUT blocking that cadence.
# Logs to var/loop.log.
#
# Deliberately does NOT restart rabbitmq, the coordinator, or the database:
#
#   - Restarting rabbitmq bounces all ~32 builder connections at once. The
#     coordinator starts a buildopt's dispatch thread only when a builder
#     REGISTERS (dispatch.py: ensure_started on builder_reg), and a builder
#     mid-DWARF-extraction won't re-register for up to ~45 min. So a broker
#     bounce empties the queues and idles the fleet for as long as the slowest
#     in-flight extraction — measured repeatedly during the 2026-07-16/17 soak.
#
#   - The coordinator is a supervised-thread service (crash -> restart-with-
#     backoff), so it needs no periodic bounce. Worse, restarting it ALONE would
#     strand every dispatcher: builders that stay connected never re-register,
#     so the dispatch threads never restart. (The real fix is to start
#     dispatchers from the DB at coordinator boot; until then, don't bounce it.)
#
#   - The daily pipeline's DWARF re-extraction can run for HOURS (7.5 h observed
#     on 2026-07-17). Running it inline blocks every restart for that whole time,
#     so it runs in the background under a lock instead.
#
#   nohup ./assemblage_loop.sh >/dev/null 2>&1 &   # or run inside tmux
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"
export PATH="$HOME/.local/bin:$PATH"

LOG_DIR="$REPO_ROOT/var"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/loop.log"
COMPOSE="docker compose -f docker-compose.yml"
RESTART_INTERVAL_S="${RESTART_INTERVAL_S:-21600}"   # 6h; PSI stayed 0.00 all soak
INFRA_CHECK_S="${INFRA_CHECK_S:-60}"                # watchdog poll
DAILY_LOCK="$LOG_DIR/daily.lock"
DAY_FILE="$LOG_DIR/last_pipeline_day"

# Services that must always be up. The watchdog only ever STARTS these when they
# are missing -- it never restarts a healthy one (bouncing the broker/coordinator
# is exactly what this script exists to avoid; see the header).
INFRA_SERVICES="database rabbitmq coordinator minio"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG"; }

# Restart only the RUNNING *rust* worker containers.
#
# Rust-only by deliberate policy: this deployment crawls and builds Rust only,
# and the C/C++ services (builder_0..9, scraper_0) are kept scaled to 0. Matching
# a bare '^(builder|scraper)' would make this loop *resurrect* any C++ builder
# that got started by accident -- e.g. a bare `docker compose up -d`, which brings
# up every service in the file -- and keep it alive forever. Anchoring on the rust
# names means the loop can never do that. To run C++ here again, widen this
# pattern deliberately.
WORKER_PATTERN="${WORKER_PATTERN:-^(builder_rust|scraper_rust)}"

restart_workers() {
    local workers
    workers=$($COMPOSE ps --services --filter status=running 2>/dev/null \
        | grep -E "$WORKER_PATTERN" | sort -u | tr '\n' ' ') || true
    if [ -z "${workers// /}" ]; then
        log "no running worker containers to restart"
        return
    fi
    log "restarting workers: $workers"
    # shellcheck disable=SC2086
    $COMPOSE restart $workers >>"$LOG" 2>&1 || log "worker restart failed (continuing)"
}

# Run the daily pipeline in the background under a non-blocking lock, so a slow
# run never stalls the restart cadence and two runs never overlap. The day is
# recorded (in DAY_FILE) only on success, so a failure retries next iteration.
run_daily_async() {
    local day="$1"
    (
        exec 9>"$DAILY_LOCK"
        if ! flock -n 9; then
            log "daily pipeline already running; not starting another"
            exit 0
        fi
        log "running daily dataset pipeline for $day (background)"
        if DB_HOST=localhost MINIO_ENDPOINT=localhost:9010 \
            uv run assemblage-daily >>"$LOG" 2>&1; then
            echo "$day" > "$DAY_FILE"
            log "daily pipeline done for $day"
        else
            log "daily pipeline failed for $day (will retry next cycle)"
        fi
    ) &
}

# Bring back any infra service that has stopped. On 2026-07-17 and 2026-07-18,
# both at 00:00:0X EDT, something cleanly stopped database+rabbitmq+coordinator
# (exit 0, SIGTERM, dependency order) and they stayed down -- db and rabbitmq
# carry no restart policy, and a `docker stop` defeats one anyway. The 5-second
# stop became a 17-hour outage purely because nothing noticed. This is the
# self-heal; var/docker_events_recorder.sh is what identifies the culprit.
ensure_infra_up() {
    local running missing=""
    # NOTE every `return` here is an explicit `return 0`. Under `set -e` a bare
    # `return` propagates the previous command's status, so the all-infra-up path
    # (the normal one) would return 1 and kill the whole loop.
    running="$($COMPOSE ps --services --filter status=running 2>/dev/null | sort -u)" || return 0
    for svc in $INFRA_SERVICES; do
        printf '%s\n' "$running" | grep -qx "$svc" || missing="$missing $svc"
    done
    [ -n "${missing// /}" ] || return 0
    log "INFRA DOWN:$missing — starting"
    # shellcheck disable=SC2086
    if $COMPOSE up -d --no-recreate $missing >>"$LOG" 2>&1; then
        log "infra recovered:$missing"
    else
        log "infra recovery FAILED:$missing (will retry in ${INFRA_CHECK_S}s)"
    fi
}

# Sleep in slices so infra is watched every INFRA_CHECK_S while workers are still
# only restarted once per RESTART_INTERVAL_S.
sleep_watching_infra() {
    local remain="$1" slice
    while [ "$remain" -gt 0 ]; do
        slice=$(( remain < INFRA_CHECK_S ? remain : INFRA_CHECK_S ))
        sleep "$slice"
        remain=$(( remain - slice ))
        ensure_infra_up
    done
}

log "loop starting (worker restart ${RESTART_INTERVAL_S}s, infra watchdog ${INFRA_CHECK_S}s)"

while true; do
    ensure_infra_up

    today="$(date '+%Y-%m-%d')"
    last="$(cat "$DAY_FILE" 2>/dev/null || echo '')"
    if [ "$today" != "$last" ]; then
        run_daily_async "$today"
    fi

    restart_workers

    log "sleeping ${RESTART_INTERVAL_S}s (infra watched every ${INFRA_CHECK_S}s)"
    sleep_watching_infra "$RESTART_INTERVAL_S"
done
