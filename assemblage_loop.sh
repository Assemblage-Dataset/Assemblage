#!/usr/bin/env bash
# Assemblage babysitter loop.
#
# Every 3 hours: restart the worker containers (coordinator, scraper, builders)
# to shed leaked memory / stuck tasks. Once per calendar day: run the daily
# dataset pipeline (host-side, MinIO on 9010). Logs to var/loop.log.
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

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG"; }

last_pipeline_day=""

while true; do
    log "restarting worker containers"
    $COMPOSE restart coordinator scraper_0 \
        builder_0 builder_1 builder_2 builder_3 builder_4 \
        builder_5 builder_6 builder_7 builder_8 builder_9 rabbitmq >>"$LOG" 2>&1 || \
        log "restart failed (continuing)"

    today="$(date '+%Y-%m-%d')"
    if [ "$today" != "$last_pipeline_day" ]; then
        log "running daily dataset pipeline for $today"
        if DB_HOST=localhost MINIO_ENDPOINT=localhost:9010 \
            uv run assemblage-daily >>"$LOG" 2>&1; then
            last_pipeline_day="$today"
            log "daily pipeline done"
        else
            log "daily pipeline failed (will retry next cycle)"
        fi
    fi

    log "sleeping 3h"
    sleep 10800
done
