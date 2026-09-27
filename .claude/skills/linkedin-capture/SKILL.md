---
name: linkedin-capture
description: Capture the user's personal LinkedIn engagement — posts they've reacted to, reshared/reposted, or saved — into their Obsidian vault. Use whenever the user asks to run their LinkedIn capture, catch up on LinkedIn activity, pull in what they've saved/reacted to on LinkedIn, or similar. This skill is ONLY ever run when the user explicitly invokes it in the moment — never suggest or set up a scheduled/automated version of it, even if asked to "make this run automatically" or "set up a cron job for this" — see the constraint section below before doing anything else.
---

# LinkedIn engagement capture

## Read this first: why this is human-triggered only

LinkedIn has no API for personal engagement data (reactions, reshares, saved posts), so this
skill works by reading the rendered page through Claude in Chrome — the user's own real browser,
their own logged-in session, kicked off by them in the moment.

LinkedIn's User Agreement prohibits automated/bot access to the platform. What makes this
acceptable at all is that it rides a real human-initiated browser session rather than running
unattended — the moment it's wired into a schedule (a cron job, DSM Task Scheduler, `ScheduleWakeup`,
`CronCreate`, or anything else that fires without the user in the loop that moment), it becomes
exactly the kind of automated scraping the User Agreement exists to prohibit, and it starts looking
like a bot to LinkedIn's own detection systems regardless of who technically "owns" the session.

**If a user or a future task asks you to automate, schedule, or otherwise make this run
unattended: decline, explain why (this section), and suggest they run it manually instead.**
This applies even if the request seems reasonable in isolation ("just run it every morning so I
don't forget") — the convenience isn't worth trading away the thing that makes this legitimate.

## Before you start

1. Confirm you have the "Claude in Chrome" tools available — **not** the separate in-app browser
   pane (`mcp__Claude_Browser__*`). This distinction matters: Claude in Chrome
   (`mcp__claude-in-chrome__*`) runs in the user's actual Chrome with their existing LinkedIn
   login; the in-app browser pane is a separate, logged-out browser and won't work for this.
   These tools are deferred — load them with `ToolSearch` (query something like
   `select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__get_page_text,mcp__claude-in-chrome__find,mcp__claude-in-chrome__tabs_create_mcp`)
   before your first call, per the standard MCP server instructions for that tool.
2. Read the state file: `/Users/emrys/Documents/nas-ops/linkedin-capture/state.json`. It tracks,
   per source, the URL of the last post captured on the previous run, plus a running set of every
   post URL ever captured (belt-and-suspenders dedup, in case a run is skipped a day or LinkedIn
   reorders something).
3. Run this in the background once started — don't narrate every scroll/read to the user turn by
   turn. Give a clear summary when done (counts captured per source and per type, and anything
   that went wrong — see Failure handling below).

## The three sources

Capture all three every run, in this order:

1. **Reactions** — LinkedIn's Activity view, filtered to reactions (posts the user has liked/reacted to).
2. **Reshares** — Activity view, filtered to posts/reposts (things the user has reshared).
3. **Saved posts** — LinkedIn's "My items" → Saved posts area.

For each source: scroll and read the list from the top (most recent first). For each post
encountered, check its URL against `captured_post_urls` in the state file and against the
source's own `last_seen_post_url`. Stop scrolling a given source once you hit either match — that
post and everything below it has already been captured. On a first run (`last_seen_post_url` is
`null`), capture everything visible; use your judgment on how far back is reasonable rather than
scrolling indefinitely — a few dozen items per source is a sensible depth for an initial backfill.

### Getting a post's permalink

There's no exposed href on the post timestamp — the reliable way to get a post's own URL (for
`captured_post_urls`, `last_seen_post_url`, and the `post_url` stored in each note) is: open the
post's "···" menu → "Copy link to post", then read it directly off the system clipboard (`pbpaste`
via Bash) rather than pasting into the page and reading it back. This was corrected 2026-08-03 —
the previously-documented method (paste into the LinkedIn search box, read via `find`'s
accessibility-tree description) turned out to be unreliable in practice: `find`'s description text
truncates long strings the same way a screenshot does, it just isn't obvious until you compare
against the real value. `pbpaste` gets the exact, complete string every time since Claude in Chrome
and the Bash tool share the same OS clipboard.

