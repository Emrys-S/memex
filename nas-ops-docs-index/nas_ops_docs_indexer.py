#!/usr/bin/env python3
"""
nas_ops_docs_indexer.py
------------------------
Closes a real gap in the second-brain retrieval layer: vault_indexer.py
(on the NAS) covers the whole Obsidian vault -- Boox, Bluesky, LinkedIn,
People, Projects -- but this repo's own CLAUDE.md and TASKS.md, which carry
most of the actual working history and decisions behind the second-brain
project itself, were never reachable through query.py at all. They live
only on this Mac, outside the Obsidian vault, with no sync mechanism.

Rather than invent a sync path (a new Syncthing folder for two files is
more infrastructure than the problem needs), this indexes them directly
from the Mac to the NAS's existing Qdrant, the same way the Zotero Mac
backfill scripts talk to it over Tailscale -- consistent with this
project's repeated preference for the option that adds the least new
infrastructure.

Writes into the SAME "second_brain" collection vault_indexer.py already
uses (not a new collection), tagged source="nas-ops", using the identical
schema and chunking approach (split on top-level "## " headings, then
character-chunk anything still too long) so query.py's existing rendering
and --source filtering work on this data with no changes on that end.
Point IDs are namespaced "nas-ops::<path>::<chunk-index>" (vault_indexer.py
uses "vault::...") so there's no collision risk between the two indexers
sharing one collection.

Idempotent by design, same reconciliation pattern as vault_indexer.py and
the Zotero indexers: state (mtime, content hash, the exact point IDs
written) is tracked per file in a local JSON file, so a changed file has
its old points deleted before new ones are written, and an untouched file
is skipped via a cheap mtime check.

This is a one-shot script, not a persistent poller -- these two files only
change during active work sessions (like this one), not on a schedule, so
there's no real benefit to a background loop the way there is for
Boox/Bluesky/Zotero's actual ingestion pipelines. Re-run it by hand after
a session that meaningfully edits CLAUDE.md or TASKS.md:
  python3 nas_ops_docs_indexer.py

Environment variables:
  QDRANT_HOST   default schoerro.tail3f930f.ts.net (the NAS over Tailscale)
  QDRANT_PORT   default 6333
"""

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = Path(__file__).resolve().parent / "state.json"
QDRANT_HOST = os.environ.get("QDRANT_HOST", "schoerro.tail3f930f.ts.net")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
COLLECTION_NAME = "second_brain"
VECTOR_SIZE = 384  # all-MiniLM-L6-v2

MAX_CHUNK_CHARS = 1500
CHUNK_OVERLAP = 200

# Repo-root markdown files to index, relative to REPO_ROOT. Extend this list
# if another top-level doc becomes worth making searchable -- everything
# else about the pipeline (chunking, state tracking, dedup) generalizes for
# free, this is the only line that would need to change.
SOURCE_FILES = ["CLAUDE.md", "TASKS.md"]


def split_sections(body: str):
    """Split on top-level '## ' headings. Same logic as vault_indexer.py's
    split_sections() -- kept in lockstep deliberately so chunk shapes stay
    consistent across every source in the shared collection."""
    pieces = re.split(r"(?m)^##\s+(.+)$", body)
    if len(pieces) == 1:
        return [(None, body.strip())] if body.strip() else []
    sections = []
    if pieces[0].strip():
        sections.append((None, pieces[0].strip()))
    for i in range(1, len(pieces), 2):
        heading = pieces[i].strip()
        text = pieces[i + 1].strip() if i + 1 < len(pieces) else ""
        if text:
            sections.append((heading, text))
    return sections


def chunk_text(text: str):
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + MAX_CHUNK_CHARS
        chunks.append(text[start:end])
        start = end - CHUNK_OVERLAP
    return chunks


def extract_title(rel_path: str, text: str) -> str:
    match = re.search(r"(?m)^#\s+(.+)$", text)
    if match:
        return match.group(1).strip()
    return Path(rel_path).stem


def build_points(rel_path: str, text: str):
    title = extract_title(rel_path, text)
    points = []
    chunk_index = 0
    for heading, section_text in split_sections(text):
        for chunk in chunk_text(section_text):
            if not chunk.strip():
                continue
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"nas-ops::{rel_path}::{chunk_index}"))
            points.append({
                "id": point_id,
                "payload": {
                    "path": rel_path,
                    "source": "nas-ops",
                    "title": title,
                    "section_heading": heading,
                    "date": None,
                    "obsidian_link": None,  # not an Obsidian note
                    "external_link": f"nas-ops/{rel_path}",  # locator: repo-relative path
                    "text": chunk,
                },
            })
            chunk_index += 1
    return points


def ensure_collection(client: QdrantClient):
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME not in existing:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def main():
    print(f"=== nas_ops_docs_indexer starting (repo={REPO_ROOT}, collection={COLLECTION_NAME}) ===")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=60)
    ensure_collection(client)

    state = load_state()
    changed = 0
    unchanged = 0
    errors = 0

    for rel_path in SOURCE_FILES:
        full_path = REPO_ROOT / rel_path
        try:
            if not full_path.exists():
                print(f"  ! {rel_path} not found, skipping")
                continue

            stat = full_path.stat()
            mtime = stat.st_mtime
            prior = state.get(rel_path)
            if prior and prior.get("mtime") == mtime:
                unchanged += 1
                continue

            text = full_path.read_text(errors="replace")
            content_hash = hashlib.md5(text.encode()).hexdigest()
            if prior and prior.get("hash") == content_hash:
                state[rel_path]["mtime"] = mtime
                unchanged += 1
                continue

            if prior and prior.get("point_ids"):
                client.delete(collection_name=COLLECTION_NAME, points_selector=prior["point_ids"])

            points = build_points(rel_path, text)
            if points:
                vectors = model.encode([p["payload"]["text"] for p in points], show_progress_bar=False)
                client.upsert(
                    collection_name=COLLECTION_NAME,
                    points=[
                        PointStruct(id=p["id"], vector=vectors[i].tolist(), payload=p["payload"])
                        for i, p in enumerate(points)
                    ],
                )

            state[rel_path] = {
                "mtime": mtime,
                "hash": content_hash,
                "point_ids": [p["id"] for p in points],
            }
            changed += 1
            print(f"  ✓ indexed {rel_path} ({len(points)} chunk(s))")

        except Exception as e:
            print(f"  ! failed on {rel_path}: {e}")
            errors += 1
            continue

    save_state(state)
    print(f"Done: {changed} changed, {unchanged} unchanged, {errors} errors.")


if __name__ == "__main__":
    main()
