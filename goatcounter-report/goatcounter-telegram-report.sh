#!/bin/bash
# goatcounter-telegram-report.sh
# Weekly GoatCounter -> Telegram report. Run by DSM Task Scheduler task 13
# ("GoatCounter Telegram Report", Mondays 08:00 NAS time, runs as root).
# Every run is appended to the log below, so a run can be confirmed without checking Telegram.
# The bot token is masked in the log in case an error ever echoes it.
H=/var/services/homes/claude-agent
LOG="$H/goatcounter_telegram.log"
{
  echo "=== $(date '+%F %T %Z') goatcounter telegram report ==="
  cd "$H/goatcounter-report" || { echo "ERROR: $H/goatcounter-report missing"; exit 1; }
  [ -r "$H/.goatcounter_telegram.env" ] || { echo "ERROR: $H/.goatcounter_telegram.env missing"; exit 1; }
  set -a; . "$H/.goatcounter_telegram.env"; set +a
  /usr/bin/python3 goatcounter_telegram_report.py 2>&1 | sed -E 's/[0-9]{8,}:[A-Za-z0-9_-]{30,}/<bot-token>/g'
  rc=${PIPESTATUS[0]}
  echo "exit=$rc"
  echo
} >> "$LOG" 2>&1
chown claude-agent:users "$LOG" 2>/dev/null
[ "$(tail -n 2 "$LOG" | head -n 1)" = "exit=0" ]
