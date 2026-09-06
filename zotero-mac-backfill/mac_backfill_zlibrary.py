#!/usr/bin/env python3
"""
mac_backfill_zlibrary.py
-------------------------
Z-Library backfill, moved from the NAS to the Mac (2026-08-23) once both
other Mac jobs (Personal Library, Remaining 16 libraries) finished. The NAS
was managing only ~33-50 items/hr on this library specifically -- it's full
academic books, not short papers/articles, so items run to hundreds of
chunks each, and the NAS's 2-core Celeron was the real bottleneck, not the
script. The Mac did Personal Library (2,541 items) in 3.45h (~725 items/hr).

This is a one-time backfill only -- the NAS's own zotero-indexer container
keeps running its normal always-on incremental sync throughout (unaffected,
on its own 12h poll cycle); nothing about the "NAS stays canonical/always-on"
principle changes here, since this script's job is done once Z-Library is
caught up, same as the other two Mac backfills already were.

Talks to the NAS's Qdrant over Tailscale, same as the other two Mac runs.
Seeded from the NAS's own checkpoint (current_start=2100) at handoff time --
see RESUME_START_DEFAULT below.

Does NOT run each item in its own forked subprocess, unlike the NAS's
full_backfill.py. That was tried first here and failed badly (2026-08-23):
after ~51 successful items, every subsequent fork()'d child started
crashing immediately on macOS -- a known, documented risk (fork() after a
process has touched networking/TLS state is unreliable on macOS, which is
exactly why Python's multiprocessing defaults to 'spawn' there instead of
'fork', unlike Linux/the NAS). Because the loop treated "crashed with no
result" the same as "processed and failed," it silently burned through all
11,011 remaining items in 17 minutes and marked the whole library falsely
complete, with only the first 51 items actually indexed.

Two more layers were added after that, in order:
  1. A consecutive-failure circuit breaker -- five real failures in a row
     aborts the run loudly, without marking anything complete, so a
     systemic problem stops the run and gets surfaced rather than silently
     swallowed as fake progress the way the crash cascade was.
  2. A real per-item timeout, added after a genuine hang was directly
     confirmed (2026-08-23 21:11): the process sat at 0.0% CPU for 30+
     minutes, every open socket stuck in CLOSE_WAIT, identical CPU time
     across two `ps` checks a second apart -- unlike the earlier "stuck"
     report on the NAS, which turned out to be a timezone miscalculation,
     this one was directly confirmed real. Implemented safely this time via
     a persistent worker pool using multiprocessing's 'spawn' context
     (`new_worker_pool()` below), not 'fork' -- spawn starts each worker as
     a completely fresh interpreter with nothing inherited from the parent,
     so it doesn't share fork's "corrupted state from networking already
     touched in the parent" crash class. The model loads once per worker
     (via the pool's initializer, not per item), and the pool is only ever
     recreated after a genuine timeout, which should be rare -- so this
     keeps the earlier throughput fix while adding real hang protection.

Run detached so it survives the terminal closing:
  nohup caffeinate -s -w $$ python3 mac_backfill_zlibrary.py > zlibrary_stdout.log 2>&1 &
Progress (use the stdout log, not zlibrary.log -- see README note below):
  tail -f zlibrary_stdout.log

Note on zlibrary.log: logging.FileHandler's target file was observed
staying empty during the earlier fork-based attempt (likely a buffering
quirk from the file handle being duplicated across forked children). Now
that there's no forking at all, this may no longer apply, but the stdout
log is kept as the primary log regardless since it's already confirmed
reliable.
"""

import os
import sys
import json
import time
import logging
import multiprocessing as mp

os.environ.setdefault("OMP_NUM_THREADS", os.environ.get("THREADS_PER_JOB", "8"))
os.environ.setdefault("MKL_NUM_THREADS", os.environ.get("THREADS_PER_JOB", "8"))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

print("mac_backfill_zlibrary.py starting, about to import zotero_indexer (torch import, can take a while)...", flush=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zotero_indexer import (  # noqa: E402
    QDRANT_HOST,
    QDRANT_PORT,
    ensure_collection,
    discover_libraries,
    client_for,
    get_collection_map,
    get_model,
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
        logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), "zlibrary.log")),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

TARGET_LIBRARY_NAME = "Integrated_CDUK_Z-Library_2020"
CHECKPOINT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zlibrary_checkpoint.json")
# Seeded from the NAS's own checkpoint at the moment of handoff (2026-08-23).
RESUME_START_DEFAULT = 2100
MAX_CONSECUTIVE_FAILURES = 5
ITEM_TIMEOUT_SECONDS = int(os.environ.get("ITEM_TIMEOUT_SECONDS", "900"))

# Real hang confirmed live 2026-08-23 21:11 -- process sat at 0.0% CPU for
# 30+ minutes with every open socket stuck in CLOSE_WAIT (the remote end
# closed, but a blocked read never noticed). Unlike the earlier "stuck"
# report on the NAS (which turned out to be a timezone miscalculation, not
# a real hang), this one was directly confirmed: identical CPU time across
# two ps checks a second apart. So a per-item timeout genuinely is needed
# here -- but a forked subprocess already proved unsafe on macOS (see the
# big comment at the top of this file). The safe version: a persistent
# worker pool using the 'spawn' context, not 'fork'. Spawn starts each
# worker as a completely fresh interpreter with nothing inherited from the
# parent -- immune to the "corrupted state from networking already touched
# in the parent" crash class that fork hit. The model loads once per
# worker (via the pool's initializer, not per item), and the pool is only
# ever recreated after a genuine timeout -- which should be rare -- so this
# keeps both the throughput fix and adds real hang protection.
_pool_ctx = mp.get_context("spawn")


