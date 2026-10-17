#!/usr/bin/env python3
"""
poller.py -- one-shot collector for the key-figures watchlist (2026-10-16).

Run every few hours by a DSM Task Scheduler task as a throwaway container (see watchlist-run.sh). Reads the watchlist table from
the vault, polls every `confirmed` row (Bluesky public API + RSS/Atom feeds, stdlib HTTP only), then:
  1. drops what it has already seen and what the user already has in the vault (novelty);
  2. scores each new item on four computed signals (see watchlist_core): format, relevance to the user's own notes (embedding +
     the existing Qdrant index), convergence (how many other tracked sources link the same thing), reach vs the author's baseline;
  3. keeps every item in a candidate pool, and chooses a few for the digest under per-source and global caps;
  4. has a small model word a one-line summary and a "why", from the computed signals only (it never decides what matters);
  5. writes digest_items.jsonl (read by the ingest digest), pool.jsonl, state.json and a generated health note.
DRY_RUN=1 prints what it would do and writes nothing.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import watchlist_core as w

WATCHLIST = Path(os.environ.get("WATCHLIST_FILE", "/vault/Resources/Watchlist/Watchlist.md"))
VAULT = Path(os.environ.get("VAULT_ROOT", "/vault"))
DATA = Path(os.environ.get("DATA_DIR", "/data"))
STATUS_NOTE = Path(os.environ.get("STATUS_NOTE", "/review/Watchlist status.md"))
QDRANT_HOST, QDRANT_PORT = os.environ.get("QDRANT_HOST", "qdrant"), int(os.environ.get("QDRANT_PORT", "6333"))
THRESHOLD = float(os.environ.get("DIGEST_THRESHOLD", "0.45"))
PER_SOURCE_CAP = int(os.environ.get("PER_SOURCE_CAP", "2"))
GLOBAL_CAP = int(os.environ.get("GLOBAL_CAP", "7"))
MAX_AGE_DAYS = int(os.environ.get("MAX_AGE_DAYS", "10"))
DIGEST_MAX_AGE_DAYS = int(os.environ.get("DIGEST_MAX_AGE_DAYS", "3"))
DRY_RUN = os.environ.get("DRY_RUN") == "1"
MODEL = os.environ.get("WATCHLIST_MODEL", "claude-haiku-4-5")
OWN_SOURCES = ["projects", "boox", "personal", "people", "meeting-notes", "inbox"]
BSKY = "https://public.api.bsky.app/xrpc/"
UA = "memex-watchlist/1.0 (personal research reader)"
STATE_F, POOL_F, DIGEST_F = DATA / "state.json", DATA / "pool.jsonl", DATA / "digest_items.jsonl"


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


# -------------------------------------------------------------------------------------------------- io helpers


def http_get(url, etag="", lastmod="", timeout=20, maxbytes=3_000_000):
    headers = {"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/json, */*"}
    if etag:
        headers["If-None-Match"] = etag
    if lastmod:
        headers["If-Modified-Since"] = lastmod
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            return {"status": r.status, "body": r.read(maxbytes), "etag": r.headers.get("ETag", ""), "lastmod": r.headers.get("Last-Modified", "")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": b"", "etag": etag, "lastmod": lastmod}
    except Exception as e:
        return {"status": 0, "body": b"", "etag": "", "lastmod": "", "error": str(e)[:100]}


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def save_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    tmp.replace(p)


def tail_lines(p: Path, n: int) -> list:
    if not p.exists():
        return []
    return p.read_text(errors="replace").splitlines()[-n:]


# -------------------------------------------------------------------------------------------------- collection


def collect_bluesky(row, handle, state, now):
    res = http_get(BSKY + "app.bsky.feed.getAuthorFeed?" + urllib.parse.urlencode({"actor": handle, "limit": 50, "filter": "posts_no_replies"}))
    if res["status"] != 200:
        return None, f"HTTP {res['status']} {res.get('error', '')}".strip()
    try:
        feed = json.loads(res["body"]).get("feed", [])
    except ValueError:
        return None, "bad JSON"
    posts = [p for p in (w.normalise_bsky(e, handle) for e in feed) if p]
    base = w.median([p["engagement"] for p in posts]) if len(posts) >= 8 else None
    out = []
    for p in posts:
        if not p["published"] or p["published"] < now - timedelta(days=MAX_AGE_DAYS):
            continue
        p.update(baseline=base, source_key=f"bsky:{handle}", id=w.item_id("post", p["guid"]),
                 target=w.canonical_url(p["link"] or p["url"]))
        out.append(p)
    return out, ""


def collect_feed(row, url, state, now):
    src = state["sources"].get(f"feed:{url}", {})
    res = http_get(url, src.get("etag", ""), src.get("lastmod", ""))
    if res["status"] == 304:
        return [], ""
    if res["status"] != 200:
        return None, f"HTTP {res['status']} {res.get('error', '')}".strip()
    entries = w.parse_feed(res["body"])
    if not entries:
        return None, "not a feed / empty"
    if w.dates_look_bulk(entries):
        state["sources"].setdefault(f"feed:{url}", {})["dates"] = "bulk-dated"
        return [], "dates unreliable (most entries share one date)"
    state["sources"].setdefault(f"feed:{url}", {}).pop("dates", None)
    state["sources"].setdefault(f"feed:{url}", {}).update(etag=res["etag"], lastmod=res["lastmod"])
    out = []
    for e in entries:
        if not e["published"] or e["published"] < now - timedelta(days=MAX_AGE_DAYS):
            continue
        out.append({"kind": "article", "title": e["title"], "url": e["url"], "link": e["url"], "published": e["published"], "author": e["author"],
                    "summary": e["summary"], "text_len": len(e["summary"]), "engagement": 0, "likes": 0, "reposts": 0, "baseline": None,
                    "source_key": f"feed:{url}", "id": w.item_id("art", w.canonical_url(e["url"])), "target": w.canonical_url(e["url"])})
    return out, ""


def vault_urls() -> set:
    """Canonical URLs the user already has in the vault (reading lists, newsletters, captured posts): novelty check."""
    keys = re.compile(r"(?m)^(?:link|post_url|url|source_url|external_url):\s*\"?(\S+?)\"?\s*$")
    out = set()
    for root, dirs, files in os.walk(VAULT):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("Networking", "Google Drive")]
        for f in files:
            if not f.endswith(".md"):
                continue
            try:
                with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                    head = fh.read(3000)
            except OSError:
                continue
            for m in keys.finditer(head):
                c = w.canonical_url(m.group(1))
                if c:
                    out.add(c)
    return out


# -------------------------------------------------------------------------------------------------- relevance


class Relevance:
    """Similarity of an item to the user's own writing and notes, via the same embedding model and Qdrant index the search uses."""

    def __init__(self):
        self.ok, self.model, self.client = False, None, None
        try:
            from qdrant_client import QdrantClient
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer("all-MiniLM-L6-v2")
            self.client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=30)
            self.ok = True
        except Exception as e:
            log("relevance unavailable, using a neutral value:", str(e)[:100])

    def score(self, texts):
        """-> [(mean_top3, best_note_title)] aligned with texts."""
        if not self.ok or not texts:
            return [(0.35, "")] * len(texts)
        from qdrant_client.models import FieldCondition, Filter, MatchAny
        flt = Filter(must=[FieldCondition(key="source", match=MatchAny(any=OWN_SOURCES))])
        out = []
        vecs = self.model.encode(texts, show_progress_bar=False)
        for v in vecs:
            try:
                pts = self.client.query_points(collection_name="second_brain", query=v.tolist(), limit=5, query_filter=flt, with_payload=True).points
                top = [p.score for p in pts[:3]]
                real = next((p for p in pts if p.payload.get("source") != "people"), None)      # a People note is a poor thing to cite
                out.append((sum(top) / len(top) if top else 0.0, (real.payload.get("title") or "") if real else ""))
            except Exception as e:
                log("qdrant query failed:", str(e)[:80])
                out.append((0.35, ""))
        return out


