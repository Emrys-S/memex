#!/usr/bin/env python3
"""
full_backfill.py
-----------------
One-time full re-index across all Zotero libraries, bypassing the normal
`since=<version>` incremental cursor. Reuses zotero_indexer.py's own
index_item()/get_collection_map()/etc. directly (imported from /app, not
reimplemented) so this exercises the exact same tested logic as the live
indexer -- not a parallel reconstruction of it. That also means the
wait=False fix in zotero_indexer.py's delete_item_points()/index_item()
(diagnosed 2026-08-22: Qdrant's own internal wait=true commit timeout was
returning a legitimate 'wait_timeout' status the installed qdrant-client
couldn't parse, turning real successes into false "failed" log lines) is
inherited here automatically -- nothing to change in this file for that.

Why this exists (diagnosed 2026-08-17): live Qdrant state showed only 447
distinct items indexed across all 18 libraries, despite Personal Library
alone holding 4,235 real items. This script does the full re-index properly,
and verifies its own completion against live Qdrant counts per library
instead of trusting a summary log line.

index_item() already deletes-then-reinserts a given item's points before
writing, so this is safe to run over already-indexed items too -- nothing
gets duplicated.

Resume checkpoint (added 2026-08-22, mirroring the same fix built for the
Mac's parallel run after it lost progress to an unrelated reboot): writes
checkpoint.json after every completed page with the current library and its
next start= offset, plus each already-fully-completed library's own result.
On launch, reads it back, skips libraries already marked complete, and
resumes the in-progress one from its last offset instead of 0.

SKIP_LIBRARIES env var (comma-separated library names) excludes libraries
entirely -- added 2026-08-22 so this run can skip Personal Library, which
the Mac is running its own corrected pass over in parallel; re-running it
here too would just be duplicate, wasted work.

Run detached so it survives the SSH session / user disconnecting:
  docker exec -d zotero-indexer python3 /data/full_backfill.py
Progress:
  docker exec zotero-indexer tail -f /data/full_backfill.log
"""

import os
import sys
import json
import time
import logging

# PyTorch's BLAS/OpenMP thread pool auto-detects core count via the
# container's reported CPU count, which on this NAS's cgroup setup can
# massively overshoot the real 2 physical cores -- observed live 2026-08-17:
# a plain import of sentence-transformers (via zotero_indexer) burned 16+
# CPU-minutes with zero progress instead of the usual ~90s. Pinning thread
# counts before the heavy import is the standard fix for this class of
# container/BLAS thread-explosion hang.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

print("full_backfill.py starting, about to import zotero_indexer (torch import, can take a while)...", flush=True)

sys.path.insert(0, "/app")
from zotero_indexer import (  # noqa: E402
    ZOTERO_USER_ID,
    ZOTERO_API_KEY,
    QDRANT_HOST,
    QDRANT_PORT,
    ensure_collection,
    discover_libraries,
    client_for,
    get_collection_map,
    index_item,
    load_state,
    save_state,
)
from qdrant_client import QdrantClient
from pyzotero import zotero

