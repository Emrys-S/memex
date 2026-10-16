# second-brain-mcp: search hygiene + structured tools (2026-10-15)

The MCP server itself (`mcp_server.py`, `query.py`) lives on the NAS at `/volume1/docker/second-brain-mcp/`
(and `query.py` is mirrored to `second-brain-index/`). This folder keeps the new, dependency-free modules and tests.

**Why**: unscoped searches were unhelpful. ~330k Zotero full-text chunks (including back-of-book author indexes and
reference lists) were merged purely by score with ~5k vault notes; the same paper came back several times; empty title
chunks and dataview boilerplate ranked high; "open tasks" and "what did I capture recently" are not semantic questions.

**`result_hygiene.py`** (query time, no re-index): drops index / reference-list / front-matter Zotero chunks and
title-only / dataview / backlink-only vault chunks; a score floor per collection (`VAULT_MIN_SCORE` 0.35,
`ZOTERO_MIN_SCORE` 0.42); at most 2 chunks per document (Zotero deduped by normalised title); rank-fusion (RRF) of the
two collections so vault notes are not drowned. A strong match on an empty title chunk pulls in that note's real
content (`ANCHOR_MIN_SCORE` 0.55), which fixes entity questions ("what have I noted about <person>").
`search()` also degrades gracefully: if one collection times out (cold Qdrant after a restart) it returns the other
and the tool says so, instead of erroring. If both fail it re-raises the real error rather than reporting "no
matches" (fixed 2026-10-16; the first version used a bare `raise` outside an `except` and let the second warning
overwrite the first).

**`vault_tools.py`**: `open_tasks` (open checkboxes across Projects / Boox / People / Daily, soonest due first, overdue
flagged, `[marvin:ID]` hidden) and `recent_captures` (LinkedIn / Bluesky / Boox by each note's own date).

**LibreChat connection**: the server answers unauthenticated requests with 401 (bearer auth). LibreChat v0.8.x probes
for OAuth without credentials, reads that 401 as "OAuth required" and then never connects with its token (log:
`OAuth Required: true`, `Tools: undefined`). The `second-brain` entry in LibreChat's `librechat.yaml` therefore sets
`requiresOAuth: false`; a healthy start logs `OAuth Required: false` and lists all six tools. After any restart the
service needs ~1 minute to listen, so restart LibreChat after it if the log says "0 tools".

**Tests**: `python3 -m unittest test_hygiene test_vault_tools` (17 tests; also run inside the service image).
Pre-change backups on the NAS: `*.bak-20261015-pre-hygiene`, `-pre-anchor`, `-pre-anchor2`, `-pre-degrade`,
`*.bak-20261016-pre-degrade2`.