# -------------------------------------------------------------------------------------------------- wording


def why_from_signals(c) -> str:
    """The reason is assembled from the computed signals, never written by the model, so it cannot embellish."""
    sg, bits = c["signals"], []
    if sg["rel"] >= 0.5 and sg["neighbour"]:
        bits.append(f"closely matches your note \u201c{sg['neighbour'][:70]}\u201d")
    if sg["conv_n"]:
        bits.append(f"also shared by {sg['conv_n']} other tracked source{'s' if sg['conv_n'] > 1 else ''}")
    if sg["reach_ratio"] and sg["reach_ratio"] >= 2:
        bits.append(f"about {sg['reach_ratio']:.0f}x their usual engagement on Bluesky")
    if c["kind"] == "article":
        bits.append("a full article")
    return " \u00b7 ".join(bits) or "from someone on your watchlist"


def word_items(chosen, api_key):
    """Fill the one-sentence summary (model) and the why (deterministic)."""
    client = None
    if api_key:
        try:
            from anthropic import Anthropic
            client = Anthropic(api_key=api_key)
        except Exception as e:
            log("anthropic unavailable:", str(e)[:80])
    for c in chosen:
        c["why"] = why_from_signals(c)
        c["summary_text"] = c["summary"][:220]
        if not client:
            continue
        try:
            msg = client.messages.create(model=MODEL, max_tokens=120, messages=[{"role": "user", "content": (
                "Summarise this item for a researcher in digital and AI governance in ONE sentence of at most 30 words, in your own words. "
                "Say only what the item says; add no opinion, no judgement of importance and nothing that is not in the text. Reply with the "
                f"sentence only.\n\nAuthor/outlet: {c['person']}\nTitle: {c['title']}\nText: {c['summary'][:900]}")}])
            c["summary_text"] = msg.content[0].text.strip().strip('"')
        except Exception as e:
            log("summary failed for one item:", str(e)[:80])


