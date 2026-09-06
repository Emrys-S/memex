#!/usr/bin/env python3
"""
zotero_indexer.py
------------------
Polls a Zotero library (personal + all group libraries the account belongs
to) for changes via version-based sync, extracts full text from attached
PDFs, embeds title/abstract plus body-text chunks locally via
sentence-transformers, and stores them in Qdrant for semantic search.

No paid API is used here -- embedding runs entirely on CPU. The only place
this project spends money on an LLM for Zotero is zotero_query.py's optional
answer synthesis, at query time, not during indexing.

Incremental sync: Zotero's Web API has no push/webhook mechanism for
personal or group libraries (confirmed against the official sync docs, which
list WebSocket streaming as an unfinished TODO even in Zotero's own client)
-- polling with the `since=<version>` parameter is the correct, current
mechanism, not a fallback. Each library's last-synced version is tracked in
STATE_FILE; deletions are picked up via the /deleted endpoint so removed
Zotero items don't linger in the index.

Both personal and group libraries are indexed (confirmed scope, 2026-08-02)
-- group membership is discovered automatically via the account's own
/groups listing, not hardcoded.

Environment variables:
  ZOTERO_API_KEY    Read-only Zotero API key (zotero.org/settings/keys)
  ZOTERO_USER_ID    Your numeric Zotero user ID (same settings page)
  POLL_INTERVAL     Seconds between polls (default 43200 = 12 hours)
  QDRANT_HOST       Qdrant service hostname (default "qdrant")
  QDRANT_PORT       Qdrant REST port (default 6333)
  STATE_FILE        Path to the local per-library version cursor file
  CHUNK_SIZE        Characters per body-text chunk (default 1000)
  CHUNK_OVERLAP     Character overlap between consecutive chunks (default 150)

Not yet live-tested against a real library as of this build -- pyzotero's
exact method behavior (especially .deleted() and version tracking) is
implemented against its documented API, not verified live, since no API key
was available at build time. Watch the first real run's logs closely.
"""

import os
import re
import time
import json
import logging
import uuid
from pathlib import Path

import fitz  # pymupdf
from pyzotero import zotero
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

# ── Config ─────────────────────────────────────

ZOTERO_API_KEY = os.environ.get("ZOTERO_API_KEY", "")
ZOTERO_USER_ID = os.environ.get("ZOTERO_USER_ID", "")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "43200"))
QDRANT_HOST = os.environ.get("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "/data/state.json"))
# Lightweight, append-only log of successfully-indexed items, read by
# ingest_digest.py as its Zotero "source" -- the digest never talks to the
# Zotero API directly, it just reads this file, same pattern as how it reads
# Boox/Bluesky's own output .md files. Kept separate from STATE_FILE (the
# real sync cursor) so a digest-side read can never affect indexing.
RECENT_ITEMS_FILE = Path(os.environ.get("RECENT_ITEMS_FILE", "/data/recent_items.jsonl"))
RECENT_ITEMS_MAX_LINES = 2000  # trimmed on write so this can't grow unbounded
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "150"))
COLLECTION_NAME = "zotero_library"
VECTOR_SIZE = 384  # all-MiniLM-L6-v2 output dimension

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# httpx/httpcore log every request at INFO by default, including full URLs --
# for Zotero's PDF downloads that means presigned S3 URLs with embedded
# temporary tokens streaming through the logs on every single attachment,
# across a 4,217-item library. Quieting these to WARNING leaves the
# application's own log.info() calls as the only INFO-level output.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ── Embedding + Qdrant setup ────────────────────

model = None  # loaded lazily in main() so --help / import doesn't pay the cost


def get_model() -> SentenceTransformer:
    global model
    if model is None:
        log.info("Loading embedding model (all-MiniLM-L6-v2)...")
        model = SentenceTransformer("all-MiniLM-L6-v2")
    return model


def ensure_collection(client: QdrantClient):
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME not in collections:
        log.info(f"Creating Qdrant collection '{COLLECTION_NAME}'")
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=qmodels.VectorParams(size=VECTOR_SIZE, distance=qmodels.Distance.COSINE),
        )


# ── State ──────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"libraries": {}}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Zotero helpers ──────────────────────────────