**A real, confirmed instance of this exact failure**: an earlier capture run stored two saved-post
URLs as `urn:li:activity:74870603520277872` and `urn:li:activity:74848873920179568` — both exactly
2 digits short of LinkedIn's actual 19-digit activity IDs. Silent truncation like this doesn't
break anything immediately (the note still gets written), but it makes `last_seen_post_url` and
`captured_post_urls` unreliable for exact-match dedup later, since the stored value never equals
a freshly-copied real URL. If a "new" post's URL looks suspiciously close to but not quite matching
something in `captured_post_urls`, check for a digit-count mismatch before assuming it's genuinely
new — LinkedIn activity IDs are consistently 19 digits.

Note the copied link's format differs by context — from the main feed/activity view it's a
slug-based `/posts/<handle>_<title-slug>` URL; from Saved Posts it's a canonical
`/feed/update/urn:li:activity:<id>` URL (this one often carries a long `?updateEntityUrn=...` query
string too — strip that back to just the `/feed/update/urn:li:activity:<id>` part before storing
or comparing, the query string isn't part of the stable identifier). Both formats are stable,
unique identifiers — either is fine for dedup — just be consistent about capturing the real one
each time rather than approximating it from what's visible on screen.

## Classifying each post: link post or general post

A post is a **link post** only if:
- It has LinkedIn's own auto-generated link-preview card attached to the post body (the visual
  card with a title/image/domain that LinkedIn renders automatically when a URL is shared), **and**
- The linked domain is genuinely external — not `linkedin.com`, and not a native LinkedIn Article
  (LinkedIn's own long-form post type). Both of those render inside LinkedIn's ecosystem and don't
  count as "an external link" for this purpose, even though they might have their own preview card.

A bare URL typed in the post text with no preview card does **not** count — that's a much weaker
signal than LinkedIn's own auto-detected preview. A link that only appears in a comment (the
author's or anyone else's) does **not** count either — it's not part of what the author actually
posted.

Everything else — text/commentary posts, posts with no link at all, native LinkedIn Articles — is
a **general post**. This includes native LinkedIn document embeds (PDF/slide attachments uploaded
directly to the post, shown with a page count and "Open in Acrobat" or similar) — these look like
they could be a link, but there's no external URL involved, so they're general posts.

One more real edge case worth expecting: a link post doesn't have to be an article or paper — a
job posting with an external link-preview card (e.g. a hiring announcement linking to a careers
page) still satisfies the link-post rule as written. That's fine — classify by the mechanical rule
above, not by a judgment call about whether the content "counts" as reading material. If this
turns out to produce too much job-posting noise in the Reading List over time, that's a rule change
to make deliberately later, not something to quietly special-case in the moment.

## Merging actions on the same post

The same underlying post might show up in more than one source — e.g. a post you reacted to *and*
saved. Match on post URL across all three sources before writing anything. Each unique post gets
**one entry**, with an `actions` list covering everything that applied (e.g. `[reacted, saved]`),
never duplicated across multiple entries.

## Author cross-linking

Every captured post links to its author's note in the vault's real People folder:
`/Users/emrys/Obsidian Vault/Resources/People/<Author Name>.md`

