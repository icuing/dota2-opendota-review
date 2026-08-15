#!/bin/sh
set -eu

CRONTAB_FILE=${DOTA2_CRONTAB_FILE:-/etc/crontabs/root}
BEGIN_MARKER="# BEGIN DOTA2_DAILY_REVIEW"
END_MARKER="# END DOTA2_DAILY_REVIEW"

if [ ! -f "$CRONTAB_FILE" ]; then
    echo "未找到 root 的 cron 配置；无需删除。"
    exit 0
fi

TMP_FILE="${CRONTAB_FILE}.dota2.$$"
trap 'rm -f "$TMP_FILE"' EXIT INT TERM

awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
    $0 == begin { skip = 1; next }
    $0 == end { skip = 0; next }
    !skip { print }
' "$CRONTAB_FILE" >"$TMP_FILE"
cat "$TMP_FILE" >"$CRONTAB_FILE"

if [ -z "${DOTA2_SKIP_CRON_RESTART:-}" ]; then
    /etc/init.d/cron restart
fi

echo "已删除 Dota 2 每日复盘 cron 任务；历史复盘文件仍保留。"
