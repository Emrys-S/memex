"""
watchlist_core.py
-----------------
Pure logic for the key-figures watchlist (2026-10-16). No network, no third-party dependencies, so it is unit-tested here and
runs unchanged on the NAS (poller.py) and on the Mac (discover.py).

The watchlist is a markdown table in the vault (Resources/Watchlist/Watchlist.md). Only rows with Status `confirmed` are
polled; discovery proposes the rest as `candidate` for the user to confirm. Each poll collects new items (Bluesky posts and
feed entries), scores them on four computed signals (format, relevance to the user's own notes, convergence, reach vs the
author's own baseline), and a few survive into the digest. The language model only words the "why", it never decides.
"""
import hashlib
import html as htmllib
import json
import math
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

COLUMNS = ["Name", "Community", "Tier", "Bluesky", "Feeds", "Status", "Notes"]

# ---------------------------------------------------------------------------------------------- watchlist table


def _cells(line: str) -> list:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def parse_watchlist(text: str) -> list:
    """Rows of the first table whose header starts with `Name`. Tolerant of anything else in the note."""
    rows, header = [], None
    for line in (text or "").splitlines():
        if not line.strip().startswith("|"):
            if header and rows:
                break
            continue
        cells = _cells(line)
        if header is None:
            if cells and cells[0].lower() == "name":
                header = [c.lower() for c in cells]
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        row = dict(zip(header, cells + [""] * (len(header) - len(cells))))
        if not row.get("name"):
            continue
        row["bluesky"] = row.get("bluesky", "").lstrip("@").strip()
        row["feeds"] = [u for u in re.split(r"[\s,]+", row.get("feeds", "")) if u.startswith(("http://", "https://"))]
        row["status"] = row.get("status", "").strip().lower() or "candidate"
        row["tier"] = (row.get("tier", "A") or "A").strip().upper()[:1]
        rows.append(row)
    return rows


def render_table(rows: list) -> str:
    out = ["| " + " | ".join(COLUMNS) + " |", "|" + "|".join(["---"] * len(COLUMNS)) + "|"]
    for r in rows:
        feeds = " ".join(r.get("feeds") or [])
        cells = [r.get("name", ""), r.get("community", ""), r.get("tier", "A"), r.get("bluesky", ""), feeds,
                 r.get("status", "candidate"), (r.get("notes") or "").replace("|", "/")]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------- urls and ids

_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_cid$|mc_eid$|igshid$|ref$|ref_src$|source$|s$|cmpid$|_hsenc$|_hsmi$)", re.I)


def canonical_url(url: str) -> str:
    """Scheme-less, tracking-free, fragment-free form used to recognise the same page across sources."""
    try:
        p = urlparse((url or "").strip())
    except ValueError:
        return ""
    if not p.netloc:
        return ""
    host = re.sub(r"^www\.", "", p.netloc.lower())
    q = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k)])
    path = re.sub(r"/+$", "", p.path) or ""
    return f"{host}{path}" + (f"?{q}" if q else "")


def item_id(*parts) -> str:
    return hashlib.sha1("|".join(str(x) for x in parts).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------------------------- text helpers

_TAG = re.compile(r"<[^>]+>")


def strip_html(s: str, limit: int = 800) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s or "")
    s = htmllib.unescape(_TAG.sub(" ", s))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def _parse_date(value: str):
    if not value:
        return None
    value = value.strip()
    try:
        d = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


# ---------------------------------------------------------------------------------------------- feeds


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_feed(data: bytes, max_items: int = 60) -> list:
    """RSS 2.0 / Atom -> [{title, url, published (datetime|None), author, summary, guid}], newest first. [] if not a feed."""
    if not data or len(data) > 5_000_000:
        return []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    items = []
    for el in root.iter():
        name = _local(el.tag)
        if name not in ("item", "entry"):
            continue
        f = {"title": "", "url": "", "published": None, "author": "", "summary": "", "guid": ""}
        content, summary = "", ""
        for ch in el:
            n = _local(ch.tag)
            txt = (ch.text or "").strip()
            if n == "title":
                f["title"] = strip_html(txt, 300)
            elif n == "link":
                if ch.get("href"):                      # Atom
                    if (ch.get("rel") or "alternate") == "alternate" or not f["url"]:
                        f["url"] = ch.get("href").strip()
                elif txt:                               # RSS
                    f["url"] = txt
            elif n in ("pubDate", "published", "updated", "date"):
                d = _parse_date(txt)
                if d and (f["published"] is None or n in ("pubDate", "published")):
                    f["published"] = d
            elif n in ("creator", "author"):
                f["author"] = strip_html(txt or "".join(x.text or "" for x in ch.iter() if _local(x.tag) == "name"), 120)
            elif n in ("encoded", "content"):
                content = txt
            elif n in ("description", "summary"):
                summary = txt
            elif n in ("guid", "id"):
                f["guid"] = txt
        f["summary"] = strip_html(content or summary, 800)
        if f["title"] and f["url"]:
            items.append(f)
    items.sort(key=lambda x: x["published"] or datetime(1970, 1, 1, tzinfo=timezone.utc), reverse=True)
    return items[:max_items]


