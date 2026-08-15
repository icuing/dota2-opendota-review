#!/bin/sh
set -eu

TIME_VALUE=${1:-06:15}
RETENTION_DAYS=${2:-30}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
RUNNER="$SCRIPT_DIR/run_daily_review.sh"
CRONTAB_FILE=${DOTA2_CRONTAB_FILE:-/etc/crontabs/root}
BEGIN_MARKER="# BEGIN DOTA2_DAILY_REVIEW"
END_MARKER="# END DOTA2_DAILY_REVIEW"

case "$TIME_VALUE" in
    [0-2][0-9]:[0-5][0-9]) ;;
    *)
        echo "时间格式错误，请使用 HH:MM，例如 00:15。" >&2
        exit 2
        ;;
esac

HOUR=${TIME_VALUE%:*}
MINUTE=${TIME_VALUE#*:}
if [ "$HOUR" -gt 23 ]; then
    echo "小时应为 00 到 23。" >&2
    exit 2
fi

case "$RETENTION_DAYS" in
    *[!0-9]*|'')
        echo "保留天数应为 1 到 3650 的整数。" >&2
        exit 2
        ;;
esac
if [ "$RETENTION_DAYS" -lt 1 ] || [ "$RETENTION_DAYS" -gt 3650 ]; then
    echo "保留天数应为 1 到 3650。" >&2
    exit 2
fi

if [ ! -x /usr/bin/python3 ] && [ -z "${DOTA2_ALLOW_MISSING_PYTHON:-}" ]; then
    echo "没有找到 /usr/bin/python3。请先运行：" >&2
    echo "opkg update && opkg install python3 python3-urllib python3-openssl ca-bundle" >&2
    exit 3
fi

mkdir -p "$(dirname -- "$CRONTAB_FILE")"
touch "$CRONTAB_FILE"
TMP_FILE="${CRONTAB_FILE}.dota2.$$"
trap 'rm -f "$TMP_FILE"' EXIT INT TERM

awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
    $0 == begin { skip = 1; next }
    $0 == end { skip = 0; next }
    !skip { print }
' "$CRONTAB_FILE" >"$TMP_FILE"

{
    cat "$TMP_FILE"
    echo "$BEGIN_MARKER"
    echo "$MINUTE $HOUR * * * DOTA2_RETENTION_DAYS=$RETENTION_DAYS DOTA2_PARSE_TIMEOUT_MINUTES=60 /bin/sh \"$RUNNER\""
    echo "$END_MARKER"
} >"$CRONTAB_FILE"

chmod +x "$RUNNER" "$SCRIPT_DIR/install_openwrt_cron.sh" "$SCRIPT_DIR/uninstall_openwrt_cron.sh"

if [ -z "${DOTA2_SKIP_CRON_RESTART:-}" ]; then
    /etc/init.d/cron restart
fi

echo "安装完成：每天 $TIME_VALUE 自动检查前一天的比赛；解析最长等待 60 分钟。"
echo "Telegram 成功接收后会立即删除当天全部本地复盘文件。"
echo "运行日志：$SCRIPT_DIR/daily_logs/openwrt-latest.log"
