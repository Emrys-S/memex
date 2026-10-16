# newsletter-watcher: registry (2026-10-16)

The watcher itself (`newsletter_to_obsidian.py`) runs on the NAS at `/volume1/docker/newsletter-watcher/`: a Mailgun
inbound-route webhook (`*@ingest.<domain>`) that writes one note per newsletter into the vault's `Resources/Newsletters`.
This folder keeps the pure, dependency-free registry module and its tests.

**Problem**: newsletters arrive as *forwards*, so the mail's sender is whoever forwarded it. Nothing recorded who the
publishers are, which platform sends them, or where their subscription links are, so there was no way to move them to
their own dedicated address (`<publication>@ingest.<domain>`, no forwarding needed).

**`newsletter_registry.py`**
- `analyse()` recovers the original sender from a forwarded header block (Gmail, Apple Mail, Outlook; plain or HTML-only),
  the unsubscribe / manage-preferences links (anchors, plain-text lines, `List-Unsubscribe`), and the sending platform.
  A forward whose header block cannot be parsed is *not* guessed at.
- `observe()` / `render_registry()` keep a registry (`/data/registry.json` on the NAS) and render a note,
  `Review/Newsletter registry.md`: publication, sender, platform, forwarded/direct counts, the proposed dedicated address,
  and how easily it can be moved (simple signup form vs tied to an account).
- Subscription links carry a per-subscriber token, so the note shows link **hosts only**; full links stay in the JSON.

**In the watcher**: new notes get `publication` and `forwarded_by` in the frontmatter and `sender` is the publisher, not
the forwarder; the link filter uses the publisher's domain. Registry updates are best effort and never block a note.

**Tests**: `python3 -m unittest test_newsletter_registry` (12 tests, fictional publishers).
Backups on the NAS: `newsletter_to_obsidian.py.bak-20261016-pre-registry`, `Dockerfile.bak-...`, `docker-compose.yml.bak-...`.