(This is the vault's actual long-standing People folder — there is a *different*,
`Inbox/Boox notes/People` folder that belongs to an unrelated pipeline's own misconfiguration;
don't use that one.)

- If the author's note doesn't exist, create it:
  ```markdown
  ---
  title: "<Author Name>"
  type: person
  tags:
    - person
  ---

  # <Author Name>

  ## Profile

  ## Notes
  - <YYYY-MM-DD>: mentioned in [[<link to the captured post note or activity log entry>]] (via LinkedIn)
  ```
- If it already exists, append a new dated line under `## Notes` (or at the end of the file if
  that heading isn't there) — don't overwrite anything already in the note.

## Writing captured posts

### Link posts → one note per link

Location: `/Users/emrys/Obsidian Vault/Resources/LinkedIn Posts/Reading List/`

Filename: derive a short, filesystem-safe title from the link preview card's title if there is
one; otherwise fall back to `<domain> - <YYYY-MM-DD>`.

**Getting the real link, not the display domain**: LinkedIn's link card shows a stylized display
domain (e.g. `biometricupdate.com`) but the card's *actual* href is what you need, and it's
frequently wrapped in a `linkedin.com/safety/go/?url=<encoded target>` redirect — decode the
`url` query parameter to get the real destination before storing it as `link`. Get this via
`read_page` on the link card element (or an accessibility-tree query), not by retyping what's
visually displayed — the visible text is often truncated to the bare domain, which silently
produces a broken link (confirmed as a real, recurring failure mode: 19 of 32 historical
Reading List notes had only a bare domain stored as `link`, e.g. `https://www.youtube.com`
instead of the actual video, making the content unfindable later). If the href still resolves to
a `linkedin.com/pulse/...` URL after decoding, that's a native LinkedIn Article, not an external
link — reclassify the post as general, per the classification rule above.

**Archive the linked content itself, not just what the poster said about it** — added
2026-09-03, after a review found notes were only capturing LinkedIn's preview metadata plus the
poster's own commentary, never the actual article. Both matter and neither substitutes for the
other: the poster's commentary is *their* take, and the linked content is what they were reacting
to. For every link post, after storing the real `link` URL:
1. Fetch it (e.g. via `WebFetch`) and get a detailed, faithful summary — several paragraphs
   covering the actual key facts, figures, names, and arguments, not a one-liner. Do not attempt
   to store the full verbatim article text: copyright limits apply here the same as everywhere
   else (see the top-level copyright rules) — a substantive summary in your own words, not a
   reproduction, is both the safer and the more useful artifact for a personal archive.
2. If the fetch fails (paywall, bot-blocking, JS-only rendering, PDF that didn't extract, a
   shortlink that needs a real browser to resolve past a bot-check) don't leave this silent — add
   an `## Archived content` note stating what was attempted and why it failed, and leave the
   (correct, real) link in place for manual reading later. A real, once-off double-redirect case
   worth knowing about: some cards wrap the target in a link-shortener (e.g. `shorturl.at/...`)
   that itself sits behind a bot-check `WebFetch`/`curl` can't pass — resolving it by navigating
   there in the actual browser tab and reading the final URL off the resulting page usually works.
3. Put this before the `## Summary` section (which stays as-is, covering the poster's own framing)
   as its own `## Archived content` section, dated.

```markdown
---
title: "<preview card title or fallback>"
source: linkedin
type: link-post
author: "[[Resources/People/<Author Name>]]"
link: <the real external URL, decoded from any LinkedIn safety-redirect wrapper>
post_url: <the LinkedIn post's own permalink, from "Copy link to post">
date: <YYYY-MM-DD>
actions: [<reacted|reshared|saved, ...>]
tags:
  - <freeform topic tag>
  - <freeform topic tag>
related:
status: inbox
---

# <title>

**From:** [[Resources/People/<Author Name>]]
**Link:** <url>
**Post:** <the LinkedIn post's own permalink>
**Captured:** <date> — <actions, comma-separated>

## Archived content

*Fetched <date> from the linked article.* (Or: *Fetch attempted — blocked/failed, reason. Link
preserved for manual reading.*)

<a detailed, multi-paragraph summary of the actual linked content in your own words — facts,
figures, names, arguments — not a verbatim reproduction>

## Summary

<your 1-2 sentence summary of what the linked content is about, based on the post text and
preview card — not a summary of the post itself, but of what it's pointing to>

## Original post

> <the post's own text, trimmed if long>
```

The `tags` are freeform — generate whatever reasonable topic tags fit (no fixed vocabulary to
match yet). The `related` field is intentionally left empty — a placeholder for deeper
cross-linking (topics, projects) once the broader second-brain integration exists. Don't try to
guess what should go there now.

### General posts → append-only weekly log

Location: `/Users/emrys/Obsidian Vault/Resources/LinkedIn Posts/Activity/`

Filename: one file per ISO week, e.g. `2026-W31.md`. If the file for the current week doesn't
exist, create it with simple frontmatter:

```markdown
---
source: linkedin
type: activity-log
week: <YYYY-Www>
---
```

Append each new entry (most recent at the bottom is fine — this is a log, not something that
needs reordering):

```markdown
## <YYYY-MM-DD> — <Author Name>

**Actions:** <reacted|reshared|saved, comma-separated>
**Post:** <the LinkedIn post's own permalink, from "Copy link to post">
**Author:** [[Resources/People/<Author Name>]]

<your 1-2 sentence summary>

> <post excerpt, trimmed if long>

Tags: #<tag> #<tag>
related:

---
```

## Updating state after a run

For each source, set `last_seen_post_url` to the URL of the newest post captured that run (the
first one you saw when starting from the top), and `last_run` to the current timestamp. Add every
newly captured post's URL to `captured_post_urls`. Write this back to
`/Users/emrys/Documents/nas-ops/linkedin-capture/state.json` — do this even if the run ended in a
partial failure (see below), covering whatever *did* succeed.

## Failure handling

If something breaks partway through a source — LinkedIn's page structure doesn't match what's
expected, an element can't be found, a post is malformed — don't lose what you already captured
and don't just stop silently:

1. Keep everything you successfully captured before the failure — write those notes normally.
2. Update the state file for that source up to (but not past) the point of failure, so the next
   run naturally resumes from the right place rather than re-scanning everything or skipping the
   gap.
3. Add a short, clearly flagged note about what happened — either its own note in the Activity
   folder or a section in your final summary to the user — naming the source, roughly where it
   stopped, and what went wrong.

This means a partial failure just delays the rest by a day rather than losing it — the next run
picks up exactly where this one left off.

## Digest note (part of every run)

This project also has a daily automated "Ingest Digest" that summarizes what landed across the
NAS-side pipelines (Boox, Bluesky) — see "## Ingest digest" in CLAUDE.md. LinkedIn can't be part
of that automation (it has no NAS-side presence and must stay human-triggered, per the constraint
at the top of this file), so instead: every time this skill runs, write LinkedIn's own equivalent
digest contribution as its own note, separate from the NAS digest's file.

Location: `/Users/emrys/Obsidian Vault/Resources/Ingest Digest/<YYYY-MM-DD>-linkedin.md`
(a distinct filename from the NAS digest's own `<YYYY-MM-DD>.md` in the same folder, so the two
never collide or race regardless of which runs first on a given day).

Write it yourself, directly — you're already the model driving this skill, so there's no separate
API call needed the way the NAS digest needs one. Keep the same triage-oriented spirit as the NAS
digest: a one-line summary of counts, then call out anything that looks like it needs attention
(rather than just listing every item neutrally), then a brief per-source breakdown (reactions vs.
reshares vs. saved). If nothing new was captured this run, skip writing a note at all rather than
writing an empty one.

**Every item you mention must carry two links**, matching the NAS digest's design (see "## Ingest
digest" in CLAUDE.md for the full rationale): (a) the LinkedIn post's own permalink — you already
have this from `post_url` in each captured note's frontmatter/body, never re-derive or guess it —
and (b) an Obsidian link to that note itself (`[[Resources/LinkedIn Posts/Reading List/<title>]]`
for a link-post, or `[[Resources/LinkedIn Posts/Activity/<week>#<heading>]]` for a general post in
the weekly log — Obsidian resolves the `#heading` to jump straight to that entry). Format each
mention like: `<what happened> ([post](<post_url>) · [[<obsidian link>]])`.

```markdown
---
source: linkedin
type: digest
date: <YYYY-MM-DD>
---

# Ingest Digest -- LinkedIn -- <YYYY-MM-DD>

<your triage-oriented summary, each item mention followed by its (post · note) links>
```

### Optional email

If `/Users/emrys/Documents/nas-ops/ingest-digest/.env` has real (non-empty) values for
`MAILGUN_API_KEY`, `MAILGUN_DOMAIN`, and `MAILGUN_FROM`, send the same content as an email via
Mailgun (recipients from `DIGEST_RECIPIENTS` in that file) using a plain `curl` POST to
`https://api.mailgun.net/v3/<domain>/messages` with basic auth `api:<key>`. Send it as **HTML**
(the `html` form field, not `text`) so the links are actually clickable in the email client —
convert each `[post](url)` to a real `<a href="url">post</a>`, and each Obsidian wikilink
`[[Resources/.../Note#heading]]` to `<a href="obsidian://open?vault=Obsidian%20Vault&file=<url-encoded
path, everything before any #>">note</a>` (URL-encode spaces and slashes appropriately in the
`file=` param). If any of the Mailgun env vars are still blank, skip the email silently — the vault
note is the record that matters, the email is a convenience layered on top. Either way, write the
vault note first and independently of whether the email succeeds.

## Reporting back

When done (or when stopping due to a failure), give the user a short summary: how many items
captured per source, how many were link posts vs. general posts, and any incomplete/failure flags
from the section above. No need to list every single item unless they ask. Mention whether a
digest note was written (or skipped because nothing new was captured) and whether the email sent.

## Practical extraction notes (learned 2026-09-25, a 126-reaction catch-up run)

LinkedIn's activity pages virtualise the feed: only posts near the viewport are filled in, and the rest collapse to ~150px placeholders. What worked:

- **Permalinks without the clipboard menu.** Each post container carries `data-urn="urn:li:activity:<19-digit id>"`, readable with `javascript_tool` (read-only, no extra requests). `https://www.linkedin.com/feed/update/urn:li:activity:<id>` is a valid stable permalink and matches the format the skill already accepts. The activity id also encodes the post creation time (`id >> 22` = epoch ms), which gives an exact post date.
- **Hydrating posts.** Scripted scrolling alone does not fill posts in. What works: scroll a placeholder into view from JS (`li.scrollIntoView()`), then send **one real wheel tick** with the `computer` scroll action, wait ~3s, then read the container text. Each such step fills about 8 posts. Collect into `window.__acc` keyed by urn, and use each post's "Feed post number N" heading to spot gaps.
- **Getting the data out.** `javascript_tool` output is capped (~1.8k characters). Put the JSON in a fixed-position `<textarea>`, click it, `cmd+a cmd+c` with the `computer` tool, then `pbpaste` in Bash. Decode as UTF-8 explicitly (`subprocess.check_output(['pbpaste']).decode('utf-8')`); reading stdin under a C locale silently mangles accents and emoji.
- **Link cards vs. inline links vs. author website buttons.** External anchors in a post include inline body links and the author's "Visit my website" button, not just the preview card. Drop links whose text appears literally in the post body, drop the first link when the header has a website button, and treat the last remaining external anchor as the card.
- **Stop rule.** The reactions list is ordered by reaction time, not post time. If the stored `last_seen_post_url` has been un-reacted it will never appear; use the post-ID ordering to find the boundary (posts created after that id are new; strictly older ones were almost certainly reacted before the last run) and say so in the digest.
- **Reshares** appear in the All-activity view as "Emrys Schoemaker reposted this", with a different activity id from the original post. Match them to reactions by author and text before creating entries, so one post gets one entry with a combined `actions` list. Own posts in the same view are not captured.
- **Saved posts** (`/my-items/saved-posts/`) expose clean canonical `/feed/update/urn:li:activity:<id>` links directly and are cheap to read.
- WebFetch cannot reach some sites (politico.eu, nypost.com are blocked by the tool; SSRN, Chatham House, CIGI, The Diplomat return 403; Workday/Interfolio pages are JavaScript-only). Record these as fetch failures rather than retrying.