def _pool_worker_init():
    get_model()


def _index_item_task(library, item, collection_map):
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=180)
    zot = client_for(library)
    zot.client.timeout = 60
    index_item(client, zot, library, item, collection_map)
    return True


def new_worker_pool():
    return _pool_ctx.Pool(processes=1, initializer=_pool_worker_init)


def load_checkpoint() -> int:
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE) as f:
                data = json.load(f)
            start = data.get("start", RESUME_START_DEFAULT)
            log.info(f"Resuming from checkpoint: start={start} (attempted={data.get('attempted', '?')} as of {data.get('saved_at', '?')})")
            return start
        except Exception as e:
            log.warning(f"Could not read checkpoint file, using seeded start: {e}")
    log.info(f"No local checkpoint yet -- seeding from NAS handoff point, start={RESUME_START_DEFAULT}")
    return RESUME_START_DEFAULT


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
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=180)
    ensure_collection(client)

    seed_zot = zotero.Zotero(os.environ["ZOTERO_USER_ID"], "user", os.environ["ZOTERO_API_KEY"])
    seed_zot.client.timeout = 60
    all_libraries = discover_libraries(seed_zot)
    matches = [lib for lib in all_libraries if lib["name"] == TARGET_LIBRARY_NAME]
    if not matches:
        log.error(f"Could not find library named {TARGET_LIBRARY_NAME!r} -- aborting.")
        sys.exit(1)
    LIBRARY = matches[0]

    zot = client_for(LIBRARY)
    zot.client.timeout = 60
    collection_map = get_collection_map(zot)
    target = zot.num_items()

    start = load_checkpoint()
    log.info(f"=== {LIBRARY['name']} -- moved from NAS to Mac (target: {target} items, resuming from start={start}) ===")

    attempted = 0
    succeeded = 0
    failed = 0
    consecutive_failures = 0
    complete = False
    aborted = False
    t0 = time.time()

    log.info("Starting item worker pool (spawn context, model loads once)...")
    pool = new_worker_pool()

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
                async_result = pool.apply_async(_index_item_task, (LIBRARY, item, collection_map))
                async_result.get(timeout=ITEM_TIMEOUT_SECONDS)
                succeeded += 1
                consecutive_failures = 0
            except mp.TimeoutError:
                failed += 1
                consecutive_failures += 1
                log.error(f"  TIMEOUT indexing item {item_key} after {ITEM_TIMEOUT_SECONDS}s -- restarting worker pool, skipping item")
                pool.terminate()
                pool.join()
                pool = new_worker_pool()
            except Exception as e:
                failed += 1
                consecutive_failures += 1
                log.error(f"  Failed to index item {item_key}: {e}")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    log.error(
                        f"  {consecutive_failures} consecutive failures -- aborting run rather than continuing "
                        f"blind (see 2026-08-23 note at top of this file for why this circuit breaker exists). "
                        f"Checkpoint NOT marked done. Needs investigation before resuming."
                    )
                    aborted = True
                    break

            if attempted % 50 == 0:
                elapsed = time.time() - t0
                rate = attempted / elapsed * 3600 if elapsed > 0 else 0
                log.info(f"  ... {attempted} attempted this run ({succeeded} ok, {failed} failed) -- {rate:.0f} items/hr")

        if aborted:
            break

        if len(items) < 100:
            complete = True
            break

        start += 100
        save_checkpoint(start, attempted, succeeded, failed)

    pool.terminate()
    pool.join()

    elapsed = time.time() - t0
    status = "complete" if complete else ("ABORTED" if aborted else "INCOMPLETE")
    log.info(f"{LIBRARY['name']}: {attempted} attempted this run, {succeeded} succeeded, {failed} failed -- {status} in {elapsed/3600:.2f}h")

    if complete and os.path.exists(CHECKPOINT_FILE):
        os.rename(CHECKPOINT_FILE, CHECKPOINT_FILE + ".done")
        log.info("Checkpoint marked done.")

    if complete:
        state = load_state()
        try:
            new_version = zot.last_modified_version()
            lib_state = state["libraries"].setdefault(
                LIBRARY["id"], {"version": 0, "type": LIBRARY["type"], "name": LIBRARY["name"]}
            )
            lib_state["version"] = new_version
            lib_state["name"] = LIBRARY["name"]
            save_state(state)
            log.info("State cursor updated to current version -- normal incremental sync resumes cleanly from here.")
        except Exception as e:
            log.warning(f"Could not update state cursor: {e}")

    log.info("Waiting 30s for any in-flight writes to settle before verifying...")
    time.sleep(30)
    try:
        actual = verify_library(client, LIBRARY)
        match = "OK" if actual >= target else "SHORT"
        log.info(f"VERIFICATION: target={target}  verified_in_qdrant={actual}  [{match}]")
    except Exception as e:
        log.warning(f"Could not verify (Qdrant read failed) -- run itself still completed fine: {e}")


if __name__ == "__main__":
    main()
