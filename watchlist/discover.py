#!/usr/bin/env python3
"""
discover.py -- propose Bluesky accounts and feeds for the people and outlets on the watchlist.   (2026-10-16)

Runs on the Mac (keeps the load off the NAS) against free public endpoints only (Bluesky's public API, the people's own websites).
Reads a local seed file (NOT in the repo: the list of who you follow is personal), merges into the vault's watchlist note without
overwriting rows you have already decided, and prints what it found.

  seed file lines:   Name | Community | Tier | optional homepage | optional feed URLs to try (space-separated)
                     (Tier A = person, B = outlet / institution)
  usage:             python3 discover.py SEED_FILE WATCHLIST_NOTE [--vault VAULT_ROOT]

Status rules: `confirmed` only when the match is trustworthy (exact name, real activity, no comparable namesake, or a handle you
already captured; an outlet whose discovered feed parses and has recent items). Everything else is `candidate` and is never polled
until you change it to `confirmed`.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import watchlist_core as w

UA = "memex-watchlist/1.0 (personal research reader)"
BSKY = "https://public.api.bsky.app/xrpc/"
COMMON_FEED_PATHS = ("/feed", "/feed/", "/feed.xml", "/rss.xml", "/atom.xml", "/index.xml", "/rss", "/feeds/all.atom.xml")


def http_get(url: str, timeout: int = 15, maxbytes: int = 1_500_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(maxbytes), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, b"", ""
    except Exception:
        return 0, b"", ""


def bsky_json(method: str, **params):
    code, body, _ = http_get(BSKY + method + "?" + urllib.parse.urlencode(params, doseq=True))
    try:
        return json.loads(body) if code == 200 else {}
    except ValueError:
        return {}


def vault_handles(vault: Path) -> dict:
    """name -> Bluesky handle, from the user's own Bluesky captures (post_url + author / '## date — Author' headings)."""
    out = {}
    root = vault / "Resources" / "Bluesky"
    if not root.exists():
        return out
    for p in root.rglob("*.md"):
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "/Activity/" in str(p):
            for m in re.finditer(r"(?m)^##\s+\d{4}-\d{2}-\d{2}\s+[—–-]\s+(.+?)\s*\n(?:.*\n){0,4}?\*\*Post:\*\*\s*https://bsky\.app/profile/([^/\s]+)/post/", t):
                out.setdefault(w.norm_name(m.group(1)), m.group(2))
        else:
            a = re.search(r'(?m)^author:\s*"?\[\[(?:[^\]|]*/)?([^\]|#]+)', t)
            h = re.search(r"https://bsky\.app/profile/([^/\s\"']+)/post/", t)
            if a and h:
                out.setdefault(w.norm_name(a.group(1)), h.group(1))
    return out


def validate_feed(url: str):
    """(ok, newest_datetime) for a URL that should be a feed."""
    code, body, _ = http_get(url, timeout=15, maxbytes=3_000_000)
    items = w.parse_feed(body) if code == 200 else []
    if not items:
        return False, None
    dates = [i["published"] for i in items if i["published"]]
    return True, (max(dates) if dates else None)


def discover_feeds(page_url: str, want: int = 2) -> list:
    """Feed URLs for a website: advertised in its HTML first, then a few conventional paths; only feeds that parse."""
    code, body, _ = http_get(page_url)
    cands = w.find_feed_links(body.decode("utf-8", "replace"), page_url) if code == 200 else []
    base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(page_url))
    if "substack.com" in page_url:
        cands.insert(0, base + "/feed")
    if not cands:
        cands = [base + p for p in COMMON_FEED_PATHS]
    out = []
    for u in cands:
        ok, newest = validate_feed(u)
        if ok and (newest is None or newest > datetime.now(timezone.utc) - timedelta(days=365)):
            out.append(u)
        if len(out) >= want:
            break
    return out


def bio_sites(desc: str) -> list:
    skip = re.compile(r"(bsky\.|linktr\.ee|twitter\.com|x\.com|linkedin\.com|instagram\.com|facebook\.com|youtu|mastodon|github\.com)", re.I)
    seen, out = set(), []
    for u in w.extract_urls(desc):
        if not skip.search(u) and u not in seen:
            seen.add(u)
            out.append(u)
    return out[:2]


