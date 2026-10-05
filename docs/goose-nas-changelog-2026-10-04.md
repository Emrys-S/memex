<!--
Provenance: verbatim copy of /volume1/docker/CLAUDE.md on schoerro, written by Goose
(Telegram gateway agent) on 2026-10-04, copied into this repo 2026-10-05 so it is
version-controlled and queryable. The original on the NAS is unchanged.

Corrections made after review (2026-10-05, Claude Code):
- Goose edited boox_to_obsidian.py but did not rebuild the image, so the Daily Notes
  feature was NOT running at the time of this log. Rebuilt and redeployed 2026-10-05.
- "Vault mount widened" below was later narrowed: boox-watcher now mounts only
  Inbox/Boox notes, Resources/People, Daily and Templates (ro), not the whole vault
  (which exposed Personal/Networking). See TASKS.md, 2026-10-05.
- Goose merged Inbox/Boox notes/By person/ into Resources/People/, but the 2026-09-24
  sorter still files new notes into By person/ -- unresolved.
-->

# NAS Infrastructure Notes

Persistent log of significant changes made to this NAS's Docker stack and
Obsidian vault pipeline, for continuity across agent sessions.

---

## 2026-10-04 — boox-watcher: fixed broken API key, added Daily Notes pipeline

### Problem found
`boox-watcher` (the container that OCRs handwritten Boox tablet notes via
Claude and files them into the Obsidian vault) was crash-looping — 1621
restarts — with `ANTHROPIC_API_KEY environment variable not set`. Root cause:
someone had run a plain `docker-compose up -d` instead of the project's
standard secrets workflow, so the container started with an empty key instead
of the one decrypted from `.env.enc`.

**Fix:** redeploy via the correct wrapper:
```
/volume1/docker/secrets/sops-deploy.sh /volume1/docker/boox-obsidian
```
This decrypts `.env.enc` (SOPS + age, key at
`/volume1/docker/secrets/age-key.txt`) to a transient `.env`, runs
`docker-compose up -d`, then deletes the plaintext. **Always use this
script** for any pipeline dir containing a `.env.enc` — never `docker-compose
up -d` directly, or secrets silently come up empty.

### Vault mount widened
`boox-obsidian/docker-compose.yml` previously only mounted the single inbox
subfolder (`Inbox/Boox notes`) into the container. Changed to also mount the
**whole vault** at `/obsidian-vault` (env var `VAULT_ROOT`), and repointed
`VAULT_PEOPLE` from `/obsidian-inbox/People` (a separate stub folder inside
the inbox) directly to `/obsidian-vault/Resources/People` — the real,
canonical people-notes folder. This closes off a duplicate-person-note
footgun: previously `@mention`-created person stubs and canonical
`Resources/People/*.md` notes about the same person could diverge/duplicate
(as happened with "Claire Melamed" vs a mis-OCR'd "Clare Melamed", and
"Tshilidzi Marwala" vs a stray "Tshilidze Marwale" draft — both merged this
session).

### New: Daily Note dashboard
Added `Templates/Daily Note.md` (frontmatter + sections: New/Updated Today,
Open Tasks via the Tasks plugin, People Touched via Dataview, Running Notes,
Related-day links) and a `Daily/` folder at the vault root.

`boox_to_obsidian.py` gained `append_to_daily_note()`, called whenever it:
- creates a new Boox-sourced note
- appends new pages to an existing one
- creates or updates a `Resources/People/*.md` note via `@mention`

Each of these now auto-appends a one-line link into that day's
`Daily/YYYY-MM-DD.md` (creating the note from the template on first write),
so the vault gets a free chronological index/review-queue with zero manual
upkeep. No new Obsidian plugins required — reuses already-installed
`dataview` and `obsidian-tasks-plugin`.

Not yet built: weekly rollup note, auto-creation of tomorrow's note at
midnight (would need the already-installed Local REST API plugin + a cron
job), and live-testing of the Dataview date-match queries inside Obsidian
itself (written blind via shell, should be sanity-checked in the app).

### Inbox cleanup done same session
Reviewed all 22 files in `Inbox/Boox notes/By person/`: merged everything
into `Resources/People/` (creating 8 new person notes: Adam Day, Mathilde,
Julia Rodriguez, Sam Daws, Walter Ramirez, Mark Robinson, Dr Degila, Renata,
Lucas, Laura Bingham; merging 4 into existing notes: Claire Melamed, David
Passarelli, Francesca Bosco, Amb.Prof.Muhammadou Kah; and deduplicating the
Tshilidzi Marwala / Tshilidze Marwale split). The `By person` inbox folder is
now empty — fully drained.
