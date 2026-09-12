#!/bin/bash
# check-openrouter-credits.sh
# -----------------------------
# Checks the real OpenRouter account balance (not the per-key spending cap --
# see the 2026-09-07 incident where those two were conflated and masked a
# near-empty account) and logs every reading for a running history. Sends a
# Telegram alert via the same gateway bot in two cases, each independent:
#   1. balance drops below ALERT_THRESHOLD (low-balance warning)
#   2. cumulative spend has advanced by another SPEND_MILESTONE since the
#      last such alert (a running "you've spent $5 more" notifier, added
#      2026-09-07 per explicit request -- tracks a rolling baseline in
#      MILESTONE_STATE_FILE, seeded to current usage on first run so it
#      doesn't immediately fire for all historical spend-to-date)
# Both conditions are quiet otherwise, matching this project's established
# "flag when it matters" pattern (Dataview staleness dashboard, ingest
# digest's triage framing) rather than a notification on every run.
#
# Reads existing secrets in place rather than duplicating them anywhere:
# the OpenRouter key from goose's own secrets.yaml, the bot token from its
# existing secrets file. No new secret storage introduced.
#
# Scheduled via DSM Task Scheduler (see 8.task) -- not a persistent daemon,
# matches the existing goose-telegram-compact.py pattern (a script Task
# Scheduler invokes on a cron-style schedule, not another standing process).

set -uo pipefail

ALERT_THRESHOLD=2.00
SPEND_MILESTONE=5.00
HISTORY_LOG=/volume1/docker/secrets/openrouter-credit-history.log
MILESTONE_STATE_FILE=/volume1/docker/secrets/openrouter-milestone-baseline.txt
TELEGRAM_CHAT_ID=157757120

OPENROUTER_KEY=$(sudo grep OPENROUTER_API_KEY /var/services/homes/claude-agent/.config/goose/secrets.yaml | sed 's/OPENROUTER_API_KEY: //' | tr -d '"')
BOT_TOKEN=$(sudo cat /volume1/docker/secrets/goose-telegram-bot-token.txt)

RESPONSE=$(curl -s https://openrouter.ai/api/v1/credits -H "Authorization: Bearer $OPENROUTER_KEY")
TOTAL_CREDITS=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['total_credits'])" 2>/dev/null)
TOTAL_USAGE=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['total_usage'])" 2>/dev/null)

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

if [ -z "$TOTAL_CREDITS" ] || [ -z "$TOTAL_USAGE" ]; then
  echo "$TIMESTAMP,ERROR,could not parse response: $RESPONSE" >> "$HISTORY_LOG"
  exit 1
fi

REMAINING=$(python3 -c "print(round($TOTAL_CREDITS - $TOTAL_USAGE, 4))")
echo "$TIMESTAMP,$TOTAL_CREDITS,$TOTAL_USAGE,$REMAINING" >> "$HISTORY_LOG"

BELOW_THRESHOLD=$(python3 -c "print(1 if $REMAINING < $ALERT_THRESHOLD else 0)")
if [ "$BELOW_THRESHOLD" = "1" ]; then
  curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d chat_id="$TELEGRAM_CHAT_ID" \
    -d text="⚠️ OpenRouter balance low: \$${REMAINING} remaining (threshold: \$${ALERT_THRESHOLD}). Top up at openrouter.ai to keep this bot working." \
    > /dev/null
fi

# Seed the baseline to current usage on first run -- otherwise the ~$9+
# already spent before this feature existed would immediately trigger a
# catch-up alert, which isn't what "let me know every time you spend $5
# more" means.
if [ ! -f "$MILESTONE_STATE_FILE" ]; then
  echo "$TOTAL_USAGE" | sudo tee "$MILESTONE_STATE_FILE" > /dev/null
fi
LAST_MILESTONE_USAGE=$(sudo cat "$MILESTONE_STATE_FILE")

SPENT_SINCE_LAST=$(python3 -c "print(round($TOTAL_USAGE - $LAST_MILESTONE_USAGE, 4))")
MILESTONE_CROSSED=$(python3 -c "print(1 if $SPENT_SINCE_LAST >= $SPEND_MILESTONE else 0)")
if [ "$MILESTONE_CROSSED" = "1" ]; then
  curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d chat_id="$TELEGRAM_CHAT_ID" \
    -d text="💸 OpenRouter spend update: \$${SPENT_SINCE_LAST} spent since the last milestone alert (lifetime usage now \$${TOTAL_USAGE}, \$${REMAINING} remaining)." \
    > /dev/null
  echo "$TOTAL_USAGE" | sudo tee "$MILESTONE_STATE_FILE" > /dev/null
fi
