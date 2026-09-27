#!/bin/bash
# librechat-voice-skill-sync.sh
# Rebuilds LibreChat's `emrys-voice` skill from its two sources and restarts
# LibreChat only when the result actually changed:
#   1. plugin-SKILL.md   -- the emrys-voice plugin skill, pushed from the Mac
#                           by nas-ops/librechat-voice-sync/push_voice_skill.sh
#   2. voice-notes.md    -- the shared voice profile in the vault (arrives via
#                           Syncthing); only its "Learned from edits" section is
#                           appended, since the base profile is already in (1).
# LibreChat loads deployment skills once at startup and caches their text, so a
# restart is the only way a change goes live. A malformed skill makes startup
# throw, so the swap is health-checked and rolled back if LibreChat won't start.
set -uo pipefail
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

LC=/volume1/docker/librechat
PLUGIN="$LC/voice-skill/plugin-SKILL.md"
NOTES="/volume1/docker/obsidian-vault/vault/Resources/Emrys Voice/voice-notes.md"
OUTDIR="$LC/skill/emrys-voice"
OUT="$OUTDIR/SKILL.md"
LOG="$LC/voice-skill/sync.log"
log() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }

[ -s "$PLUGIN" ] || { log "no plugin skill pushed yet -- nothing to do"; exit 0; }
[ -s "$NOTES" ]  || { log "voice-notes.md missing at $NOTES"; exit 1; }

TMP=$(mktemp "$LC/voice-skill/build.XXXXXX")
trap 'rm -f "$TMP"' EXIT
{
  cat "$PLUGIN"
  printf '\n\n---\n\n## Learned from edits (auto-synced from the shared voice-notes.md)\n\n'
  printf 'Refinements Emrys has made to drafts over time. Apply them on top of everything above; where one conflicts with the guidance above, this section wins.\n\n'
  awk '/^## Learned from edits/{f=1;next} /^## Process note/{f=0} f' "$NOTES"
} > "$TMP"

if [ -f "$OUT" ] && cmp -s "$TMP" "$OUT"; then
  log "unchanged"; exit 0
fi

# A build that already failed to start LibreChat is not retried until its inputs change
# (otherwise a bad source would restart LibreChat twice an hour, forever).
BUILD_HASH=$(md5sum "$TMP" | cut -d' ' -f1)
if [ -f "$LC/voice-skill/failed.hash" ] && [ "$(cat "$LC/voice-skill/failed.hash")" = "$BUILD_HASH" ]; then
  log "this build already failed to start LibreChat -- skipping until the sources change"; exit 1
fi

mkdir -p "$OUTDIR"
[ -f "$OUT" ] && cp -p "$OUT" "$OUT.prev.tmp"   # kept outside the skill dir's SKILL.md name; removed below
[ -f "$OUT.prev.tmp" ] && mv "$OUT.prev.tmp" "$LC/voice-skill/SKILL.md.prev"
cat "$TMP" > "$OUT"; chmod 644 "$OUT"
log "skill rebuilt ($(wc -c < "$OUT") bytes) -- restarting LibreChat"

docker restart LibreChat >/dev/null
up=0
for i in $(seq 1 30); do
  sleep 5
  if curl -fs -o /dev/null http://127.0.0.1:3080/api/config; then up=1; break; fi
done

if [ "$up" = 1 ]; then
  rm -f "$LC/voice-skill/failed.hash"
  log "LibreChat back up with the new skill"
  exit 0
fi

# Capture first: `docker logs | grep -q` under pipefail dies of SIGPIPE and reads as "no match".
LOGS=$(docker logs --since 4m LibreChat 2>&1 || true)
if grep -q -E 'Failed to start server|SKILL\.md|Deployment skill' <<<"$LOGS"; then
  log "LibreChat failed to start on the new skill -- rolling back"
  echo "$BUILD_HASH" > "$LC/voice-skill/failed.hash"
  if [ -f "$LC/voice-skill/SKILL.md.prev" ]; then cp "$LC/voice-skill/SKILL.md.prev" "$OUT"; else rm -rf "$OUTDIR"; fi
  docker restart LibreChat >/dev/null
  log "rolled back; LibreChat restarting on the previous skill"
  exit 1
fi
log "LibreChat slow to come up (no skill error in logs) -- leaving the new skill in place"
exit 0
