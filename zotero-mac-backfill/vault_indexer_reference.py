** WARNING: connection is not using a post-quantum key exchange algorithm.
** This session may be vulnerable to "store now, decrypt later" attacks.
** The server may need to be upgraded. See https://openssh.com/pq.html
#!/usr/bin/env python3
"""
vault_indexer.py
-----------------
Generalizes the Zotero semantic-search pattern (zotero_indexer.py /
zotero_query.py) to the whole NAS-canonical Obsidian vault: Boox notes,
Bluesky/LinkedIn captures, People notes, Project notes, and the user's own
general writing all live in one vault since the Stage 2 pipeline retarget
(2026-08-06) -- so one indexer watching the vault covers every source, with
no per-pipeline special-casing. LinkedIn and hand-written notes currently
only reach the NAS-canonical vault after the Stage 3 sync cutover; until
then this indexer simply won't see them yet, which is expected, not a bug.

Embeds into its own Qdrant collection ("second_brain"), served by the same
Qdrant instance zotero_indexer.py already runs (zotero-qdrant, on the
zotero-search_default network) -- deliberately not a second database, this
NAS is resource-tight. Zotero's own 23k-point "zotero_library" collection
is left untouched; query.py searches both and merges results rather than
migrating Zotero's already-proven data into this one.

Idempotency is structural, not append-only: every run reconciles the full
current file list against stored state (path -> mtime, content hash, and
the exact Qdrant point IDs written for it last time). A changed file has
its old points deleted before new ones are written; a deleted file has its
points removed entirely. This directly addresses the "idempotency patched
per-pipeline, not solved structurally" gap flagged in the 2026-08-06
architecture review.

Chunking: markdown files are split on top-level "## " headings, if present,
before any character-based chunking -- most notes are single-topic (one
chunk), but the multi-entry activity-log files (Bluesky/LinkedIn "Activity"
folders, one growing file per week/period with many dated entries) get one
chunk per entry instead of one blurry chunk per file. This generalizes
across every source rather than special-casing activity logs specifically,
since any future note type with the same "one file, many headed sections"
shape gets the same fine-grained treatment for free.

Environment variables:
  VAULT_ROOT      default /vault (read-only mount)
  STATE_FILE      default /data/state.json
  QDRANT_HOST     default qdrant
  QDRANT_PORT     default 6333
  POLL_INTERVAL   default 21600 (6h -- coarser than Bluesky's 4h poll,
                   since most of what lands here has already been through
                   its own pipeline; this is reconciliation, not first capture)
"""

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

import yaml
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

