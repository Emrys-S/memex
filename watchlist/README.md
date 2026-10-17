# watchlist: key figures on the open web (2026-10-16)

Tracks what the people and institutions you care about publish, **not just what you have seen or interacted with**, and
surfaces only the most significant few in the daily digest, under *From people you track*.

**Pieces**
- **The watchlist** is a markdown table in the vault (`Resources/Watchlist/Watchlist.md`): Name, Community, Tier (A person, B
  institution/outlet), Bluesky handle, feed URLs, Status, Notes. Only `confirmed` rows are polled. You edit it in Obsidian.
- `discover.py` (Mac, one-off, free public APIs): proposes Bluesky accounts and feeds from a **private seed file kept outside the
  repo** (who you follow is personal). Never overwrites a row you already edited. Name search finds decoys, so a Bluesky match is
  auto-confirmed only with an exact name, real activity, no comparably popular namesake, **and a bio that looks like the field**
  (a namesake from another field was auto-matched once); a handle you already captured on Bluesky wins outright. Everything else
  stays `candidate` until you change it.
- `poller.py` (NAS, one-shot): polls Bluesky's public API and RSS/Atom feeds (stdlib HTTP only), drops what it has seen and what you
  already have in the vault, scores each new item, keeps a candidate pool, picks a few for the digest, writes a health note.
  Runs as a throwaway container of the existing indexer image (it already has the embedding model, Qdrant client and Anthropic SDK)
  via `/usr/local/bin/watchlist-run.sh`, DSM Task Scheduler task id 15 at :20 past 00/04/08/12/16/20.
- `watchlist_core.py`: all the pure logic (table parsing, feed and Bluesky parsing, scoring, selection, discovery heuristics), tested.

**Deciding without scrolling: the review note.** The table has 100+ rows, so the poller writes `Review/Watchlist review.md`: only
rows that still need a decision, one checkbox each, in four groups: (1) *Is this them?* (the account found, with its bio, follower
count and a profile link; tick to start tracking), (2) *Which account is theirs?* (the accounts sharing the name, one box each plus
"none of these"), (3) *Nothing to track yet* (tick to skip, or write `+ @handle` / `+ https://feed-url` on the line to add a source),
(4) *Outlets with no feed found* (homepage linked; tick to skip or add the feed). `[-]` on any line also means skip. Each run applies
the ticks to the watchlist table, then regenerates the note without the resolved lines (so it only shrinks, and new candidates from a
later discovery run appear). Bios are cached for a week. `REVIEW_ONLY=1` applies ticks and refreshes the note without polling.

**Significance is computed, not guessed.** Four signals: *format* (a full article beats a linked post beats chatter),
*relevance* (similarity to your own notes via the existing Qdrant index, People notes excluded from citations), *convergence* (how
many other tracked sources link the same thing, this run plus the last 7 days) and *reach* (Bluesky engagement against that
author's own median). Caps: threshold 0.45, 2 per source and 7 per day, one item per link (an article and the post pointing at it
are one story), nothing you already have in the vault, only items published in the last 3 days. A small model writes the one-sentence
summary; the "why" is assembled from the signals, so it cannot embellish.

**Guards found by real data:** feeds whose dates are bulk-stamped (a dozen posts at the same minute, or most on one day: a rebuild)
are skipped, because their old posts look new; feeds listed twice (`/feed`, `/feed/`) are one source; per-source health
(failing / quiet 90+ days / dates unreliable) is in the generated note `Review/Watchlist status`.

**Digest integration**: `ingest_digest.py` got a `watchlist` log source and a prompt step that includes EVERY tracked item in its
own section (the filter already chose them), each linked by the person's name, with the reason restated and no speculation.

**Not covered (on purpose or yet):** LinkedIn (human-triggered only, never automated), reposts, replies, paywalled full text (titles
and the feed's own summary only), scholarly output (OpenAlex), email-only sources (point them at the newsletter ingest address),
learning from your keep/skip choices.

Run by hand: `sudo env DRY_RUN=1 /usr/local/bin/watchlist-run.sh` (scores everything, writes nothing); `sudo env REVIEW_ONLY=1 ...` (apply review ticks now). Tests:
`python3 -m unittest test_watchlist_core` (19 tests, fictional data).
