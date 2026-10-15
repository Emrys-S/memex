#!/usr/bin/env python3
"""calendar_people.py -- turn a published Outlook calendar (ICS) into "people I met" in the vault.

Exchange's published ICS feed has NO attendee list, so people are recognised from event TITLES
("Yves - Emrys", "Lunch Emrys/Marguerite", "Meeting w/ Usha Anand") against the People notes.

For each recent event that names a known person it
  * appends one dated line to that person's note (idempotent, hidden marker <!--cal:ID-->), and
  * lists the event in the daily note for that day (block between <!--cal-start/end-->).
Names it cannot settle go to  Review/Calendar queue.md  as checkboxes:
  - ambiguous first name ("Daniel": Eastwood / Jarvis / ...)  -> pick one, or "none of these"
  - a person-looking title with no People note ("Anita - Emrys") -> create the note, or ignore.
Only events that name someone are ever copied into the vault; personal events (school picnic,
dishwasher visit) are never stored.

Matching is deliberately conservative: first-name-only matches need a person-like context (the
user's own name or "with"/"lunch"/...), a single People note of type person with that first name,
and no different capitalised surname next to it.   stdlib only.

Usage:  calendar_people.py [--once|--loop] [--dry-run]
Env:    VAULT /vault  DATA /data  ICS_URL (secret) | ICS_FILE (tests)  HOME_TZ America/New_York
        SELF_NAMES Emrys  BACKFILL_DAYS 7  FETCH_EVERY 10800  INTERVAL 600
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:                                   # pragma: no cover
    ZoneInfo = None

VAULT = Path(os.environ.get("VAULT", "/vault"))
DATA = Path(os.environ.get("DATA", "/data"))
ICS_URL = os.environ.get("ICS_URL", "")
ICS_FILE = os.environ.get("ICS_FILE", "")
HOME_TZ = os.environ.get("HOME_TZ", "America/New_York")
SELF_NAMES = {n.strip().lower() for n in os.environ.get("SELF_NAMES", "Emrys").split(",") if n.strip()}
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS", "7"))
FETCH_EVERY = int(os.environ.get("FETCH_EVERY", "10800"))
INTERVAL = int(os.environ.get("INTERVAL", "600"))
# regexes (case-insensitive, ';;'-separated) for events that must never be matched or stored,
# e.g. personal contacts that share a first name with a work contact ("Alan RSS" = Alan at the
# Rudolf Steiner School, not Alan Dale / Alan Lowe)
IGNORE_PATTERNS = [x for x in os.environ.get("IGNORE_TITLE_PATTERNS", "").split(";;") if x.strip()]

PEOPLE_REL = "Resources/People"
DAILY_REL = "Daily"
QUEUE_REL = "Review/Calendar queue.md"
IGNORE = "__ignore__"

WIN_TZ = {
    "Eastern Standard Time": "America/New_York", "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver", "Pacific Standard Time": "America/Los_Angeles",
    "Atlantic Standard Time": "America/Halifax", "Alaskan Standard Time": "America/Anchorage",
    "Hawaiian Standard Time": "Pacific/Honolulu", "UTC": "UTC", "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik", "W. Europe Standard Time": "Europe/Berlin",
    "Romance Standard Time": "Europe/Paris", "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw", "E. Europe Standard Time": "Europe/Bucharest",
    "FLE Standard Time": "Europe/Kiev", "GTB Standard Time": "Europe/Bucharest",
    "Russian Standard Time": "Europe/Moscow", "Turkey Standard Time": "Europe/Istanbul",
    "Israel Standard Time": "Asia/Jerusalem", "Arab Standard Time": "Asia/Riyadh",
    "Arabian Standard Time": "Asia/Dubai", "India Standard Time": "Asia/Kolkata",
    "Pakistan Standard Time": "Asia/Karachi", "China Standard Time": "Asia/Shanghai",
    "Tokyo Standard Time": "Asia/Tokyo", "Singapore Standard Time": "Asia/Singapore",
    "AUS Eastern Standard Time": "Australia/Sydney", "New Zealand Standard Time": "Pacific/Auckland",
    "South Africa Standard Time": "Africa/Johannesburg", "E. Africa Standard Time": "Africa/Nairobi",
    "W. Central Africa Standard Time": "Africa/Lagos", "SA Eastern Standard Time": "America/Sao_Paulo",
}

# words that are never a first name in a meeting title
STOP = set("""a an and the of for to in on at with w x vs re fw fwd meeting call lunch coffee dinner drinks breakfast
chat catch up catchup sync check intro introduction review monthly weekly daily annual quarterly team all staff hold
follow followup conversation discussion session workshop webinar conference panel event launch seminar briefing
internal external office visit service school picnic ceremony paper read proposal draft call-in zoom teams webex
monday tuesday wednesday thursday friday saturday sunday january february march april may june july august
september october november december rector director chief officer professor prof dr mr ms mrs update planning
kickoff retro standup prep debrief interview onboarding on-boarding training holiday leave out ooh unu un cpr ai
swiss global network digital""".split())
CONTEXT_WORDS = {"with", "w", "lunch", "coffee", "dinner", "drinks", "breakfast", "chat", "catchup", "call", "meeting"}


def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}", flush=True)


def strip_accents(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def clean_display(name: str) -> str:
    n = re.sub(r"\([^)]*\)", " ", name)
    n = re.sub(r"[\s_\-]*\d+\s*$", "", n)
    return re.sub(r"\s+", " ", n).strip(" -_.,") or name.strip()


def words(s: str) -> list:
    """letters/digits only, accents stripped, lowercase"""
    return re.sub(r"[^a-z0-9]+", " ", strip_accents(s).lower()).split()


# ── ICS parsing ─────────────────────────────────────────────────────────────
@dataclass
class Event:
    uid: str
    title: str
    start: datetime                 # aware, in HOME_TZ
    all_day: bool = False
    key: str = ""

    @property
    def day(self) -> date:
        return self.start.date()


def home_tz():
    return ZoneInfo(HOME_TZ) if ZoneInfo else timezone.utc


_warned_tz = set()


def tz_for(tzid: str):
    if not ZoneInfo:
        return home_tz()
    name = WIN_TZ.get(tzid, tzid)
    try:
        return ZoneInfo(name)
    except Exception:
        if tzid not in _warned_tz:
            _warned_tz.add(tzid)
            log(f"unknown time zone '{tzid}': treating its events as {HOME_TZ}")
        return home_tz()


def unescape(v: str) -> str:
    return (v.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")).strip()


def parse_dt(params: str, value: str):
    """-> (aware datetime in HOME_TZ, all_day)"""
    value = value.strip()
    if "VALUE=DATE" in params or re.fullmatch(r"\d{8}", value):
        d = datetime.strptime(value[:8], "%Y%m%d")
        return d.replace(tzinfo=home_tz()), True
    m = re.fullmatch(r"(\d{8}T\d{6})(Z?)", value)
    if not m:
        raise ValueError(f"bad DTSTART {value!r}")
    naive = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
    if m.group(2) == "Z":
        aware = naive.replace(tzinfo=timezone.utc)
    else:
        tzm = re.search(r"TZID=([^;:]+)", params)
        aware = naive.replace(tzinfo=tz_for(tzm.group(1).strip('"')) if tzm else home_tz())
    return aware.astimezone(home_tz()), False


def parse_ics(text: str) -> list:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n[ \t]", "", text)                      # unfold
    events = []
    skipped = 0
    for block in text.split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT")[0]
        props = {}
        for line in block.split("\n"):
            m = re.match(r"^([A-Z][A-Z0-9-]*)((?:;[^:]*)?):(.*)$", line)
            if m and m.group(1) not in props:
                props[m.group(1)] = (m.group(2), m.group(3))
        if "DTSTART" not in props or "SUMMARY" not in props:
            skipped += 1
            continue
        status = props.get("STATUS", ("", ""))[1].strip().upper()
        title = unescape(props["SUMMARY"][1])
        if status == "CANCELLED" or re.match(r"(?i)^cancell?ed:", title):
            continue
        try:
            start, all_day = parse_dt(*props["DTSTART"])
        except ValueError:
            skipped += 1
            continue
        uid = props.get("UID", ("", ""))[1].strip() or hashlib.sha1(title.encode()).hexdigest()[:12]
        ev = Event(uid=uid, title=title, start=start, all_day=all_day)
        ev.key = hashlib.sha1(f"{uid}|{start.isoformat()}".encode()).hexdigest()[:12]
        events.append(ev)
    if skipped:
        log(f"{skipped} calendar entries skipped (no start/title or unparseable date)")
    return events


# ── people index and matching ───────────────────────────────────────────────
@dataclass
class Person:
    name: str
    type: str
    aliases: list
    w: list = field(default_factory=list)          # normalised words of the cleaned name

    @property
    def link(self) -> str:
        return f"[[{PEOPLE_REL}/{self.name}|{self.name}]]"


def fm_of(text: str):
    m = re.match(r"\A---\n(.*?)\n---[ \t]*\n?", text, re.DOTALL)
    return (m.group(1), text[m.end():]) if m else ("", text)


def load_people() -> list:
    pdir = VAULT / PEOPLE_REL
    out = []
    if not pdir.exists():
        return out
    for f in sorted(pdir.glob("*.md")):
        text = f.read_text(encoding="utf-8", errors="replace")
        fm, _ = fm_of(text)
        t = re.search(r"^type:[ \t]*(.*)$", fm, re.MULTILINE)
        al = []
        am = re.search(r"^aliases:[ \t]*(.*)$", fm, re.MULTILINE)
        if am:
            rest = am.group(1).strip()
            if rest.startswith("["):
                al = [a.strip().strip('"\'') for a in rest.strip("[]").split(",") if a.strip()]
            else:
                for line in fm[am.end():].split("\n")[1:]:
                    lm = re.match(r"^[ \t]+-[ \t]+(.*)$", line)
                    if not lm:
                        break
                    al.append(lm.group(1).strip().strip('"\''))
        p = Person(name=f.stem, type=(t.group(1).strip().strip('"\'') if t else "none"), aliases=al)
        p.w = words(clean_display(f.stem))
        out.append(p)
    return out


@dataclass
class Match:
    people: list = field(default_factory=list)         # [(Person, how)]   how: "name" | "first name"
    ambiguous: list = field(default_factory=list)      # [(token, [Person...])]
    new: list = field(default_factory=list)            # ["Anita", "Rosalind Fenwick"]

    def empty(self) -> bool:
        return not (self.people or self.ambiguous or self.new)


def tokens_of(title: str):
    """[(original, normalised, start, end)] -- split on anything that is not a letter/digit"""
    return [(m.group(0), strip_accents(m.group(0)).lower(), m.start(), m.end()) for m in re.finditer(r"[^\W_]+", title, re.UNICODE)]


def is_name_word(orig: str, nrm: str) -> bool:
    """Capitalised word that could be part of a person's name. ALL-CAPS and short mixed-caps words
    (RSS, UCL, XYZ) are acronyms, not names."""
    if not orig[:1].isupper() or orig.isupper() or len(nrm) < 2 or nrm in STOP or nrm in SELF_NAMES or nrm.isdigit():
        return False
    if len(orig) <= 4 and sum(c.isupper() for c in orig) >= 2:
        return False
    return True


def adjacent(title: str, a, b) -> bool:
    """only whitespace between the two tokens (a comma, dash, slash or bracket separates people/phrases)"""
    return title[a[3]:b[2]].strip() == ""


def match_event(title: str, people: list) -> Match:
    res = Match()
    toks = tokens_of(title)
    norm = [t[1] for t in toks]
    consumed = set()
    ctx = bool(SELF_NAMES & set(norm)) or bool(CONTEXT_WORDS & set(norm))

    # 1. full names (2+ words), contiguous, any note type
    full_idx = {}
    for p in people:
        if len(p.w) >= 2:
            full_idx.setdefault(p.w[0], []).append(p)
    for i, tok in enumerate(norm):
        if i in consumed:
            continue
        best = None
        for p in full_idx.get(tok, []):
            n = len(p.w)
            if norm[i:i + n] == p.w and (best is None or n > len(best.w)):
                best = p
        if best:
            if all(best.name != q.name for q, _ in res.people):
                res.people.append((best, "name"))
            consumed.update(range(i, i + len(best.w)))
    for p in people:                                           # multi-word aliases
        for al in p.aliases:
            aw = words(al)
            if len(aw) >= 2:
                for i in range(len(norm) - len(aw) + 1):
                    if norm[i:i + len(aw)] == aw and not (set(range(i, i + len(aw))) & consumed):
                        if all(p.name != q.name for q, _ in res.people):
                            res.people.append((p, "name"))
                        consumed.update(range(i, i + len(aw)))

    # 2. first names (need a person-like context); notes with no type count as people if the name has 2-3 words
    by_first, bare_by_word = {}, {}
    for p in people:
        if len(p.w) >= 2 and (p.type == "person" or (p.type == "none" and len(p.w) <= 3)):
            by_first.setdefault(p.w[0], []).append(p)
        elif len(p.w) == 1 and p.type in ("person", "none"):
            bare_by_word[p.w[0]] = p
    for i, t in enumerate(toks):
        orig, nrm = t[0], t[1]
        if i in consumed or not ctx or len(nrm) < 3 or not is_name_word(orig, nrm):
            continue
        cands = list(by_first.get(nrm, []))
        bare = bare_by_word.get(nrm)
        nxt = toks[i + 1] if i + 1 < len(toks) and (i + 1) not in consumed and adjacent(title, t, toks[i + 1]) else None
        if nxt:
            if len(nxt[1]) == 1 and nxt[0].isupper():                  # "Daniel J" -> surname initial
                cands = [p for p in cands if p.w[-1].startswith(nxt[1])]
                consumed.add(i + 1)
            elif is_name_word(nxt[0], nxt[1]):                          # "Rosalind Fenwick": a surname we know or not
                cands = [p for p in cands if p.w[-1] == nxt[1]]
                if not cands:
                    bare = None                                          # someone else: handled as a possible new person
        opts = cands + ([bare] if bare else [])
        if not opts:
            continue
        consumed.add(i)
        if len(opts) == 1:
            res.people.append((opts[0], "first name"))
        else:
            res.ambiguous.append((orig, opts))

    # 3. possible new people: a whole capitalised run (1-3 words) next to the user's own name, or after "with"/"w"
    runs, cur = [], []
    for i, t in enumerate(toks):
        ok = i not in consumed and is_name_word(t[0], t[1])
        if ok and cur and adjacent(title, toks[cur[-1]], t):
            cur.append(i)
        else:
            if cur:
                runs.append(cur)
            cur = [i] if ok else []
    if cur:
        runs.append(cur)
    self_idx = {i for i, n in enumerate(norm) if n in SELF_NAMES}
    for run in runs:
        if len(run) > 3:
            continue
        before = run[0] - 1
        shaped = (run[-1] + 1 in self_idx) or (before in self_idx) or (before >= 0 and norm[before] in ("with", "w"))
        if shaped:
            name = " ".join(toks[i][0] for i in run)
            if not any(name.split()[0].lower() == q.w[0] for q, _ in res.people) and name not in res.new:
                res.new.append(name)
    return res


# ── vault writes ────────────────────────────────────────────────────────────
def ensure_dir(d: Path) -> None:
    missing, cur = [], d
    while not cur.exists() and cur != cur.parent:
        missing.append(cur)
        cur = cur.parent
    d.mkdir(parents=True, exist_ok=True)
    for m in missing:
        try:
            os.chmod(m, 0o777)
        except OSError:
            pass


def write_atomic(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    tmp = path.with_name(path.name + ".cp-tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o666)
    except OSError:
        pass
    os.replace(tmp, path)


def marker(ev: Event, person: str) -> str:
    return "cal:" + hashlib.sha1(f"{ev.key}|{person}".encode()).hexdigest()[:10]


def append_person_line(person: Person, ev: Event, how: str, dry: bool) -> bool:
    f = VAULT / PEOPLE_REL / f"{person.name}.md"
    if not f.exists():
        return False
    text = f.read_text(encoding="utf-8")
    mk = marker(ev, person.name)
    if mk in text:
        return False
    when = "" if ev.all_day else ev.start.strftime(" %H:%M")
    tag = "calendar" if how == "name" else "calendar, matched by first name"
    line = f"- {ev.day.isoformat()}{when} ({tag}): {ev.title} <!--{mk}-->\n"
    if dry:
        return True
    write_atomic(f, text + ("" if text.endswith("\n") else "\n") + line)
    return True


def create_person_note(name: str, dry: bool) -> bool:
    f = VAULT / PEOPLE_REL / f"{name}.md"
    if f.exists():
        return False
    if not dry:
        write_atomic(f, f'---\ntitle: "{name}"\ntype: person\ntags:\n  - person\n---\n\n# {name}\n\n## Profile\n\n## Notes\n')
    return True


MEET_HEAD = "## 📅 Meetings (calendar)"


def daily_block(events_info: list, day: date) -> str:
    """events_info: [(Event, Match, resolved)]  for that day -> markdown block"""
    lines = []
    for ev, m, resolved in sorted(events_info, key=lambda t: t[0].start):
        who = [p.link for p, _ in m.people]
        who += [resolved[t].link for t, _ in m.ambiguous if isinstance(resolved.get(t), Person)]
        todo = [f"❓ {t}" for t, _ in m.ambiguous if not isinstance(resolved.get(t), Person) and resolved.get(t) != IGNORE]
        todo += [f"➕ {n}" for n in m.new if resolved.get("new:" + n) not in (IGNORE,) and not isinstance(resolved.get("new:" + n), Person)]
        who += [resolved["new:" + n].link for n in m.new if isinstance(resolved.get("new:" + n), Person)]
        if not (who or todo):
            continue
        when = "" if ev.all_day else ev.start.strftime("%H:%M ")
        tail = ", ".join(who)
        if todo:
            tail += (" · " if tail else "") + ", ".join(todo) + " (see [[Review/Calendar queue]])"
        lines.append(f"- {when}{ev.title} — {tail}")
    return "<!--cal-start-->\n" + "\n".join(lines) + ("\n" if lines else "") + "<!--cal-end-->"


def write_daily_block(day: date, block: str, dry: bool) -> bool:
    f = VAULT / DAILY_REL / f"{day.isoformat()}.md"
    if not f.exists():
        return False
    text = f.read_text(encoding="utf-8")
    has_block = "<!--cal-start-->" in text
    if "<!--cal-start-->\n<!--cal-end-->" == block:
        if not has_block:
            return False                                   # nothing to show, nothing to clean up
    if has_block:
        new = re.sub(r"<!--cal-start-->.*?<!--cal-end-->", lambda m: block, text, count=1, flags=re.DOTALL)
    else:
        sect = f"{MEET_HEAD}\n{block}\n\n"
        for anchor in ("## 📝 Running Notes", "## 🔗 Related"):
            if anchor in text:
                new = text.replace(anchor, sect + anchor, 1)
                break
        else:
            new = text.rstrip("\n") + "\n\n" + sect.rstrip("\n") + "\n"
    if new == text:
        return False
    if not dry:
        write_atomic(f, new)
    return True


# ── queue ───────────────────────────────────────────────────────────────────
def oid(d: dict) -> str:
    return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:8]


def render_queue(items: list, ticks: dict, banner: str, stamp: str) -> str:
    out = ["---", "type: review-queue", "tags:", "  - review", "---", "", "# Calendar queue", "",
           f"_{len(items)} open item(s). Updated {stamp}._", ""]
    if banner:
        out += [f"> ⚠️ {banner}", ""]
    out += ["Names found in calendar event titles that could not be settled automatically. Tick **one** box per item; "
            "the next scan (within ~10 minutes) applies it. Unticked items stay open. Events that name nobody in "
            "your People notes are never copied into the vault.", ""]
    if not items:
        out += ["Nothing to review. :)", ""]
    for it in items:
        out += [f"### {it['title']} <!--cq-item {it['id']}-->", ""]
        out += [f"- {e}" for e in it["evidence"]]
        out.append("")
        for o in it["options"]:
            mark = "x" if o["oid"] in ticks.get(it["id"], set()) else " "
            payload = {k: v for k, v in o.items() if k != "label"}
            out.append(f"- [{mark}] {o['label']} <!--cq {json.dumps(payload, ensure_ascii=False, sort_keys=True)} -->")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def parse_queue(text: str) -> dict:
    items, cur = {}, None
    for line in text.split("\n"):
        m = re.search(r"<!--cq-item ([0-9a-f]{8})-->", line)
        if m and line.startswith("### "):
            cur = m.group(1)
            items[cur] = {"ticked": [], "oids": set()}
            continue
        om = re.search(r"<!--cq (\{.*?\}) -->", line)
        if cur and om and re.match(r"^- \[[ xX]\]", line):
            try:
                o = json.loads(om.group(1))
            except ValueError:
                continue
            items[cur]["oids"].add(o.get("oid"))
            if re.match(r"^- \[[xX]\]", line):
                items[cur]["ticked"].append(o)
    return items


# ── state / feed ────────────────────────────────────────────────────────────
def load_state() -> dict:
    f = DATA / "state.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except ValueError:
            pass
    return {"resolved": {}, "last_fetch": 0, "fail_since": None}


def save_state(st: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "state.json").write_text(json.dumps(st, indent=2, ensure_ascii=False))


class FeedError(Exception):
    pass


def fetch_feed(state: dict, force: bool = False) -> str:
    """Latest good ICS text (cached). A revoked/unpublished link returns a redirect to an error page:
    that is reported, and the last good copy keeps being used."""
    cache = DATA / "last.ics"
    if ICS_FILE:
        return Path(ICS_FILE).read_text(encoding="utf-8", errors="replace")
    due = force or time.time() - state.get("last_fetch", 0) >= FETCH_EVERY or not cache.exists()
    if due:
        if not ICS_URL:
            raise FeedError("ICS_URL is not set")
        try:
            req = urllib.request.Request(ICS_URL, headers={"User-Agent": "calendar-people/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode("utf-8", errors="replace")
            if "BEGIN:VCALENDAR" not in body:
                raise FeedError("the calendar link no longer returns a calendar (unpublished or link rotated?)")
            DATA.mkdir(parents=True, exist_ok=True)
            cache.write_text(body, encoding="utf-8")
            os.chmod(cache, 0o600)
            state["last_fetch"] = time.time()
            state["fail_since"] = None
            log(f"calendar fetched ({len(body)} bytes)")
        except FeedError:
            raise
        except Exception as e:
            raise FeedError(f"{type(e).__name__}: {e}")
    return cache.read_text(encoding="utf-8")


# ── one cycle ───────────────────────────────────────────────────────────────
def apply_decisions(parsed: dict, state: dict, people: list, events: dict, dry: bool) -> list:
    done = []
    byname = {p.name: p for p in people}
    for iid, info in parsed.items():
        t = info["ticked"]
        if len(t) != 1:
            if len(t) > 1:
                log(f"queue item {iid}: {len(t)} boxes ticked, tick exactly one -- skipped")
            continue
        o = t[0]
        res = state["resolved"].setdefault(o["k"], {})
        if o["a"] == "pick" and o.get("p") in byname:
            res[o["t"]] = o["p"]
        elif o["a"] == "ignore":
            res[o["t"]] = IGNORE
        elif o["a"] == "create":
            name = o["n"]
            if create_person_note(name, dry):
                log(f"created People note '{name}'")
            res["new:" + name] = name
        else:
            continue
        done.append(iid)
    return done


def cycle(dry: bool) -> None:
    state = load_state()
    DATA.mkdir(parents=True, exist_ok=True)
    qpath = VAULT / QUEUE_REL
    old_text = qpath.read_text(encoding="utf-8") if qpath.exists() else ""
    parsed = parse_queue(old_text) if old_text else {}

    banner = ""
    try:
        ics = fetch_feed(state)
    except FeedError as e:
        state["fail_since"] = state.get("fail_since") or datetime.now().strftime("%Y-%m-%d %H:%M")
        banner = f"Calendar feed problem since {state['fail_since']}: {e}. Using the last good copy if there is one."
        log(banner)
        cache = DATA / "last.ics"
        if not cache.exists():
            if not dry:
                save_state(state)
                write_atomic(qpath, render_queue([], {}, banner, datetime.now().strftime("%Y-%m-%d %H:%M")))
            return
        ics = cache.read_text(encoding="utf-8")

    people = load_people()
    events = parse_ics(ics)
    now = datetime.now(home_tz())
    today = now.date()
    lo = today - timedelta(days=BACKFILL_DAYS)
    window = [e for e in events if lo <= e.day <= today + timedelta(days=1)]
    ev_by_key = {e.key: e for e in window}

    apply_decisions(parsed, state, people, ev_by_key, dry)
    people = load_people()
    byname = {p.name: p for p in people}

    # match every event in the window
    infos = []                      # (Event, Match, resolved-dict-with-Person-objects)
    queue_items = []
    n_lines = 0
    for ev in sorted(window, key=lambda e: e.start):
        if any(re.search(pat, ev.title, re.IGNORECASE) for pat in IGNORE_PATTERNS):
            continue
        m = match_event(ev.title, people)
        if m.empty():
            continue
        saved = state["resolved"].get(ev.key, {})
        resolved = {}
        for tkn, opts in m.ambiguous:
            v = saved.get(tkn)
            resolved[tkn] = IGNORE if v == IGNORE else byname.get(v) if v else None
        for n in m.new:
            v = saved.get("new:" + n)
            resolved["new:" + n] = IGNORE if v == IGNORE else byname.get(v) if v else None
        infos.append((ev, m, resolved))
        started = ev.start <= now
        if started:
            for p, how in m.people:
                n_lines += append_person_line(p, ev, how, dry)
            for tkn, opts in m.ambiguous:
                if isinstance(resolved[tkn], Person):
                    n_lines += append_person_line(resolved[tkn], ev, "name", dry)
            for n in m.new:
                if isinstance(resolved["new:" + n], Person):
                    n_lines += append_person_line(resolved["new:" + n], ev, "name", dry)
        # queue items for unresolved parts
        when = ev.start.strftime("%Y-%m-%d") + ("" if ev.all_day else ev.start.strftime(" %H:%M"))
        for tkn, opts in m.ambiguous:
            if resolved[tkn] is None:
                iid = hashlib.sha1(f"amb|{ev.key}|{tkn}".encode()).hexdigest()[:8]
                os_ = [{"a": "pick", "k": ev.key, "t": tkn, "p": p.name} for p in opts]
                labels = [f"It was {p.link}" for p in opts]
                os_.append({"a": "ignore", "k": ev.key, "t": tkn}); labels.append("None of these / not a person I know")
                queue_items.append({"id": iid, "title": f"“{tkn}” in “{ev.title}” ({when})",
                                    "evidence": [f"Several People notes could be “{tkn}”."],
                                    "options": [dict(o, oid=oid(o), label=l) for o, l in zip(os_, labels)]})
        for n in m.new:
            if resolved["new:" + n] is None:
                iid = hashlib.sha1(f"new|{ev.key}|{n}".encode()).hexdigest()[:8]
                os_ = [{"a": "create", "k": ev.key, "n": n}, {"a": "ignore", "k": ev.key, "t": "new:" + n}]
                labels = [f"Create a People note **{n}** and log this meeting", "Ignore (don't ask again)"]
                queue_items.append({"id": iid, "title": f"New person? “{n}” in “{ev.title}” ({when})",
                                    "evidence": [f"No People note matches “{n}”."],
                                    "options": [dict(o, oid=oid(o), label=l) for o, l in zip(os_, labels)]})

    # daily notes
    n_daily = 0
    by_day = {}
    for ev, m, r in infos:
        by_day.setdefault(ev.day, []).append((ev, m, r))
    for d in {e.day for e in window} | set(by_day):
        if d > today:
            continue
        if write_daily_block(d, daily_block(by_day.get(d, []), d), dry):
            n_daily += 1

    # queue file: rewrite only when the set of items changed (no churn while the user edits it)
    queue_items.sort(key=lambda it: it["title"])
    ticks = {iid: {o["oid"] for o in info["ticked"]} for iid, info in parsed.items()}
    new_sig = {(it["id"], tuple(sorted(o["oid"] for o in it["options"]))) for it in queue_items}
    old_sig = {(iid, tuple(sorted(info["oids"]))) for iid, info in parsed.items()}
    banner_changed = bool(banner) != ("⚠️" in old_text)
    if dry:
        log(f"(dry run) {len(window)} events in window; {len(infos)} name someone; "
            f"{n_lines} person-note line(s) would be added; {n_daily} daily note(s) would change")
        for ev, m, r in infos:
            parts = [f"{p.name} ({how})" for p, how in m.people] + [f"? {t}→{'/'.join(p.name for p in o)}" for t, o in m.ambiguous] + [f"new: {n}" for n in m.new]
            print(f"   {ev.start.strftime('%m-%d %H:%M')}  {ev.title[:55]:55} -> {'; '.join(parts)}")
        return
    if new_sig != old_sig or banner_changed or not old_text:
        write_atomic(qpath, render_queue(queue_items, ticks, banner, datetime.now().strftime("%Y-%m-%d %H:%M")))
        log(f"queue written: {len(queue_items)} open item(s)")
    save_state(state)
    log(f"cycle done: {len(window)} events in window, {len(infos)} name someone, {n_lines} line(s) added, {n_daily} daily note(s) updated")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    log(f"calendar-people: vault={VAULT} home_tz={HOME_TZ} {'(dry run)' if a.dry_run else ''}")
    if a.loop and not a.dry_run:
        while True:
            try:
                cycle(False)
            except Exception as e:
                log(f"cycle failed: {type(e).__name__}: {e}")
            time.sleep(INTERVAL)
    else:
        cycle(a.dry_run)


if __name__ == "__main__":
    main()