VAULT_ROOT = Path(os.environ.get("VAULT_ROOT", "/vault"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "/data/state.json"))
QDRANT_HOST = os.environ.get("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "21600"))
COLLECTION_NAME = "second_brain"
VECTOR_SIZE = 384  # all-MiniLM-L6-v2

MAX_CHUNK_CHARS = 1500
CHUNK_OVERLAP = 200

# Longest-prefix-first match against the file's path relative to VAULT_ROOT.
# None means "exclude entirely" -- currently just the credentials folder
# (see CLAUDE.md's Personal/Networking security flag -- nothing from there
# gets embedded until the user has moved those values out) and a couple of
# vestigial non-content folders.
SOURCE_RULES = [
    ("Personal/Networking", None),
    ("Google Drive", None),
    ("Inbox/Boox notes", "boox"),
    ("Resources/Boox Notes", "boox"),
    ("Resources/Bluesky", "bluesky"),
    ("Resources/LinkedIn Posts", "linkedin"),
    ("Resources/LinkedIn references", "linkedin"),
    ("Resources/People", "people"),
    ("Resources/Meeting Notes", "meeting-notes"),
    ("Resources/Ingest Digest", None),  # generated summaries of other sources -- would just duplicate them
    ("Personal", "personal"),
    ("Projects", "projects"),
    ("Inbox", "inbox"),
]
SOURCE_RULES.sort(key=lambda r: len(r[0]), reverse=True)


def source_for_path(rel_path: str):
    for prefix, label in SOURCE_RULES:
        if rel_path == prefix or rel_path.startswith(prefix + "/"):
            return label
    return "other"


def list_markdown_files():
    for path in VAULT_ROOT.rglob("*.md"):
        rel = str(path.relative_to(VAULT_ROOT))
        if any(part.startswith(".") for part in Path(rel).parts):
            continue
        label = source_for_path(rel)
        if label is None:
            continue
        yield rel, label


def parse_frontmatter(text: str):
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, parts[2]


def split_sections(body: str):
    """Split on top-level '## ' headings. Returns [(heading_or_None, text), ...]."""
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


def external_link_from_frontmatter(fm: dict):
    for key in ("post_url", "link", "url", "zotero_link"):
        if fm.get(key):
            return fm[key]
    return None


def obsidian_link(rel_path: str, heading: str = None):
    note_path = rel_path[:-3] if rel_path.endswith(".md") else rel_path
    return f"[[{note_path}#{heading}]]" if heading else f"[[{note_path}]]"


def build_points(rel_path: str, source: str, raw_text: str):
    fm, body = parse_frontmatter(raw_text)
    title = fm.get("title") or Path(rel_path).stem
    ext_link = external_link_from_frontmatter(fm)
    date = fm.get("date") or fm.get("week")

    points = []
    chunk_index = 0
    for heading, section_text in split_sections(body):
        for chunk in chunk_text(section_text):
            if not chunk.strip():
                continue
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"vault::{rel_path}::{chunk_index}"))
            points.append({
                "id": point_id,
                "payload": {
                    "path": rel_path,
                    "source": source,
                    "title": title,
                    "section_heading": heading,
                    "date": date,
                    "obsidian_link": obsidian_link(rel_path, heading),
                    "external_link": ext_link,
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
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


SAVE_EVERY = 25  # persist state this often so a mid-run error doesn't discard already-indexed work


def run_once(model, client, state):
    seen_paths = set()
    changed = 0
    unchanged = 0
    errors = 0

    for rel_path, source in list_markdown_files():
        seen_paths.add(rel_path)
        try:
            full_path = VAULT_ROOT / rel_path
            try:
                stat = full_path.stat()
            except FileNotFoundError:
                continue
            mtime = stat.st_mtime

            prior = state.get(rel_path)
            if prior and prior.get("mtime") == mtime:
                unchanged += 1
                continue

            try:
                raw_text = full_path.read_text(errors="replace")
            except OSError as e:
                print(f"  ! could not read {rel_path}: {e}")
                errors += 1
                continue

            content_hash = hashlib.md5(raw_text.encode()).hexdigest()
            if prior and prior.get("hash") == content_hash:
                # Touched but not actually edited -- just refresh the stored mtime.
                state[rel_path]["mtime"] = mtime
                unchanged += 1
                continue

            if prior and prior.get("point_ids"):
                client.delete(collection_name=COLLECTION_NAME, points_selector=prior["point_ids"])

            points = build_points(rel_path, source, raw_text)
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
                "source": source,
            }
            changed += 1
            print(f"  ✓ indexed {rel_path} ({source}, {len(points)} chunk(s))")

        except Exception as e:
            # A transient error on one file (e.g. a momentary Qdrant hiccup) must not
            # discard every file already indexed this run -- log it, keep going, and
            # let the next run retry this specific file (its mtime/hash won't have
            # been recorded, so it's naturally picked up again).
            print(f"  ! failed on {rel_path}: {e}")
            errors += 1
            continue

        if changed and changed % SAVE_EVERY == 0:
            save_state(state)

    # Reconcile deletions: anything in state but no longer on disk (or now excluded).
    removed = 0
    for rel_path in list(state.keys()):
        if rel_path not in seen_paths:
            try:
                point_ids = state[rel_path].get("point_ids") or []
                if point_ids:
                    client.delete(collection_name=COLLECTION_NAME, points_selector=point_ids)
                del state[rel_path]
                removed += 1
                print(f"  ✗ removed {rel_path} (no longer present)")
            except Exception as e:
                print(f"  ! failed removing {rel_path}: {e}")
                errors += 1

    print(f"Reconciled: {changed} changed, {unchanged} unchanged, {removed} removed, {errors} errors.")
    return state


def main():
    print(f"=== vault_indexer starting (vault={VAULT_ROOT}, collection={COLLECTION_NAME}) ===")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    ensure_collection(client)

    while True:
        state = load_state()
        try:
            state = run_once(model, client, state)
        except Exception as e:
            # run_once already isolates per-file errors; this only catches something
            # outside that loop (e.g. listing the vault itself). Save whatever partial
            # progress exists rather than losing it.
            print(f"! run failed: {e}")
        save_state(state)
        print(f"Sleeping {POLL_INTERVAL}s...")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