def discover_person(name: str, known: dict, community: str = "", homepage: str = "", guesses: list = None) -> dict:
    row = {"bluesky": "", "feeds": [], "status": "candidate", "notes": ""}
    handle = known.get(w.norm_name(name), "")
    profiles = []
    if handle:
        profiles = bsky_json("app.bsky.actor.getProfiles", actors=[handle]).get("profiles", [])
    if not profiles:
        actors = bsky_json("app.bsky.actor.searchActors", q=name, limit=8).get("actors", [])
        dids = [a["did"] for a in actors][:25]
        profiles = bsky_json("app.bsky.actor.getProfiles", actors=dids).get("profiles", []) if dids else []
    prof, conf, why = w.bsky_confidence(profiles, name, handle)
    notes = []
    if prof and conf == "high" and not handle and not w.bio_topical(prof.get("description", ""), community):
        conf, why = "candidate", why + "; but the bio does not look like " + (community or "this field") + " work (possible namesake)"
    if prof:
        row["bluesky"] = prof["handle"]
        notes.append(f"bluesky: {why}")
        sites = bio_sites(prof.get("description", ""))
        for site in sites:
            row["feeds"].extend(f for f in discover_feeds(site, 1) if f not in row["feeds"])
        if row["feeds"]:
            notes.append("feed from their bio")
    else:
        notes.append(why)
    if conf == "high":
        row["status"] = "confirmed"
    if homepage:                                   # a homepage given in the seed is trusted like a hand-entered row
        found = [f for f in discover_feeds(homepage, 2) if f not in row["feeds"]]
        row["feeds"].extend(found)
        if found:
            notes.append("feed from the homepage in your seed")
            row["status"] = "confirmed" if (conf == "high" or not prof) else row["status"]
    for g in guesses or []:
        if g not in row["feeds"] and validate_feed(g)[0]:
            row["feeds"].append(g); notes.append("feed from your seed")
    if row["feeds"] and not prof:
        row["status"] = "confirmed"
    row["notes"] = "; ".join(notes)
    return row


def discover_outlet(name: str, homepage: str, guesses: list = None) -> dict:
    feeds = discover_feeds(homepage, 2) if homepage else []
    feeds += [g for g in (guesses or []) if g not in feeds and validate_feed(g)[0]]
    return {"bluesky": "", "feeds": feeds, "status": "confirmed" if feeds else "candidate",
            "notes": "feed discovered and parses" if feeds else "no feed found on the homepage"}


def read_seed(path: Path) -> list:
    seeds = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        seeds.append({"name": parts[0], "community": parts[1] if len(parts) > 1 else "", "tier": (parts[2] if len(parts) > 2 else "A").upper()[:1] or "A",
                      "homepage": parts[3] if len(parts) > 3 else "", "guesses": parts[4].split() if len(parts) > 4 else []})
    return seeds


INTRO = """---
title: Watchlist
type: watchlist
tags:
  - watchlist
---

# Watchlist: key figures and institutions

The NAS polls the rows below whose **Status is `confirmed`**, collects their new Bluesky posts and feed items, scores them, and
the best few appear in the daily digest under *From people you track*.

- **Edit freely.** Change `candidate` to `confirmed` once you have checked the Bluesky handle or feed, or to `skip` to drop a row.
  Add feeds (space-separated) or a Bluesky handle if you know them. Discovery never overwrites a row you have already edited; it only adds new names.
- **Tier**: A = a person, B = an institution or outlet (scored a little lower so a big outlet does not crowd out people).
- Name search finds decoys, so only exact-name, clearly-active accounts are auto-confirmed; read the Notes column.
- Source health (feeds that went quiet or started failing) is in `Review/Watchlist status`.

"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seed"); ap.add_argument("note")
    ap.add_argument("--vault", default="")
    a = ap.parse_args()
    seeds = read_seed(Path(a.seed))
    note = Path(a.note)
    existing = w.parse_watchlist(note.read_text(encoding="utf-8")) if note.exists() else []
    have = {w.norm_name(r["name"]) for r in existing}
    known = vault_handles(Path(a.vault)) if a.vault else {}
    print(f"seed: {len(seeds)} names | already in the note: {len(existing)} | handles known from your Bluesky captures: {len(known)}")
    new_rows = []
    for i, s in enumerate(seeds, 1):
        if w.norm_name(s["name"]) in have:
            continue
        res = (discover_outlet(s["name"], s["homepage"], s["guesses"]) if s["tier"] == "B"
               else discover_person(s["name"], known, s["community"], s["homepage"], s["guesses"]))
        row = {"name": s["name"], "community": s["community"], "tier": s["tier"], **res}
        new_rows.append(row)
        print(f"  [{i:3}/{len(seeds)}] {s['name'][:30]:30} {row['status']:9} bsky={row['bluesky'] or '-':28} feeds={len(row['feeds'])}")
        time.sleep(0.25)
    rows = existing + new_rows
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(INTRO + w.render_table(rows) + "\n", encoding="utf-8")
    c = {k: sum(1 for r in rows if r["status"] == k) for k in ("confirmed", "candidate", "skip")}
    print(f"\nwrote {note} | rows: {len(rows)} (+{len(new_rows)} new) | {c}")


if __name__ == "__main__":
    sys.exit(main())
