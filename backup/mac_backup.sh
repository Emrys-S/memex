#!/bin/bash
# mac_backup.sh
# -------------
# Backs up the Mac-side half of the second-brain project's Tier 1/Tier 2 data
# to Google Drive via restic + rclone. Shares the SAME restic repo as the
# NAS's nas_backup.sh (rclone:gdrive-backup:restic-repo) -- distinguished by
# --host mac-emrys vs --host schoerro on every restic call, so retention on
# one machine never touches the other's snapshots. See "## Backup strategy"
# in CLAUDE.md for the full design.
#
# Tier 2 (weekly, gated by a timestamp file): this nas-ops repo (CLAUDE.md,
#   the LinkedIn capture skill, state files) -- the Mac-side equivalent of
#   the NAS's pipeline-configs tier.
#
# Tier 1 vault backup moved to nas_backup.sh as of 2026-08-12, once the
# vault genuinely became NAS-canonical (Stage 3 of the vault migration --
# NAS-canonical + Syncthing, Mac/phone are thin synced replicas now, not
# the source of truth). Backing up a replica here too would just be
# redundant work against the same underlying content nas_backup.sh already
# covers at the actual source. The `forget --tag tier1` call below is kept
# so this host's pre-2026-08-12 vault snapshot history still ages out
# naturally via the NAS's scheduled prune, rather than being stranded.
#
# Deliberately does NOT run `restic prune` or `restic check` -- the NAS's
# scheduled run already does full repo maintenance daily against this same
# shared repo. Running prune from both machines would double the rewrite
# work and Google Drive API load for no benefit; `forget` alone (scoped to
# this host's own tags) is enough to mark this machine's old snapshots for
# the NAS's next prune to actually clean up.

set -euo pipefail

# launchd jobs get a minimal PATH that doesn't include Homebrew's
# /usr/local/bin -- the exact same class of bug that broke rclone-mount.sh
# on the NAS (documented in CLAUDE.md). Export it explicitly up front.
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

BACKUP_DIR="$HOME/Documents/nas-ops/backup"
REPO_DIR="$HOME/Documents/nas-ops"
RCLONE_CONFIG_FILE="$HOME/.config/rclone/rclone.conf"
ENV_FILE="$BACKUP_DIR/.env"
LAST_TIER2_FILE="$BACKUP_DIR/.last_tier2"
REPO="rclone:gdrive-backup:restic-repo"
HOST="mac-emrys"
LOG="$BACKUP_DIR/logs/backup-$(date +%Y%m%d-%H%M%S).log"
TIER2_INTERVAL_SECONDS=604800  # 7 days

mkdir -p "$BACKUP_DIR/logs"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" | tee -a "$LOG"; }

if [ ! -f "$ENV_FILE" ]; then
    log "✗ $ENV_FILE not found -- RESTIC_PASSWORD not set, aborting."
    exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"
if [ -z "${RESTIC_PASSWORD:-}" ]; then
    log "✗ RESTIC_PASSWORD is empty in $ENV_FILE -- aborting."
    exit 1
fi
export RESTIC_PASSWORD
if [ -f "$RCLONE_CONFIG_FILE" ]; then
    export RCLONE_CONFIG="$RCLONE_CONFIG_FILE"
fi

log "=== Mac backup run started ==="

# ── Repo init (idempotent -- no-ops if it already exists; the NAS almost
#    certainly already created it, this is just a safety net) ──
if ! restic -r "$REPO" snapshots --host "$HOST" >/dev/null 2>&1; then
    log "Repository not reachable/initialized from this host, attempting init..."
    restic -r "$REPO" init 2>&1 | tee -a "$LOG" || true
fi

# ── Tier 2: weekly (this repo's configs/scripts/state) ──
NOW=$(date +%s)
LAST_TIER2=$(cat "$LAST_TIER2_FILE" 2>/dev/null || echo 0)
RUN_TIER2=false
if [ $(( NOW - LAST_TIER2 )) -ge "$TIER2_INTERVAL_SECONDS" ]; then
    RUN_TIER2=true
fi

if [ "$RUN_TIER2" = true ]; then
    log "Running weekly Tier 2 backup (nas-ops repo)..."
    restic -r "$REPO" backup "$REPO_DIR" \
        --tag tier2 --host "$HOST" \
        --exclude ".DS_Store" \
        --exclude "$BACKUP_DIR/logs" \
        --exclude "$BACKUP_DIR/staging" \
        --exclude "$REPO_DIR/zotero-mac-backfill" \
        --exclude "$BACKUP_DIR/pre-*" \
        2>&1 | tee -a "$LOG"
    echo "$NOW" > "$LAST_TIER2_FILE"
    log "  Tier 2 backup complete"
else
    DAYS_AGO=$(( (NOW - LAST_TIER2) / 86400 ))
    log "Tier 2 not due yet (last run ${DAYS_AGO}d ago, runs every 7d)"
fi

# ── Retention: forget only (own host, own tags) -- prune/check happen on
#    the NAS's scheduled run against the shared repo, see header note ──
log "Applying retention policy (forget)..."
restic -r "$REPO" forget --tag tier1 --host "$HOST" --keep-daily 14 --keep-weekly 8 --keep-monthly 6 2>&1 | tee -a "$LOG"
restic -r "$REPO" forget --tag tier2 --host "$HOST" --keep-weekly 8 --keep-monthly 6 2>&1 | tee -a "$LOG"

# ── Alerting: ping Uptime Kuma push monitor on success, if configured ──
if [ -n "${UPTIME_KUMA_PUSH_URL:-}" ]; then
    curl -s "${UPTIME_KUMA_PUSH_URL}&msg=OK&ping=" >/dev/null || true
    log "Pinged Uptime Kuma push monitor"
else
    log "UPTIME_KUMA_PUSH_URL not set -- skipping push notification"
fi

log "=== Mac backup run finished ==="

# Trim old local logs (30 days)
find "$BACKUP_DIR/logs" -name '*.log' -mtime +30 -delete