# -------------------------------------------------------------------------------------------------- status note


def write_status(state, rows, run, now):
    lines = ["---", "title: Watchlist status", "type: watchlist-status", "tags:", "  - watchlist", "---", "", "# Watchlist status", "",
             f"Generated by the watchlist poller, {now.strftime('%Y-%m-%d %H:%M UTC')}. Last run: {run['sources']} sources polled, "
             f"{run['new']} new items, {run['selected']} chosen for the digest, {run['failed']} source(s) failed.", "",
             "| Source | Type | Last success | Newest item | Failures | Health |", "|---|---|---|---|---|---|"]
    by_key = {}
    for r in rows:
        if r["status"] != "confirmed":
            continue
        if r.get("bluesky"):
            by_key[f"bsky:{r['bluesky']}"] = (r["name"], "Bluesky")
        for u in r.get("feeds", []):
            if not any(w.canonical_url(u) == w.canonical_url(k[5:]) for k in by_key if k.startswith("feed:")):   # /feed and /feed/ are one
                by_key[f"feed:{u}"] = (r["name"], "feed")
    for key, (name, kind) in sorted(by_key.items(), key=lambda kv: kv[1]):
        e = state["sources"].get(key, {})
        lines.append(f"| {name} | {kind} | {e.get('last_success', '')[:16]} | {e.get('last_item', '')[:10]} | {e.get('consecutive_failures', 0)} | {w.health_label(e, now)}{' (dates unreliable: skipped)' if e.get('dates') else ''} |")
    STATUS_NOTE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_NOTE.write_text("\n".join(lines) + "\n")


# -------------------------------------------------------------------------------------------------- main


