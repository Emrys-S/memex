# nas-ops — a self-hosted "second brain"

Infrastructure and pipeline code for a personal knowledge system that runs on a home NAS:
multi-source ingestion, a unified semantic search layer, and an MCP query server that lets an
LLM answer questions grounded in my own notes, reading, and research library — with citations
back to source.

This repo is the **code and architecture only**. The actual captured content (notes, reading
history, research library) lives in a private vault and is never part of this repository — see
[Privacy](#privacy) below.

## Why

I wanted a system where everything I read, write, and capture — handwritten notes, research
papers, things I save or engage with online — ends up in one place I actually control, is
searchable by meaning (not just keyword), and can be queried by an LLM with real citations, not
hallucinated ones. Off-the-shelf tools either meant giving up control of the data, or didn't
integrate across sources. So: plain files where possible, self-hosted where it matters, and glue
code where nothing existing fit.

It started, somewhat less loftily, because the NAS was already running and making concerning
noises — which turned into "well, while I'm debugging the disks, what else could this box do."

## Architecture

```
Sources ──▶ Ingestion pipelines ──▶ raw/ (plain markdown)
                                          │
                                          ▼
                              Semantic index (Qdrant, local embeddings)
                                          │
                                          ▼
                              MCP query server ──▶ LLM clients (citations back to source)
```

**Ingestion pipelines** (each a small, independent service):
- **Handwritten notes** — OCR via a vision-capable LLM, with symbol-based routing (a tag can
  route a note to a "person" file, a task tracker, a project idea list, etc.) and idempotent
  reprocessing (edits are hash-checked so a re-run can't silently clobber a hand-edited note).
- **Social engagement** (two very different postures, deliberately):
  - One platform has a real, sanctioned API — that pipeline runs unattended, on a schedule.
  - Another has no API for personal engagement data at all. Scraping it would violate the
    platform's terms and look exactly like the automated access those terms exist to prevent —
    so that pipeline is architected to *only* run through a human-driven browser session, kicked
    off in the moment, and is explicitly designed to refuse being wired into a schedule even if
    asked. Different platforms, different postures, on purpose.
- **Reference library** — full-text indexing of a large personal/shared research library (tens
  of thousands of items across dozens of collections), incremental via the source's own
  version-cursor API, with per-collection state tracking so a sync interruption never means
  starting over.

**Semantic index** — one shared vector collection across all sources (`all-MiniLM-L6-v2`, run
locally — no embedding API cost), tagged by source so queries can be scoped ("only my reading
list" vs. "everything"). Idempotency is structural: every run reconciles the current file list
against stored per-file state (mtime, content hash, exact point IDs written last time) rather than
just appending, so reprocessing is safe by design instead of patched reactively.

**Query layer** — an MCP server exposing search and a "map an outline against the evidence"
composite tool, so a writing draft can be checked against my own material in one call instead of
several manual searches.

**Secrets** — SOPS + age. No secrets daemon, nothing new to run or secure — existing per-pipeline
`.env` files are encrypted in place, git-trackable as ciphertext, decrypted to memory only at
deploy time. Chosen over a dedicated secrets-manager service specifically to avoid adding another
long-running service to an already resource-constrained box.

## A few real bugs, for flavor

Nothing here is theoretical — a few things that actually broke and got fixed along the way:

- A vector-DB client library renamed its own search method in a minor version bump, silently
  breaking the query script for weeks before anyone actually exercised it end-to-end (a good
  reminder that "the ingestion succeeded" and "the query interface works" are different claims).
- A large backfill job ported from Linux to macOS hit a `fork()`-after-networking crash specific
  to macOS's process model — the failure mode was silent and near-total (burned through 11,000
  remaining items in 17 minutes, falsely marking the run complete) until caught by direct
  verification against the actual data store rather than trusting the job's own "done" log line.
- A vault-sync tool's underlying sync engine picked up a stricter validation rule in a routine
  version bump that broke compatibility with older bundled mobile clients — fixed by pinning the
  version and removing that service from auto-update, since it's exactly the kind of regression
  class that benefits from *not* always being on the latest.

## Stack

Python · Docker / docker-compose · Qdrant (vector search) · SOPS + age (secrets) · Syncthing
(sync) · MCP (query interface) · a Synology NAS running the whole thing on modest hardware.

## Privacy

This repository contains pipeline code, infrastructure configuration, and architecture — nothing
else. It does not and will not contain: captured notes, reading/engagement history, the research
library's contents, credentials, or any personally identifying information beyond what's in this
README. Real secrets are encrypted in place (SOPS + age) and only ever decrypted to memory at
deploy time; nothing sensitive is committed even in encrypted form to this particular repo.
