#!/usr/bin/env bash
# Weekly unattended review of previously-blocked LinkedIn Reading List link fetches.
# Does not touch LinkedIn itself — only retries third-party article/job URLs already
# stored in each note. See ../.claude/skills/linkedin-link-review/SKILL.md for the procedure.
set -uo pipefail
cd /Users/emrys/Documents/nas-ops

/Users/emrys/.local/bin/claude -p "/linkedin-link-review" \
  --allowedTools "Read,Edit,Write,Bash,WebFetch,Glob,Grep,mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__get_page_text,mcp__claude-in-chrome__find,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__tabs_close_mcp" \
  --chrome \
  --permission-mode acceptEdits \
  --max-budget-usd 10 \
  --add-dir "/Users/emrys/Obsidian Vault" \
  --output-format text
