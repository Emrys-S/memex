---
name: linkedin-link-review
description: Weekly unattended review of Reading List notes whose linked-article fetch previously failed (bot-blocked, paywalled, JS-only). Retries each one, updates the note if resolved. Does NOT touch LinkedIn itself — only re-fetches third-party article/job URLs already stored in each note's `link` field, so the linkedin-capture skill's human-only constraint does not apply here.
---

# LinkedIn Reading List link review (unattended, scheduled)

## Scope

This is a maintenance pass over `/Users/emrys/Obsidian Vault/Resources/LinkedIn Posts/Reading List/`
— it never opens linkedin.com, never reads LinkedIn engagement data, and never uses the "Copy link
to post" flow. It only re-attempts fetching URLs already stored in each note's `link:` frontmatter
field (news articles, job postings, papers — ordinary third-party web content). Because of that,
this is safe to run unattended and on a schedule, unlike the `linkedin-capture` skill itself.

## Procedure

1. Find candidate notes: search `Resources/LinkedIn Posts/Reading List/*.md` for files whose
   `## Archived content` section indicates a previous failure — look for phrasing like "blocked",
   "403", "refuses automated fetching", "no usable content", "JS-rendered", "no text content to
   archive". Skip notes that already have real archived content (no failure marker).

2. For each candidate, read its `link:` frontmatter value, then retry in this order:
   a. `WebFetch` on the URL directly, asking for a detailed multi-paragraph summary (facts,
      figures, names, arguments — not a one-liner). Some sites' blocks are transient or policy
      may have changed since the last attempt.
   b. If that fails again, and the URL looks like a redirector/shortlink or the failure looked
      like a bot-check (Cloudflare challenge, 403 from a site that normally allows fetching), try
      resolving it with the real browser instead: navigate there via Claude in Chrome
      (`mcp__claude-in-chrome__*` — load via `ToolSearch` if deferred), wait for the page to
      settle, then use `get_page_text` to extract the rendered content and summarize that
      yourself. A real browser executing JS and passing bot-checks succeeds in cases WebFetch
      cannot — this resolved a shortlink wrapped in a Cloudflare challenge during the original
      2026-09-03 backfill.
   c. If the resolved browser URL differs from what's stored in `link:` (e.g. a shortlink now
      resolved to its real destination), update `link:` in both the frontmatter and the `**Link:**`
      line to the resolved URL.
   d. Close any browser tab you opened once you're done with it.

3. If a retry succeeds: replace the note's `## Archived content` section with the real summary,
   formatted the same way `linkedin-capture` writes it (dated, "*Fetched <date> from the linked
   article*" or "*Resolved <date> via browser navigation, previously blocked*"), and remove the
   old failure note.

4. If a retry still fails: leave the note as-is. Don't rewrite the failure note on every
   unsuccessful attempt (avoid noisy diffs) — only touch it if you actually resolved something, or
   if the failure *reason* has changed (e.g. was "403", now genuinely "paywall" after actually
   viewing the page).

5. Keep a running tally: resolved this run, still blocked, total candidates checked.

## Reporting back

End with a short, concrete summary: how many were resolved, which ones (title + reason it now
works), how many are still blocked and why (grouped by cause — paywall vs. bot-check vs.
JS-rendered — so a pattern is visible if e.g. Reuters is *always* going to be a dead end vs. worth
retrying again next week). This runs unattended via a scheduled `launchd` job piping `claude -p`
output to a log file (`~/Documents/nas-ops/linkedin-link-review/run.log`) — write the summary as
the final text output so it's the last thing in that log, easy to scan without re-reading the
whole transcript.

If every candidate is still blocked (nothing resolved), still write a short summary rather than
going silent — a "checked 9, resolved 0" run is a real, useful data point, not a no-op to skip
reporting.