def discover_libraries(personal_zot: zotero.Zotero) -> list:
    """Returns [{"id": ..., "type": "user"|"group", "name": ...}, ...] for
    the personal library plus every group the account belongs to."""
    libraries = [{"id": ZOTERO_USER_ID, "type": "user", "name": "Personal Library"}]
    try:
        for group in personal_zot.groups():
            libraries.append({
                "id": str(group["id"]),
                "type": "group",
                "name": group.get("data", {}).get("name", f"Group {group['id']}"),
            })
    except Exception as e:
        log.warning(f"Could not list group libraries: {e}")
    return libraries


def client_for(library: dict) -> zotero.Zotero:
    return zotero.Zotero(library["id"], library["type"], ZOTERO_API_KEY)


def get_collection_map(zot: zotero.Zotero) -> dict:
    """{collection_key: collection_name} for every collection in this library.
    Fetched once per library per sync run (collections rarely change mid-run),
    not per item -- avoids one extra API call per item.
    """
    try:
        return {c["data"]["key"]: c["data"]["name"] for c in zot.collections()}
    except Exception as e:
        log.warning(f"  Could not fetch collections: {e}")
        return {}


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list:
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def extract_pdf_text(zot: zotero.Zotero, attachment_key: str) -> str:
    try:
        pdf_bytes = zot.file(attachment_key)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        return text
    except Exception as e:
        log.warning(f"    Could not extract PDF text from {attachment_key}: {e}")
        return ""


def extract_annotations(zot: zotero.Zotero, attachment_key: str) -> list:
    """PDF highlights/comments live one level deeper than the attachment
    itself in Zotero's data model (item -> attachment -> annotation) --
    extract_pdf_text() above only reaches the attachment's own raw text, so
    without this, highlighting/commenting in Zotero's PDF reader is
    invisible to search even though it's exactly the kind of "what did I
    think was important" signal a second brain should capture. Image/ink
    annotations have no text content to embed and are skipped. Returns a
    list of text strings, one per annotation worth indexing.
    """
    try:
        children = zot.children(attachment_key)
    except Exception as e:
        log.warning(f"    Could not fetch annotations for attachment {attachment_key}: {e}")
        return []

    texts = []
    for child in children:
        if not isinstance(child, dict):
            continue
        d = child.get("data", {})
        if d.get("itemType") != "annotation":
            continue
        atype = d.get("annotationType", "")
        highlighted = (d.get("annotationText") or "").strip()
        comment = (d.get("annotationComment") or "").strip()
        if atype in ("highlight", "underline"):
            parts = []
            if highlighted:
                parts.append(f"Highlighted: {highlighted}")
            if comment:
                parts.append(f"Comment: {comment}")
            if parts:
                texts.append("\n".join(parts))
        elif atype == "note" and comment:
            texts.append(f"Note: {comment}")
        # image/ink annotations carry no text to embed -- skipped
    return texts


def zotero_link(library: dict, item_key: str) -> str:
    if library["type"] == "user":
        return f"zotero://select/library/items/{item_key}"
    return f"zotero://select/groups/{library['id']}/items/{item_key}"


def log_recent_item(item_key: str, library: dict, title: str, creators: str, date: str, link: str):
    """Append one line for the digest to pick up later. Never allowed to
    break indexing itself -- a logging failure here is a shrug, not an
    error, since the item is already safely indexed in Qdrant by this point."""
    try:
        import datetime
        record = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "item_key": item_key,
            "library_name": library["name"],
            "title": title,
            "creators": creators,
            "date": date,
            "zotero_link": link,
        }
        RECENT_ITEMS_FILE.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        if RECENT_ITEMS_FILE.exists():
            lines = RECENT_ITEMS_FILE.read_text().splitlines()
        lines.append(json.dumps(record))
        lines = lines[-RECENT_ITEMS_MAX_LINES:]
        RECENT_ITEMS_FILE.write_text("\n".join(lines) + "\n")
    except Exception as e:
        log.warning(f"    Could not write to recent-items log: {e}")


# ── Indexing ────────────────────────────────────

def delete_item_points(client: QdrantClient, item_key: str):
    # wait=False -- diagnosed 2026-08-22: with wait=True (the default), a
    # commit on this collection's current size + this NAS's tight memory
    # regularly takes just over 60s, and the *server itself* (1.18.3)
    # legitimately returns status="wait_timeout" when its own internal wait
    # deadline passes -- a real, documented status, but one the installed
    # qdrant-client (1.16.1, the newest available for this Mac's Python 3.9)
    # doesn't recognize, so it raises a pydantic validation error and the
    # whole item gets logged as failed even though the write itself
    # succeeded server-side. wait=False sidesteps both the slowness and the
    # parsing crash: Qdrant still applies writes to a given collection in
    # submission order regardless of wait=True/False, so the delete-then-
    # upsert sequence within one item stays correctly ordered either way.
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[qmodels.FieldCondition(key="item_key", match=qmodels.MatchValue(value=item_key))]
            )
        ),
        wait=False,
    )