print("zotero_indexer imported OK (torch import complete), setting up logging...", flush=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("/data/full_backfill.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

CHECKPOINT_FILE = "/data/full_backfill_checkpoint.json"
SKIP_LIBRARIES = {s.strip() for s in os.environ.get("SKIP_LIBRARIES", "").split(",") if s.strip()}


def load_checkpoint() -> dict:
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE) as f:
                data = json.load(f)
            log.info(f"Loaded checkpoint: {len(data.get('completed', {}))} librar(y/ies) already complete, "
                      f"in-progress={data.get('current_library')} at start={data.get('current_start', 0)}")
            return data
        except Exception as e:
            log.warning(f"Could not read checkpoint, starting fresh: {e}")
    return {"completed": {}, "current_library": None, "current_start": 0}


def save_checkpoint(checkpoint: dict):
    try:
        with open(CHECKPOINT_FILE, "w") as f:
            json.dump(checkpoint, f, indent=2)
    except Exception as e:
        log.warning(f"Could not write checkpoint: {e}")


def fetch_page_with_retry(zot: zotero.Zotero, start: int, attempts: int = 3):
    """A single page fetch can hit a transient timeout -- that must never be
    silently treated as "library complete." Confirmed live 2026-08-20: the
    first version of this function caught a mid-pagination read-timeout,
    logged it, and broke as if done -- silently stopping a 4,239-item
    library at 988. Retries with backoff; if still failing, raises so the
    caller can mark this library's re-index as genuinely incomplete rather
    than reporting false success.
    """
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return zot.top(start=start, limit=100)
        except Exception as e:
            last_exc = e
            if attempt < attempts:
                log.warning(f"  Page fetch at start={start} failed (attempt {attempt}/{attempts}): {e} -- retrying")
                time.sleep(5 * attempt)
    raise last_exc


def backfill_library(client: QdrantClient, library: dict, resume_start: int, checkpoint: dict) -> dict:
    zot = client_for(library)
    zot.client.timeout = 60
    collection_map = get_collection_map(zot)

    log.info(f"=== {library['name']} ({library['type']}) -- full re-index, resuming from start={resume_start} ===")

    attempted = 0
    succeeded = 0
    failed = 0
    complete = False

    # /items/top, not the flat /items endpoint -- /items mixes in every
    # attachment and note as its own entry, which then has to be filtered
    # back out client-side; /items/top returns only genuine top-level items
    # directly, roughly halving the pages needed. Also: explicit start=
    # offset pagination, not zot.follow() -- see zotero_indexer.py's
    # sync_library() for the full explanation of why follow() silently
    # truncates here (pyzotero client state gets overwritten mid-page by
    # index_item()'s own zot.children()/zot.file() calls).
    start = resume_start
    while True:
        try:
            items = fetch_page_with_retry(zot, start)
        except Exception as e:
            log.error(f"  Giving up on {library['name']} at start={start} after retries: {e}")
            break

        for item in items:
            if not isinstance(item, dict):
                continue
            attempted += 1
            item_key = item.get("data", {}).get("key", "?")
            try:
                index_item(client, zot, library, item, collection_map)
                succeeded += 1
            except Exception as e:
                failed += 1
                log.error(f"  Failed to index item {item_key}: {e}")
            if attempted % 50 == 0:
                log.info(f"  ... {attempted} attempted so far ({succeeded} ok, {failed} failed)")

        if len(items) < 100:
            complete = True
            break

        start += 100
        checkpoint["current_library"] = library["name"]
        checkpoint["current_start"] = start
        save_checkpoint(checkpoint)

    status = "complete" if complete else "INCOMPLETE (gave up after retries)"
    log.info(f"  {library['name']}: {attempted} attempted, {succeeded} succeeded, {failed} failed -- {status}")
    return {"attempted": attempted, "succeeded": succeeded, "failed": failed, "complete": complete}


def verify_library(client: QdrantClient, library: dict) -> int:
    """Count distinct item_key values actually present in Qdrant for this
    library right now -- the check the original run never did."""
    offset = None
    keys = set()
    while True:
        points, offset = client.scroll(
            collection_name="zotero_library",
            scroll_filter={"must": [{"key": "library_id", "match": {"value": str(library["id"])}}]},
            limit=1000,
            offset=offset,
            with_payload=["item_key"],
            with_vectors=False,
        )
        for p in points:
            k = p.payload.get("item_key")
            if k:
                keys.add(k)
        if offset is None:
            break
    return len(keys)


def main():
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=60)
    ensure_collection(client)

    personal_zot = zotero.Zotero(ZOTERO_USER_ID, "user", ZOTERO_API_KEY)
    personal_zot.client.timeout = 60
    libraries = discover_libraries(personal_zot)

    if SKIP_LIBRARIES:
        log.info(f"Skipping libraries per SKIP_LIBRARIES: {sorted(SKIP_LIBRARIES)}")
        libraries = [lib for lib in libraries if lib["name"] not in SKIP_LIBRARIES]

    checkpoint = load_checkpoint()

    log.info("#" * 60)
    log.info(f"FULL BACKFILL starting across {len(libraries)} libraries")
    log.info("#" * 60)

    results = dict(checkpoint.get("completed", {}))
    t0 = time.time()
    for library in libraries:
        if library["name"] in results:
            log.info(f"=== {library['name']} -- already complete per checkpoint, skipping ===")
            continue
        resume_start = checkpoint["current_start"] if checkpoint.get("current_library") == library["name"] else 0
        r = backfill_library(client, library, resume_start, checkpoint)
        results[library["name"]] = r
        checkpoint["completed"][library["name"]] = r
        checkpoint["current_library"] = None
        checkpoint["current_start"] = 0
        save_checkpoint(checkpoint)

    state = load_state()
    for library in libraries:
        zot = client_for(library)
        try:
            new_version = zot.last_modified_version()
        except Exception as e:
            log.warning(f"  Could not read final version for {library['name']}: {e}")
            continue
        lib_state = state["libraries"].setdefault(
            library["id"], {"version": 0, "type": library["type"], "name": library["name"]}
        )
        lib_state["version"] = new_version
        lib_state["name"] = library["name"]
    save_state(state)
    log.info("State cursors updated to current version for all libraries -- normal incremental sync resumes cleanly from here.")

    log.info("#" * 60)
    log.info("VERIFICATION -- live Qdrant counts vs. Zotero's own num_items() per library")
    log.info("#" * 60)
    for library in libraries:
        r = results.get(library["name"], {})
        actual = verify_library(client, library)
        try:
            zot = client_for(library)
            target = zot.num_items()
        except Exception as e:
            target = None
            log.warning(f"  Could not read num_items() for {library['name']}: {e}")
        run_status = "complete" if r.get("complete") else "INCOMPLETE"
        match = "OK" if target is not None and actual >= target else "SHORT"
        log.info(
            f"  {library['name']:35s} target={str(target):>6}  verified_in_qdrant={actual:>6}  "
            f"[{match}]  run={run_status}  (attempted={r.get('attempted', '?')}, failed={r.get('failed', '?')})"
        )

    elapsed = time.time() - t0
    log.info(f"FULL BACKFILL complete in {elapsed/3600:.1f}h")
    if os.path.exists(CHECKPOINT_FILE):
        os.rename(CHECKPOINT_FILE, CHECKPOINT_FILE + ".done")


if __name__ == "__main__":
    main()
