#!/bin/bash
# push_voice_skill.sh
# Pushes the Mac's emrys-voice plugin skill to the NAS and triggers the NAS-side
# rebuild of LibreChat's copy of that skill. Run it after ANY edit to the
# emrys-voice plugin skill (see "Working principles" in CLAUDE.md).
#
# Edits to voice-notes.md do NOT need this: that file reaches the NAS via
# Syncthing and the NAS re-syncs the LibreChat skill on its own hourly task.
# Running this script anyway makes either kind of change land immediately.
set -euo pipefail

SKILLS_ROOT="$HOME/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin"
SRC=$(find "$SKILLS_ROOT" -path '*/skills/emrys-voice/SKILL.md' -print0 2>/dev/null | xargs -0 ls -t 2>/dev/null | head -1)
[ -n "$SRC" ] && [ -s "$SRC" ] || { echo "emrys-voice SKILL.md not found under $SKILLS_ROOT" >&2; exit 1; }

# Tailscale alias first, LAN key as fallback (the alias needs the Mac's Tailscale running).
if ssh -o BatchMode=yes -o ConnectTimeout=6 schoerro-agent true 2>/dev/null; then
  SSH=(ssh schoerro-agent)
else
  SSH=(ssh -i "$HOME/.ssh/id_ed25519_schoerro_new" claude-agent@192.168.1.161)
fi

echo "pushing: $SRC"
"${SSH[@]}" 'mkdir -p /volume1/docker/librechat/voice-skill && cat > /volume1/docker/librechat/voice-skill/plugin-SKILL.md.new && mv /volume1/docker/librechat/voice-skill/plugin-SKILL.md.new /volume1/docker/librechat/voice-skill/plugin-SKILL.md' < "$SRC"
"${SSH[@]}" 'sudo /usr/local/bin/librechat-voice-skill-sync.sh' 2>&1 | grep -v -e '^\*\*'
