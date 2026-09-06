#!/bin/bash
# Waits for both Mac backfill jobs (Personal Library, Remaining Libraries) to
# finish, then restarts the NAS's full_backfill.py with SKIP_LIBRARIES
# covering everything the Mac already did -- so the NAS only keeps working
# on Z-Library instead of redundantly redoing the 16 small libraries once it
# gets there. Kills the current NAS process by its host PID (docker top,
# not a container-namespace PID) since the zotero-indexer image has no
# pkill/procps.
set -uo pipefail
cd "$(dirname "$0")"

echo "$(date '+%Y-%m-%d %H:%M:%S')  Monitor started, waiting for both Mac jobs to complete..."

while true; do
  if [ -f checkpoint.json.done ] && [ -f remaining_checkpoint.json.done ]; then
    break
  fi
  sleep 300
done

echo "$(date '+%Y-%m-%d %H:%M:%S')  Both Mac jobs complete."

SKIP_LIST=$(python3 -c "
import json
data = json.load(open('remaining_checkpoint.json.done'))
names = list(data['completed'].keys())
names.append('Personal Library')
print(','.join(names))
")

echo "Skip list for NAS (${#SKIP_LIST} chars): $SKIP_LIST"

NAS_PID=$(ssh schoerro-agent "sudo /usr/local/bin/docker top zotero-indexer" | awk '/full_backfill.py$/ {print $2}')

if [ -z "$NAS_PID" ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S')  Could not find running full_backfill.py PID on NAS -- aborting, nothing killed or restarted. Manual follow-up needed."
  exit 1
fi

echo "$(date '+%Y-%m-%d %H:%M:%S')  Killing NAS full_backfill.py (host PID $NAS_PID)..."
ssh schoerro-agent "sudo kill $NAS_PID"
sleep 5

ssh schoerro-agent bash -s <<EOF
sudo /usr/local/bin/docker exec -d -e SKIP_LIBRARIES="$SKIP_LIST" zotero-indexer sh -c "python3 /data/full_backfill.py >> /data/full_backfill.log 2>&1"
EOF

echo "$(date '+%Y-%m-%d %H:%M:%S')  NAS job restarted, now scoped to Z-Library only (resumes from its own checkpoint)."