class _FeedLinks(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base, self.found = base, []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if "alternate" in a.get("rel", "").lower() and re.search(r"(rss|atom)\+xml", a.get("type", ""), re.I) and a.get("href"):
            self.found.append((urljoin(self.base, a["href"]), a.get("title", "")))


def find_feed_links(page_html: str, base_url: str) -> list:
    """Feed URLs advertised by a page, without comment feeds, best first."""
    p = _FeedLinks(base_url)
    try:
        p.feed((page_html or "")[:600_000])
    except Exception:
        pass
    seen, out = set(), []
    for url, title in p.found:
        if re.search(r"comment", url + " " + title, re.I) or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def extract_urls(text: str) -> list:
    return [u.rstrip(".,);]") for u in re.findall(r"https?://[^\s<>\"')]+", text or "")]


# ---------------------------------------------------------------------------------------------- bluesky


def bsky_post_url(uri: str, handle: str) -> str:
    m = re.match(r"at://[^/]+/app\.bsky\.feed\.post/([A-Za-z0-9]+)$", uri or "")
    return f"https://bsky.app/profile/{handle}/post/{m.group(1)}" if m else ""


def engagement(post: dict) -> int:
    return (post.get("likeCount") or 0) + 2 * (post.get("repostCount") or 0) + 2 * (post.get("quoteCount") or 0) + (post.get("replyCount") or 0)


def normalise_bsky(entry: dict, handle: str):
    """One getAuthorFeed entry -> item dict, or None for reposts / replies / empty posts."""
    if (entry.get("reason") or {}).get("$type", "").endswith("reasonRepost"):
        return None
    post = entry.get("post") or {}
    rec = post.get("record") or {}
    if rec.get("reply"):
        return None
    text = (rec.get("text") or "").strip()
    emb = post.get("embed") or {}
    ext = emb.get("external") if emb.get("$type", "").startswith("app.bsky.embed.external") else None
    if ext is None and emb.get("$type", "").startswith("app.bsky.embed.recordWithMedia"):
        ext = (emb.get("media") or {}).get("external")
    link = (ext or {}).get("uri") or ""
    if not link:
        for facet in rec.get("facets") or []:
            for feat in facet.get("features") or []:
                if feat.get("$type", "").endswith("#link") and feat.get("uri"):
                    link = feat["uri"]
                    break
            if link:
                break
    if not text and not link:
        return None
    published = _parse_date(rec.get("createdAt", ""))
    title = (ext or {}).get("title") or text.split("\n")[0]
    return {
        "kind": "post", "title": strip_html(title, 200), "url": bsky_post_url(post.get("uri", ""), handle), "link": link,
        "published": published, "author": handle, "summary": strip_html(((ext or {}).get("description") or "") + " " + text, 600),
        "text_len": len(text), "engagement": engagement(post), "likes": post.get("likeCount") or 0,
        "reposts": post.get("repostCount") or 0, "guid": post.get("uri", ""),
        "quote": emb.get("$type", "").startswith("app.bsky.embed.record"),
    }


def median(values: list) -> float:
    v = sorted(values)
    if not v:
        return 0.0
    mid = len(v) // 2
    return float(v[mid]) if len(v) % 2 else (v[mid - 1] + v[mid]) / 2.0


# ---------------------------------------------------------------------------------------------- scoring


def clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def format_score(item: dict) -> float:
    """Long-form and linked material outranks chatter."""
    if item.get("kind") == "article":
        return 1.0
    n = item.get("text_len", 0)
    if item.get("link") and n >= 40:
        return 0.8
    if item.get("link"):
        return 0.6
    if n >= 400:
        return 0.7
    if n >= 200:
        return 0.5
    return 0.2


def reach_score(eng: int, baseline: float):
    """How far above this author's own usual engagement (None when there is no baseline to compare with)."""
    if baseline is None:
        return None
    ratio = eng / max(baseline, 3.0)
    return clip(math.log2(ratio) / 4.0) if ratio > 1 else 0.0


def relevance_score(mean_top3: float) -> float:
    """Cosine similarity to the user's own notes, rescaled: ~0.25 is background noise, ~0.6 is a close match."""
    return clip((mean_top3 - 0.25) / 0.35)


def convergence_score(n_other_sources: int) -> float:
    return clip(n_other_sources / 2.0)


def total_score(fmt: float, rel: float, conv: float, reach, tier: str = "A") -> float:
    if reach is None:
        s = 0.35 * fmt + 0.35 * rel + 0.30 * conv
    else:
        s = 0.30 * fmt + 0.30 * rel + 0.25 * conv + 0.15 * reach
    return round(s * (0.85 if tier == "B" else 1.0), 4)


def select_for_digest(cands: list, recent_selected: list, threshold: float = 0.45, per_source_cap: int = 2, global_cap: int = 7) -> list:
    """cands: dicts with id, score, source (person or outlet), seen_in_vault. recent_selected: [{id, source}] already chosen
    in the last 24 h. Highest score first; per-source and global caps apply across the window; items the user already has are
    never chosen."""
    per = {}
    for r in recent_selected:
        per[r["source"]] = per.get(r["source"], 0) + 1
    total = len(recent_selected)
    already = {r["id"] for r in recent_selected}
    targets = {r["target"] for r in recent_selected if r.get("target")}
    chosen = []
    for c in sorted(cands, key=lambda x: x["score"], reverse=True):
        if total >= global_cap:
            break
        if c["score"] < threshold or c.get("seen_in_vault") or c["id"] in already:
            continue
        if per.get(c["source"], 0) >= per_source_cap:
            continue
        if c.get("target") and c["target"] in targets:        # one item per link: the article and the post pointing at it are one story
            continue
        if c.get("target"):
            targets.add(c["target"])
        chosen.append(c)
        per[c["source"]] = per.get(c["source"], 0) + 1
        total += 1
    return chosen


def dates_look_bulk(entries: list) -> bool:
    """A feed whose entries mostly share one date was regenerated in bulk (a site migration or rebuild): its dates say nothing
    about when anything was written, so its old posts would look new (seen 2026-10-16: a think tank's 2020 annual report)."""
    stamps = [e["published"] for e in entries if e.get("published")]
    if len(stamps) < 8:
        return False
    # (1) a block of entries stamped with the same instant: a rebuild (a think tank: a dozen posts from 2018-2024 all at one minute;
    #     a legitimate feed with date-only precision repeats a stamp too, but nowhere near this often: a news wire ~6 per stamp)
    if max(stamps.count(t) for t in set(stamps)) >= 12:
        return True
    # (2) most of the feed on one calendar day
    days = [t.date() for t in stamps]
    return max(days.count(d) for d in set(days)) / len(days) >= 0.6


def update_health(entry: dict, ok: bool, now_iso: str, newest_item_iso: str = "") -> dict:
    e = dict(entry or {})
    if ok:
        e["last_success"], e["consecutive_failures"] = now_iso, 0
        if newest_item_iso and newest_item_iso > e.get("last_item", ""):
            e["last_item"] = newest_item_iso
    else:
        e["consecutive_failures"] = e.get("consecutive_failures", 0) + 1
        e["last_failure"] = now_iso
    return e


def health_label(entry: dict, now: datetime) -> str:
    if entry.get("consecutive_failures", 0) >= 3:
        return "failing"
    li = entry.get("last_item")
    if li:
        d = _parse_date(li)
        if d and (now - d).days > 90:
            return "quiet 90+ days"
    return "ok" if entry.get("last_success") else "not polled yet"


# ---------------------------------------------------------------------------------------------- discovery

_TITLES = re.compile(r"\b(prof(essor)?|dr|mr|ms|mrs|amb(assador)?|sir|dame)\b\.?", re.I)


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _TITLES.sub(" ", s.lower())
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def bsky_confidence(profiles: list, wanted: str, known_handle: str = ""):
    """Pick the Bluesky account for a person from search results (getProfiles-shaped dicts). Returns (profile|None,
    'high'|'candidate'|'none', reason). Names are an unreliable key (decoys are common), so 'high' needs an exact name match,
    real activity, and no comparably popular namesake; a handle already known from the vault wins outright."""
    if known_handle:
        for p in profiles:
            if p.get("handle", "").lower() == known_handle.lower():
                return p, "high", "handle known from your own Bluesky captures"
    w = norm_name(wanted)
    named = [p for p in profiles if norm_name(p.get("displayName", "")) == w]
    if not named:
        return None, "none", "no account with that name"
    named.sort(key=lambda p: p.get("followersCount") or 0, reverse=True)
    top = named[0]
    fol, posts = top.get("followersCount") or 0, top.get("postsCount") or 0
    if len(named) > 1 and (named[1].get("followersCount") or 0) >= 0.5 * fol and fol > 0:
        return top, "candidate", "two similar accounts share this name"
    if fol >= 300 and posts >= 20:
        return top, "high", f"exact name, {fol:,} followers, {posts:,} posts"
    return top, "candidate", f"exact name but only {fol:,} followers / {posts:,} posts"


# A bio has to look like the right field, or a name match is probably a namesake (found 2026-10-16: a network scientist was a perfect
# name match for a governance researcher of the same name). Deliberately narrow: no bare "data" or "tech".
_TOPIC = {
    "digital governance": r"govern|digital|internet|policy|democra|identity|infrastructure|privacy|sovereign|civic|govtech|regulat|"
                          r"public (interest|sector|good|policy)|open (government|data)|human rights|development|\bun\b|united nations",
    "ai governance": r"\bai\b|artificial intelligence|machine learning|algorithm|ethic|safety|govern|policy|regulat|\blaw\b|fairness|"
                     r"accountab|technology (policy|and society)|society",
    "foreign policy": r"foreign|international|geopolit|diplom|security|policy|politic|global|\beu\b|\bun\b|affairs|columnist|"
                      r"journalist|institute|fellow|professor|\bwar\b|trade|economy",
}


def bio_topical(description: str, community: str) -> bool:
    rx = _TOPIC.get((community or "").strip().lower())
    return bool(rx and re.search(rx, description or "", re.I))