def index_item(client: QdrantClient, zot: zotero.Zotero, library: dict, item: dict, collection_map: dict):
    data = item.get("data", {})
    item_type = data.get("itemType", "")
    if item_type in ("attachment", "note"):
        return  # handled as children of their parent item, not standalone

    item_key = data["key"]
    title = data.get("title", "(untitled)")
    creators = "; ".join(
        f"{c.get('firstName', '')} {c.get('lastName', c.get('name', ''))}".strip()
        for c in data.get("creators", [])
    )
    abstract = data.get("abstractNote", "")
    tags = [t["tag"] for t in data.get("tags", [])]
    date = data.get("date", "")
    link = zotero_link(library, item_key)
    # Zotero's item API returns collection membership as a list of keys on
    # every top-level item by default -- resolve to names via the library's
    # collection map (fall back to the raw key if a name lookup misses,
    # e.g. the map went stale mid-run rather than silently dropping it).
    collection_keys = data.get("collections", [])
    collection_names = [collection_map.get(k, k) for k in collection_keys]

    log.info(f"  Indexing: {title[:70]}")
    delete_item_points(client, item_key)

    embed_model = get_model()
    points = []

    def make_point(text: str, chunk_type: str, chunk_index: int):
        vector = embed_model.encode(text, show_progress_bar=False).tolist()
        return qmodels.PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "item_key": item_key,
                "library_id": library["id"],
                "library_name": library["name"],
                "collection_keys": collection_keys,
                "collection_names": collection_names,
                "title": title,
                "creators": creators,
                "item_type": item_type,
                "date": date,
                "tags": tags,
                "zotero_link": link,
                "chunk_type": chunk_type,
                "chunk_index": chunk_index,
                "text": text,
            },
        )

    # Metadata chunk always included, even for items with no attached PDF
    # (web links, book entries, notes-only items) -- otherwise those would
    # be entirely invisible to search in full-text mode.
    metadata_text = f"{title}\n{creators}\n{abstract}".strip()
    if metadata_text:
        points.append(make_point(metadata_text, "metadata", 0))

    try:
        children = zot.children(item_key)
    except Exception as e:
        log.warning(f"    Could not fetch children for {item_key}: {e}")
        children = []

    for child in children:
        if not isinstance(child, dict):
            continue
        child_data = child.get("data", {})
        if child_data.get("itemType") == "attachment" and child_data.get("contentType") == "application/pdf":
            attachment_key = child_data["key"]
            text = extract_pdf_text(zot, attachment_key)
            for i, chunk in enumerate(chunk_text(text)):
                points.append(make_point(chunk, "body", i))
            for i, annot_text in enumerate(extract_annotations(zot, attachment_key)):
                points.append(make_point(annot_text, "annotation", i))
        elif child_data.get("itemType") == "note":
            note_text = strip_html(child_data.get("note", ""))
            for i, chunk in enumerate(chunk_text(note_text)):
                points.append(make_point(chunk, "note", i))

    if points:
        client.upsert(collection_name=COLLECTION_NAME, points=points, wait=False)  # see delete_item_points() for why
        log.info(f"    ✓ {len(points)} chunk(s) indexed")
        log_recent_item(item_key, library, title, creators, date, link)


def fetch_page_with_retry(zot: zotero.Zotero, since: int, start: int, attempts: int = 3):
    """A single page fetch can hit a transient timeout -- that's not the same
    thing as "we've reached the end of the library" and must never be treated
    as such. Confirmed live 2026-08-20: an earlier version of this function
    caught a mid-pagination read-timeout, logged it, and broke out of the
    loop as if the library were fully synced -- silently stopping a
    4,239-item library at 988 items with no error surfaced anywhere except
    a log line. Retries a few times with backoff before giving up; if it
    still fails, the exception propagates to sync_library's own try/except,
    which correctly marks the whole sync as failed (and, critically, does
    NOT advance the version cursor) rather than reporting false success.
    """
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return zot.top(since=since, start=start, limit=100)
        except Exception as e:
            last_exc = e
            if attempt < attempts:
                log.warning(f"  Page fetch at start={start} failed (attempt {attempt}/{attempts}): {e} -- retrying")
                time.sleep(5 * attempt)
    raise last_exc


