# Cowork ↔ Code handoff log

Planning/architecture conversations about this project also happen in Claude's
"Second Brain" project (claude.ai chat, not this repo) — that's a separate
context store Claude Code never sees automatically, and this file never sees
Code's session logs automatically either. Use this file to hand off anything
decided or raised on one side that the other side needs.

**Format**: newest entry at the top. Tag direction (`Cowork → Code` or
`Code → Cowork`), one-line context, then the actual content. Once an item's
been read and acted on (or superseded), fold the essence into `CLAUDE.md`'s
proper sections and trim the entry here to a one-line pointer — this file is
a relay, not a permanent archive; `CLAUDE.md`/`TASKS.md` stay authoritative.

---

## 2026-09-15 — Code → Cowork

Browser access to the second brain from a locked-down (UNU-issued) work
computer, worked out in full and written up as a dated update under deferred
architecture decision **#6** in `CLAUDE.md` — read that instead of
duplicating here. This entry is just the pointer.

**Addendum (Cowork, same day)**: the `second-brain-remote-connector-design.md`
file Code flagged as unlocated is resolved — it only ever existed as a doc in
the Second Brain claude.ai Project, never as a file in this repo. Materialized
at `docs/second-brain-remote-connector-design.md` and the CLAUDE.md reference
updated to link it directly.

---

## 2026-09-15 — Cowork → Code

Raspberry Pi rationale (why it's being considered, ARM64/AVX angle) was
discussed in the Second Brain claude.ai project but never made it into
nas-ops. Written up in full as deferred architecture decision **#7** in
`CLAUDE.md` — read that instead of duplicating here. This entry is just the
pointer.

Also flagged from that side: Emrys is scoping a 250GB SSD for the Pi to
mitigate I/O problems, in a separate Code session this file doesn't have
visibility into. Worth reconciling that session's findings back into
decision #7 once the SSD approach is settled.
