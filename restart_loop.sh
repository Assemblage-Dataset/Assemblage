#!/bin/bash
# Monitor disk space and restart docker compose hourly.
# - Every 1 min: check free disk. If < 1TB, stop everything.
# - Every 1 hour: docker compose down + up for clean restart.
# Run in tmux: tmux new -s assemblage-loop './restart_loop.sh'

COMPOSE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$COMPOSE_DIR/restart_loop.log"
MIN_FREE_KB=$((1024 * 1024 * 1024))  # 1 TB in KB
CHECKS_PER_HOUR=60  # check every 1 min, restart after 60 checks

cd "$COMPOSE_DIR"
echo "$(date) Starting loop (disk check every 1m, restart every 1h)" | tee -a "$LOG"

# Start containers on first run
echo "$(date) Starting containers..." | tee -a "$LOG"
docker compose up -d >> "$LOG" 2>&1

checks=0
while true; do
    sleep 60
    checks=$((checks + 1))

    # Check free disk space
    free_kb=$(df --output=avail / | tail -1 | tr -d ' ')
    free_tb=$(echo "scale=2; $free_kb / 1024 / 1024 / 1024" | bc)

    if [ "$free_kb" -lt "$MIN_FREE_KB" ]; then
        echo "$(date) FREE DISK ${free_tb}TB < 1TB - stopping all containers!" | tee -a "$LOG"
        docker compose down -t 10 >> "$LOG" 2>&1
        echo "$(date) Containers stopped due to low disk. Exiting." | tee -a "$LOG"
        exit 1
    fi

    # Hourly restart
    if [ "$checks" -ge "$CHECKS_PER_HOUR" ]; then
        echo "$(date) Hourly restart (free: ${free_tb}TB)..." | tee -a "$LOG"
        docker compose down -t 10 >> "$LOG" 2>&1
        docker compose up -d >> "$LOG" 2>&1
        echo "$(date) Containers restarted." | tee -a "$LOG"
        checks=0
    fi
done
