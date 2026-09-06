#!/usr/bin/env python3
"""
mac_backfill_personal.py
-------------------------
Runs the fully-fixed backfill logic (retry-with-backoff, /items/top not the
flat /items endpoint, annotation capture) against Personal Library only, from
the Mac -- using its real compute instead of the NAS's 2-core Celeron, which
is the actual bottleneck (local PDF extraction + embedding, not Zotero API
latency). Talks to the NAS's Qdrant over Tailscale.

Resume checkpoint (added 2026-08-21, after the Mac shut down mid-run and
killed the process -- the indexed data survived fine, since it lives in
Qdrant on the NAS, but the run itself had to restart from item 1 with no way
to skip the ~1,623 items already done). Writes checkpoint.json after every
completed page (100 items) with the next start= offset; on launch, reads it
back and resumes from there instead of 0. Deleting checkpoint.json forces a
full restart from the beginning.

index_item() deletes-then-reinserts a given item's points before writing, so
this is also safe to run over already-indexed items -- nothing duplicated,
the checkpoint is purely a time-saver, not a correctness requirement.
"""

import os
import sys
import json
import time
import logging

# Split available cores with the concurrent mac_backfill_remaining.py job
# rather than each grabbing all 8 logical cores unthrottled (confirmed live
# 2026-08-22: unpinned, this alone was using 375% CPU on its own).
# THREADS_PER_JOB overridable via env var.
os.environ.setdefault("OMP_NUM_THREADS", os.environ.get("THREADS_PER_JOB", "4"))
os.environ.setdefault("MKL_NUM_THREADS", os.environ.get("THREADS_PER_JOB", "4"))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zotero_indexer import (  # noqa: E402
    ensure_collection,
    client_for,
    get_collection_map,
    index_item,
)
from qdrant_client import QdrantClient
from pyzotero import zotero

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

ZOTERO_API_KEY = os.environ["ZOTERO_API_KEY"]
ZOTERO_USER_ID = os.environ["ZOTERO_USER_ID"]
QDRANT_HOST = os.environ.get("QDRANT_HOST", "schoerro.tail3f930f.ts.net")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))

LIBRARY = {"id": ZOTERO_USER_ID, "type": "user", "name": "Personal Library"}
CHECKPOINT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoint.json")


def load_checkpoint() -> int:
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE) as f:
                data = json.load(f)
            start = data.get("start", 0)
            log.info(f"Resuming from checkpoint: start={start} (attempted={data.get('attempted', '?')} as of {data.get('saved_at', '?')})")
            return start
        except Exception as e:
            log.warning(f"Could not read checkpoint file, starting from 0: {e}")
    return 0


def save_checkpoint(start: int, attempted: int, succeeded: int, failed: int):
    try:
        with open(CHECKPOINT_FILE, "w") as f:
            json.dump({
                "start": start,
                "attempted": attempted,
                "succeeded": succeeded,
                "failed": failed,
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, f, indent=2)
    except Exception as e:
        log.warning(f"Could not write checkpoint: {e}")


def fetch_page_with_retry(zot: zotero.Zotero, start: int, attempts: int = 3):
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


def verify_library(client: QdrantClient, library: dict) -> int:
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
    # timeout=60 was the original value here -- diagnosed 2026-08-22 as the
    # actual root cause of a widespread false-failure streak: Qdrant writes
    # use wait=true by default (block until fully committed, not just
    # queued), and with this collection's current size plus this NAS's tight
    # memory (58% of a 512MB limit observed live), a real commit now
    # regularly takes just over 60s -- confirmed via Qdrant's own request
    # log, which showed every "failed" write actually returning HTTP 200
    # around 60.0-63.1s, i.e. genuinely succeeding, just a hair after this
    # client gave up waiting. 180s gives real margin above the observed
    # ceiling without masking a genuine hang if one ever occurs.
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=180)
    ensure_collection(client)

    zot = client_for(LIBRARY)
    zot.client.timeout = 60
    collection_map = get_collection_map(zot)
    target = zot.num_items()

    start = load_checkpoint()
    log.info(f"=== Personal Library -- corrected full re-index (target: {target} items, resuming from start={start}) ===")

    attempted = 0
    succeeded = 0
    failed = 0
    complete = False
    t0 = time.time()

    while True:
        try:
            items = fetch_page_with_retry(zot, start)
        except Exception as e:
            log.error(f"Giving up at start={start} after retries: {e}")
            break

        for item in items:
            if not isinstance(item, dict):
                continue
            attempted += 1
            item_key = item.get("data", {}).get("key", "?")
            try:
                index_item(client, zot, LIBRARY, item, collection_map)
                succeeded += 1
            except Exception as e:
                failed += 1
                log.error(f"  Failed to index item {item_key}: {e}")
            if attempted % 50 == 0:
                elapsed = time.time() - t0
                rate = attempted / elapsed * 3600 if elapsed > 0 else 0
                log.info(f"  ... {attempted} attempted this run ({succeeded} ok, {failed} failed) -- {rate:.0f} items/hr")

        if len(items) < 100:
            complete = True
            break

        start += 100
        save_checkpoint(start, attempted, succeeded, failed)

    elapsed = time.time() - t0
    log.info(f"Personal Library: {attempted} attempted this run, {succeeded} succeeded, {failed} failed -- {'complete' if complete else 'INCOMPLETE'} in {elapsed/3600:.2f}h")

    if complete and os.path.exists(CHECKPOINT_FILE):
        os.rename(CHECKPOINT_FILE, CHECKPOINT_FILE + ".done")
        log.info("Checkpoint marked done.")

    # wait=False writes may still be settling server-side when the loop
    # above finishes -- give Qdrant a moment before reading the count back,
    # so this verification isn't a false "SHORT" against writes that are
    # genuinely queued but not yet visible to a scroll() read.
    log.info("Waiting 30s for any in-flight writes to settle before verifying...")
    time.sleep(30)
    actual = verify_library(client, LIBRARY)
    match = "OK" if actual >= target else "SHORT"
    log.info(f"VERIFICATION: target={target}  verified_in_qdrant={actual}  [{match}]")


if __name__ == "__main__":
    main()