def main():
    now = datetime.now(timezone.utc)
    state = load_json(STATE_F, {})
    state.setdefault("sources", {}); state.setdefault("seen", {}); state.setdefault("selected", [])
    if not WATCHLIST.exists():
        log("no watchlist note at", WATCHLIST); return 1
    rows = w.parse_watchlist(WATCHLIST.read_text(encoding="utf-8"))
    live = [r for r in rows if r["status"] == "confirmed"]
    log(f"watchlist: {len(rows)} rows, {len(live)} confirmed{' [DRY RUN]' if DRY_RUN else ''}")

    items, failed, n_sources, polled = [], 0, 0, set()
    for r in live:
        srcs = ([("bsky", r["bluesky"])] if r.get("bluesky") else []) + [("feed", u) for u in r.get("feeds", [])]
        for kind, ident in srcs:
            ck = (kind, w.canonical_url(ident) if kind == "feed" else ident.lower())
            if ck in polled:                              # the same feed listed twice (/feed and /feed/) is one source
                continue
            polled.add(ck)
            n_sources += 1
            key = f"bsky:{ident}" if kind == "bsky" else f"feed:{ident}"
            got, err = collect_bluesky(r, ident, state, now) if kind == "bsky" else collect_feed(r, ident, state, now)
            ent = state["sources"].setdefault(key, {})
            if got is not None and err:
                log(f"  ~ {r['name']} {key[:60]}: {err}")
            if got is None:
                failed += 1; log(f"  ! {r['name']} {key[:50]}: {err}")
                state["sources"][key] = w.update_health(ent, False, now.isoformat())
                continue
            newest = max([g["published"] for g in got if g["published"]], default=None)
            state["sources"][key] = {**ent, **w.update_health(ent, True, now.isoformat(), newest.isoformat() if newest else "")}
            for g in got:
                g.update(person=r["name"], tier=r["tier"], community=r["community"])
            items.extend(got)
            time.sleep(0.3)

    seen = state["seen"]
    uniq = {}
    for i in items:
        uniq.setdefault(i["id"], i)
    items = list(uniq.values())
    new = [i for i in items if i["id"] not in seen]
    log(f"collected {len(items)} recent items from {n_sources} sources; {len(new)} not seen before")

    known = vault_urls()
    pool_tail = [json.loads(l) for l in tail_lines(POOL_F, 6000) if l.strip().startswith("{")]
    cutoff = (now - timedelta(days=7)).isoformat()
    refs = {}                                   # target -> set of distinct sources that reference it (this run + last 7 days)
    for p in pool_tail:
        if p.get("ts", "") >= cutoff and p.get("target"):
            refs.setdefault(p["target"], set()).add(p["person"])
    for i in items:
        if i["target"]:
            refs.setdefault(i["target"], set()).add(i["person"])

    # relevance only for items that could matter
    fresh = now - timedelta(days=DIGEST_MAX_AGE_DAYS)
    cands = [i for i in new if w.format_score(i) >= 0.5 and (i["target"] not in known) and i["published"] >= fresh]
    rel = Relevance().score([f"{c['title']}. {c['summary'][:400]}" for c in cands]) if cands else []
    rel_by_id = {c["id"]: r for c, r in zip(cands, rel)}
    if rel:
        raw = sorted(r[0] for r in rel)
        log(f"relevance (raw cosine, {len(raw)} items): min {raw[0]:.2f}  median {raw[len(raw) // 2]:.2f}  p90 {raw[int(len(raw) * 0.9)]:.2f}  max {raw[-1]:.2f}")

    scored = []
    for i in new:
        mean3, neigh = rel_by_id.get(i["id"], (0.0, ""))
        fmt = w.format_score(i); rl = w.relevance_score(mean3)
        conv_n = len(refs.get(i["target"], set()) - {i["person"]}) if i["target"] else 0
        reach = w.reach_score(i["engagement"], i["baseline"]) if i["kind"] == "post" else None
        ratio = (i["engagement"] / max(i["baseline"], 3.0)) if (i["baseline"] is not None and i["kind"] == "post") else 0.0
        sc = w.total_score(fmt, rl, w.convergence_score(conv_n), reach, i["tier"])
        scored.append({"id": i["id"], "ts": now.isoformat(), "source": i["person"], "person": i["person"], "tier": i["tier"], "community": i["community"],
                       "kind": i["kind"], "title": i["title"], "url": i["url"], "link": i["link"], "target": i["target"],
                       "published": i["published"].isoformat(), "summary": i["summary"], "score": sc, "likes": i["likes"], "reposts": i["reposts"],
                       "seen_in_vault": i["target"] in known or w.canonical_url(i["url"]) in known,
                       "signals": {"fmt": fmt, "rel": round(rl, 3), "rel_raw": round(mean3, 3), "conv_n": conv_n, "reach": reach, "reach_ratio": round(ratio, 1), "neighbour": neigh}})

    recent = [s for s in state["selected"] if s["ts"] >= (now - timedelta(hours=24)).isoformat()]
    eligible = [s for s in scored if s["published"] >= (now - timedelta(days=DIGEST_MAX_AGE_DAYS)).isoformat()]
    chosen = w.select_for_digest(eligible, recent, THRESHOLD, PER_SOURCE_CAP, GLOBAL_CAP)
    word_items(chosen, os.environ.get("ANTHROPIC_API_KEY", ""))
    log(f"scored {len(scored)}; {len(chosen)} chosen for the digest (threshold {THRESHOLD}, caps {PER_SOURCE_CAP}/source, {GLOBAL_CAP}/day)")
    for s in sorted(scored, key=lambda x: -x["score"])[:12]:
        mark = "->DIGEST" if any(c["id"] == s["id"] for c in chosen) else ("seen" if s["seen_in_vault"] else "")
        log(f"   {s['score']:.2f} {mark:8} {s['person'][:24]:24} {s['kind']:7} {s['title'][:70]}  [conv={s['signals']['conv_n']} rel={s['signals']['rel']} fmt={s['signals']['fmt']}]")

    if DRY_RUN:
        for c in chosen:
            log("   would add:", c["person"], "|", c["title"][:60], "|", c["why"])
        return 0
    DATA.mkdir(parents=True, exist_ok=True)
    with POOL_F.open("a", encoding="utf-8") as f:
        for s in scored:
            if s["score"] >= 0.15 or s["kind"] == "article":
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
    if POOL_F.exists() and POOL_F.stat().st_size > 20_000_000:
        POOL_F.write_text("\n".join(tail_lines(POOL_F, 15000)) + "\n")
    with DIGEST_F.open("a", encoding="utf-8") as f:
        for c in chosen:
            f.write(json.dumps({"timestamp": now.isoformat(), "kind": "watchlist", "title": c["title"], "person": c["person"], "tier": c["tier"],
                                "community": c["community"], "url": c["link"] or c["url"], "post_url": c["url"], "date": c["published"][:10],
                                "summary": c["summary_text"], "why": c["why"], "score": c["score"], "likes": c["likes"], "reposts": c["reposts"]},
                               ensure_ascii=False) + "\n")
    for i in new:
        seen[i["id"]] = now.isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= (now - timedelta(days=60)).isoformat()}
    state["selected"] = recent + [{"id": c["id"], "source": c["source"], "target": c["target"], "ts": now.isoformat()} for c in chosen]
    state["last_run"] = now.isoformat()
    save_json(STATE_F, state)
    write_status(state, rows, {"sources": n_sources, "new": len(new), "selected": len(chosen), "failed": failed}, now)
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