def sync_library(client: QdrantClient, library: dict, state: dict):
    zot = client_for(library)
    lib_state = state["libraries"].setdefault(
        library["id"], {"version": 0, "type": library["type"], "name": library["name"]}
    )
    since = lib_state["version"]

    log.info(f"Syncing {library['name']} ({library['type']}) since version {since}")
    collection_map = get_collection_map(zot)

    try:
        # /items/top, not the flat /items endpoint -- /items mixes in every
        # attachment and note as its own entry (9,413 raw entries for a
        # library with 4,239 real top-level items, confirmed live
        # 2026-08-20), which index_item() then has to filter back down
        # client-side. /items/top returns only genuine top-level items
        # directly, roughly halving the pages needed and removing the
        # filtering ambiguity entirely -- num_items() (also /items/top)
        # becomes a direct, unambiguous verification target.
        #
        # Explicit start= offset pagination, not zot.follow(). follow()
        # relies on pyzotero's self.links state from the *most recent* API
        # call made through this zot client -- but index_item() below makes
        # its own calls through the same client (zot.children(), zot.file())
        # for every item in the page, which silently overwrites that state
        # before we get back here to paginate. start= is a stateless,
        # explicit offset with no such shared-state footgun.
        start = 0
        while True:
            items = fetch_page_with_retry(zot, since, start)
            for item in items:
                # Defensive: don't assume every entry is a well-formed item
                # dict before we've checked -- a malformed entry here
                # previously crashed the whole library's sync (the except
                # clause itself threw trying to call .get() on it), silently
                # aborting everything after that point for the rest of the
                # library. One bad item should cost one skipped item, not
                # the whole sync.
                if not isinstance(item, dict):
                    log.warning(f"  Skipping unexpected entry (not an item dict): {repr(item)[:100]}")
                    continue
                item_key = item.get("data", {}).get("key", "?")
                try:
                    index_item(client, zot, library, item, collection_map)
                except Exception as e:
                    log.error(f"  ✗ Failed to index item {item_key}: {e}")
            if len(items) < 100:
                break
            start += 100
    except Exception as e:
        log.error(f"  ✗ Failed to sync {library['name']}: {e}")
        return

    try:
        deleted = zot.deleted(since=since)
        for item_key in deleted.get("items", []):
            log.info(f"  Removing deleted item: {item_key}")
            delete_item_points(client, item_key)
    except Exception as e:
        log.warning(f"  Could not check deletions for {library['name']}: {e}")

    # last_modified_version is a *method* on pyzotero's client, not a plain
    # attribute -- getattr() without calling it just returns the bound
    # method object itself (the attribute technically exists, so the
    # fallback default never kicks in), silently storing a method object as
    # the version cursor instead of an integer. Confirmed live, 2026-08-02.
    try:
        new_version = zot.last_modified_version()
    except Exception as e:
        log.warning(f"  Could not read last_modified_version for {library['name']}: {e}")
        new_version = since
    lib_state["version"] = new_version
    lib_state["name"] = library["name"]


# ── Entry point ────────────────────────────────

def main():
    if not ZOTERO_API_KEY or not ZOTERO_USER_ID:
        raise SystemExit("Error: ZOTERO_API_KEY / ZOTERO_USER_ID not set.")

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    ensure_collection(client)
    state = load_state()

    log.info("─" * 55)
    log.info("Zotero → Qdrant indexer started")
    log.info(f"Poll     : every {POLL_INTERVAL}s")
    log.info(f"Chunking : {CHUNK_SIZE} chars, {CHUNK_OVERLAP} overlap")
    log.info("─" * 55)

    personal_zot = zotero.Zotero(ZOTERO_USER_ID, "user", ZOTERO_API_KEY)

    while True:
        try:
            libraries = discover_libraries(personal_zot)
            log.info(f"Found {len(libraries)} librar(y/ies): {[l['name'] for l in libraries]}")
            for library in libraries:
                sync_library(client, library, state)
            save_state(state)
        except Exception as e:
            log.error(f"✗ Cycle failed: {e}")
        log.info(f"Sleeping {POLL_INTERVAL}s until next poll...")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
