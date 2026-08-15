#!/bin/sh
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LOG_DIR="$SCRIPT_DIR/daily_logs"
LOG_FILE="$LOG_DIR/openwrt-latest.log"
PYTHON_BIN=${PYTHON_BIN:-/usr/bin/python3}
RETENTION_DAYS=${DOTA2_RETENTION_DAYS:-30}
PARSE_TIMEOUT_MINUTES=${DOTA2_PARSE_TIMEOUT_MINUTES:-60}

mkdir -p "$LOG_DIR"
: >"$LOG_FILE"

{
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始检查昨天的 Dota 2 比赛"
    "$PYTHON_BIN" "$SCRIPT_DIR/dota2_review.py" \
        --daily --day-offset 1 --no-open-project \
        --retention-days "$RETENTION_DAYS" --parse-timeout "$PARSE_TIMEOUT_MINUTES"
    STATUS=$?
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 运行结束，退出码：$STATUS"
    exit "$STATUS"
} >>"$LOG_FILE" 2>&1
