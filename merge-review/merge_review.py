#!/usr/bin/env python3
"""merge_review.py -- duplicate-person review queue + guarded merge for an Obsidian vault.

What it does
  1. SWEEP   Finds likely duplicate People notes (same name after cleaning, one-letter typos,
             names polluted by "(ORG)" tags or trailing counters) and bare first-name notes
             ("Matilda") that could belong to several people.
  2. QUEUE   Writes them to  Review/Merge queue.md  as checkbox items with the evidence.
             NOTHING is ever merged without a ticked box.
  3. APPLY   On the next cycle, a ticked option is carried out: the source note's content is
             appended to the target (never overwritten), the old name becomes an alias, links
             across the vault are re-pointed, and the source note is moved (not deleted) to
             Review/merged/.  Every touched file is backed up first, so it can be undone.

Bare first-name notes ("Matilda") are a deliberate *holding area*: they are only listed with
evidence and never merged unless you tick a specific option.

stdlib only.   Usage:  merge_review.py [--once | --loop] [--dry-run]
Env:    VAULT (/vault)   DATA (/data)   INTERVAL seconds between cycles (600)
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

VAULT = Path(os.environ.get("VAULT", "/vault"))
DATA = Path(os.environ.get("DATA", "/data"))
INTERVAL = int(os.environ.get("INTERVAL", "600"))

PEOPLE_REL = "Resources/People"
QUEUE_REL = "Review/Merge queue.md"
LOG_REL = "Review/Merge log.md"
MERGED_REL = "Review/merged"

# never read, never rewritten
EXCLUDE_TOP = {".obsidian", ".trash", ".Trash", ".stfolder", ".stversions", "Personal", "Google Drive",
               "Templates", "Apple notes", "Images", "Review"}
MAX_REWRITES = 300            # abort a merge that would touch more files than this
MAX_APPLY_PER_CYCLE = 5
STOPWORDS = set("""about after again also another because before between could during every from have into
just like more most much only other over said same should since some such than that their them then there
these they this those through under very want were what when where which while will with would your""".split())

FM_RE = re.compile(r"\A---\n(.*?)\n---[ \t]*\n?", re.DOTALL)
CODE_RE = re.compile(r"(```.*?```|~~~.*?~~~|`[^`\n]*`)", re.DOTALL)
ENTRY_RE = re.compile(r"^\s*- \d{4}-\d{2}-\d{2}", re.MULTILINE)
WIKILINK_RE = re.compile(r"!?\[\[([^\]|#\n]+)(?:#[^\]|\n]*)?(?:\|[^\]\n]*)?\]\]")
MQ_ITEM_RE = re.compile(r"<!--mq-item ([0-9a-f]{8})-->")
MQ_OPT_RE = re.compile(r"<!--mq (\{.*?\}) -->")


def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}", flush=True)


# ── name handling ───────────────────────────────────────────────────────────
def strip_accents(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def clean_display(name: str) -> str:
    """'Olivia Courtney (FXYZ)' -> 'Olivia Courtney'; 'Francesca Bosco 1' -> 'Francesca Bosco'."""
    n = re.sub(r"\([^)]*\)", " ", name)
    n = re.sub(r"[\s_\-]*\d+\s*$", "", n)
    n = re.sub(r"\s+", " ", n).strip(" -_.,")
    return n or name.strip()


def norm_name(name: str) -> str:
    n = strip_accents(clean_display(name)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", n)).strip()


# ── frontmatter helpers ─────────────────────────────────────────────────────
def split_fm(text: str):
    m = FM_RE.match(text)
    if not m:
        return "", text
    return m.group(1), text[m.end():]


def fm_value(fm: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:[ \t]*(.*)$", fm, re.MULTILINE)
    return m.group(1).strip().strip('"\'') if m else ""


def get_aliases(fm: str) -> list:
    m = re.search(r"^aliases:[ \t]*(.*)$", fm, re.MULTILINE)
    if not m:
        return []
    rest = m.group(1).strip()
    if rest.startswith("["):
        return [a.strip().strip('"\'') for a in rest.strip("[]").split(",") if a.strip()]
    out = []
    for line in fm[m.end():].split("\n")[1:]:
        lm = re.match(r"^[ \t]+-[ \t]+(.*)$", line)
        if not lm:
            break
        out.append(lm.group(1).strip().strip('"\''))
    return out


def yaml_item(s: str) -> str:
    return json.dumps(s, ensure_ascii=False) if re.search(r"[:#\[\]{},&*!|>'\"%@`]|^\s|\s$", s) else s


def add_aliases(text: str, names: list) -> str:
    fm, body = split_fm(text)
    existing = get_aliases(fm)
    merged = list(existing)
    for n in names:
        if n and n.lower() not in {a.lower() for a in merged}:
            merged.append(n)
    if merged == existing:
        return text
    block = "aliases:\n" + "".join(f"  - {yaml_item(a)}\n" for a in merged)
    if not fm:
        return f"---\n{block}---\n\n{text}"
    new_fm = re.sub(r"^aliases:[ \t]*\n(?:[ \t]+-[ \t]+.*\n?)*", "", fm + "\n", flags=re.MULTILINE)
    new_fm = re.sub(r"^aliases:.*\n", "", new_fm, flags=re.MULTILINE).rstrip("\n")
    m = re.search(r"^type:.*$", new_fm, re.MULTILINE)
    if m:
        new_fm = new_fm[:m.end()] + "\n" + block.rstrip("\n") + new_fm[m.end():]
    else:
        new_fm += "\n" + block.rstrip("\n")
    return f"---\n{new_fm}\n---\n{body}"


# ── vault access ────────────────────────────────────────────────────────────
def ensure_dir(d: Path) -> None:
    """mkdir -p, and make every directory it creates world-writable (Syncthing runs as another uid;
    a root-owned 755 Review/ folder would silently stop the user's ticked boxes syncing back)."""
    missing = []
    cur = d
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
    tmp = path.with_name(path.name + ".mr-tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o666)      # Syncthing (another uid) must be able to write Mac-side edits back
    except OSError:
        pass
    os.replace(tmp, path)


def iter_notes():
    """Every .md the script may read/rewrite (mounted, non-excluded)."""
    if not VAULT.exists():
        return
    for root, dirs, files in os.walk(VAULT):
        rel = Path(root).relative_to(VAULT)
        if rel.parts and rel.parts[0] in EXCLUDE_TOP:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not (not rel.parts and d in EXCLUDE_TOP) and not d.startswith(".")]
        for f in files:
            if f.endswith(".md") and ".mr-tmp" not in f:
                yield Path(root) / f


class Person:
    def __init__(self, path: Path):
        self.path = path
        self.name = path.stem
        text = path.read_text(encoding="utf-8", errors="replace")
        fm, body = split_fm(text)
        self.type = fm_value(fm, "type") or "none"
        self.aliases = get_aliases(fm)
        self.body = body
        self.entries = len(ENTRY_RE.findall(body))
        self.size = len(text.encode("utf-8"))
        self.norm = norm_name(self.name)
        self.links = 0

    def desc(self) -> str:
        return f"{self.entries} dated entr{'y' if self.entries == 1 else 'ies'}, {self.links} inbound link{'' if self.links == 1 else 's'}, {self.size} bytes"


def read_people() -> list:
    pdir = VAULT / PEOPLE_REL
    if not pdir.exists():
        return []
    return [Person(p) for p in sorted(pdir.glob("*.md"))]


def count_inbound(people: list) -> None:
    by_base = {p.name.lower(): p for p in people}
    for note in iter_notes():
        try:
            text = note.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for part in CODE_RE.split(text):
            if part.startswith(("`", "~~~")):
                continue
            for m in WIKILINK_RE.finditer(part):
                base = m.group(1).strip().split("/")[-1].lower()
                p = by_base.get(base)
                if p and note != p.path:
                    p.links += 1


def link_rewrite(text: str, old: str, new: str):
    """Re-point [[People/old]] / [[Resources/People/old]] / [[old]] links (any #heading / |alias
    kept) at `new`. Code spans/blocks untouched. Returns (new_text, n_replacements)."""
    pat = re.compile(r"(!?\[\[)((?:Resources/People/|People/)?)" + re.escape(old) + r"((?:#[^\]|\n]*)?(?:\|[^\]\n]*)?\]\])",
                     re.IGNORECASE)
    n = 0
    out = []
    for part in CODE_RE.split(text):
        if part.startswith(("`", "~~~")):
            out.append(part)
            continue
        part, k = pat.subn(lambda m: f"{m.group(1)}{m.group(2)}{new}{m.group(3)}", part)
        n += k
        out.append(part)
    return "".join(out), n


# ── sweep ───────────────────────────────────────────────────────────────────
def item_id(*parts) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:8]


def opt(action: str, label_md: str, **kw) -> dict:
    d = {"a": action, **kw}
    d["oid"] = hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:8]
    d["label"] = label_md
    return d


def plink(p: Person) -> str:
    return f"[[{PEOPLE_REL}/{p.name}|{p.name}]]"


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (substitutions/insertions/deletions)."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def is_typo_pair(a_norm: str, b_norm: str) -> bool:
    """Same number of words, all but one identical, and the differing word is a long-enough word
    within 2 edits (so 'Vandermeer'/'Vandermeir' match but 'Tim'/'Tom' and 'Stephen Burt'/'Stephen
    Barrett' do not)."""
    ta, tb = a_norm.split(), b_norm.split()
    if len(ta) != len(tb) or len(ta) < 2:
        return False
    diff = [(x, y) for x, y in zip(ta, tb) if x != y]
    if len(diff) != 1:
        return False
    x, y = diff[0]
    return min(len(x), len(y)) >= 5 and edit_distance(x, y) <= 2


def sig_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{5,}", strip_accents(text).lower()) if w not in STOPWORDS}


def sweep(people: list, state: dict) -> list:
    """Returns queue items (dicts). Pure function of the vault + decisions already taken."""
    decided = set(state.get("kept", []))
    byname = {p.name: p for p in people}
    items = []
    used_sources = set()

    # A. same name once cleaned (Francesca Bosco / Francesca Bosco 1, accents, case)
    groups = {}
    for p in people:
        if p.norm:
            groups.setdefault(p.norm, []).append(p)
    for norm, grp in groups.items():
        if len(grp) < 2:
            continue
        clean = [p for p in grp if p.name == clean_display(p.name)]
        target = (max(clean, key=lambda p: (p.entries, p.size)) if clean
                  else max(grp, key=lambda p: (p.entries, p.size)))
        for s in grp:
            if s is target:
                continue
            iid = item_id("dup", s.name, target.name)
            if iid in decided:
                continue
            used_sources.add(s.name)
            items.append({
                "id": iid, "kind": "same name", "rank": 0,
                "title": f"{s.name}  →  {target.name}",
                "evidence": [f"Same person once the name is cleaned (\"{s.name}\" vs \"{target.name}\").",
                             f"{plink(s)}: {s.desc()}", f"{plink(target)}: {target.desc()}"],
                "options": [opt("merge", f"Merge {plink(s)} into {plink(target)}", s=s.name, t=target.name)],
            })

    # B. near-identical names (one-letter typos) between two people
    plist = [p for p in people if p.type in ("person", "none") and p.norm and len(p.norm.split()) >= 2]
    for i, a in enumerate(plist):
        for b in plist[i + 1:]:
            if a.norm == b.norm or a.name in used_sources or b.name in used_sources:
                continue
            if not is_typo_pair(a.norm, b.norm):
                continue
            r = difflib.SequenceMatcher(None, a.norm, b.norm).ratio()
            big, small = sorted((a, b), key=lambda p: (-p.entries, -p.size, p.name))
            iid = item_id("typo", *sorted([a.name, b.name]))
            if iid in decided:
                continue
            items.append({
                "id": iid, "kind": "possible typo", "rank": 1,
                "title": f"{a.name}  /  {b.name}",
                "evidence": [f"The names differ by only a couple of letters ({int(r * 100)}% identical), so one may be a misspelling of the other.",
                             f"{plink(a)}: {a.desc()}", f"{plink(b)}: {b.desc()}"],
                "options": [opt("merge", f"Merge {plink(small)} into {plink(big)}", s=small.name, t=big.name),
                            opt("merge", f"Merge {plink(big)} into {plink(small)}", s=big.name, t=small.name),
                            opt("keep", "Keep separate (don't ask again)", id=iid)],
            })

    # C. names polluted with a (TAG) or counter and no clean twin -> rename
    for p in people:
        c = clean_display(p.name)
        polluted = bool(re.search(r"\([^)]*\)\s*$", p.name) or re.search(r"\s+\d+\s*$", p.name))
        if (polluted and p.type in ("person", "none") and len(c.split()) <= 3 and c != p.name
                and p.name not in used_sources and c and not (VAULT / PEOPLE_REL / f"{c}.md").exists()):
            iid = item_id("rename", p.name, c)
            if iid in decided:
                continue
            items.append({
                "id": iid, "kind": "rename", "rank": 2,
                "title": f"{p.name}  →  {c}",
                "evidence": [f"The name carries a tag or counter (\"{p.name}\"); a clean name would be \"{c}\". "
                             f"The old name is kept as an alias and every link is re-pointed.",
                             f"{plink(p)}: {p.desc()}"],
                "options": [opt("rename", f"Rename {plink(p)} to **{c}**", s=p.name, t=c),
                            opt("keep", "Keep the current name (don't ask again)", id=iid)],
            })

    # D. bare first-name notes: a holding area, listed with evidence, never auto-merged
    for p in people:
        if len(p.norm.split()) != 1 or p.type not in ("person", "none") or p.name in used_sources:
            continue
        cands = [q for q in people if q is not p and q.type == "person" and len(q.norm.split()) >= 2
                 and q.norm.split()[0] == p.norm]
        if not cands:
            continue
        iid = item_id("bare", p.name)
        if iid in decided:
            continue
        mine = sig_words(p.body)
        ev = [f"Bare-name note {plink(p)} ({p.desc()}). This is a holding area: it is only merged if you "
              f"tick a specific person below."]
        opts = []
        scored = []
        for q in cands:
            shared = sorted(mine & sig_words(q.body))
            scored.append((len(shared), q, shared))
        scored.sort(key=lambda t: (-t[0], t[1].name))
        for score, q, shared in scored[:6]:
            alias_note = " (already has the alias \"%s\")" % p.name if p.name.lower() in {a.lower() for a in q.aliases} else ""
            words = ", ".join(shared[:4]) if shared else "none"
            ev.append(f"Candidate {plink(q)}{alias_note}: shares {score} content word{'' if score == 1 else 's'} ({words}); {q.desc()}")
            opts.append(opt("merge", f"Merge {plink(p)} into {plink(q)}", s=p.name, t=q.name))
        opts.append(opt("keep", "Keep as a holding area (don't ask again)", id=iid))
        items.append({"id": iid, "kind": "bare name", "rank": 3, "title": f"{p.name}  (which one?)",
                      "evidence": ev, "options": opts})

    items.sort(key=lambda it: (it["rank"], it["title"].lower()))
    return items


# ── queue file ──────────────────────────────────────────────────────────────
def render_queue(items: list, ticks: dict, stamp: str) -> str:
    out = ["---", "type: review-queue", "tags:", "  - review", "---", "",
           "# Merge queue", "",
           f"_{len(items)} open item(s). Updated {stamp}._", "",
           "**How this works.** Tick **one** box per item. The next scan (within ~10 minutes) carries it out: "
           "the old note's content is appended to the target (nothing overwritten), the old name becomes an alias, "
           "links are re-pointed, and the old note is moved to `Review/merged/` (not deleted). Every touched file is "
           "backed up first, and each action is recorded in [[Review/Merge log]]. Leave an item unticked to decide "
           "later; \"Keep separate\" stops it being asked again.", ""]
    if not items:
        out += ["Nothing to review. :)", ""]
    for it in items:
        out += [f"### {it['title']} · {it['kind']} <!--mq-item {it['id']}-->", ""]
        out += [f"- {e}" if not e.startswith("-") else e for e in it["evidence"]]
        out.append("")
        for o in it["options"]:
            mark = "x" if o["oid"] in ticks.get(it["id"], set()) else " "
            payload = {k: v for k, v in o.items() if k not in ("label",)}
            out.append(f"- [{mark}] {o['label']} <!--mq {json.dumps(payload, ensure_ascii=False, sort_keys=True)} -->")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def parse_queue(text: str) -> dict:
    """{item_id: {"ticked": [option dicts], "options": {oid}}}"""
    items = {}
    cur = None
    for line in text.split("\n"):
        m = MQ_ITEM_RE.search(line)
        if m and line.startswith("### "):
            cur = m.group(1)
            items[cur] = {"ticked": [], "oids": set()}
            continue
        if cur is None:
            continue
        om = MQ_OPT_RE.search(line)
        if om and re.match(r"^- \[[ xX]\]", line):
            try:
                o = json.loads(om.group(1))
            except ValueError:
                continue
            items[cur]["oids"].add(o.get("oid"))
            if re.match(r"^- \[[xX]\]", line):
                items[cur]["ticked"].append(o)
    return items


# ── applying decisions ──────────────────────────────────────────────────────
def backup_files(label: str, files: list) -> Path:
    d = DATA / "backups" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^A-Za-z0-9_-]+', '_', label)[:60]}"
    for f in files:
        if f.exists():
            dest = d / f.relative_to(VAULT)
            ensure_dir(dest.parent)
            shutil.copy2(f, dest)
    return d


def demote(body: str, levels: int = 2) -> str:
    return "\n".join(("#" * levels + l) if l.startswith("#") else l for l in body.split("\n"))


def merged_block(src: Person, today: str) -> str:
    body = src.body
    body = re.sub(r"\A\s*# .*\n", "", body, count=1)                               # drop the H1
    body = re.sub(r"^## Profile[ \t]*\n(?=\s*(?:## |\Z))", "", body, flags=re.MULTILINE)   # empty Profile heading
    body = body.strip("\n")
    return f'\n\n### Merged from "{src.name}" ({today})\n\n{demote(body)}\n'


def plan_rewrites(old: str, new: str, exclude: set) -> list:
    plan = []
    for note in iter_notes():
        if note in exclude:
            continue
        try:
            text = note.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        new_text, n = link_rewrite(text, old, new)
        if n:
            plan.append((note, new_text, n))
    return plan


def do_merge(s_name: str, t_name: str, dry: bool) -> str:
    pdir = VAULT / PEOPLE_REL
    s_path, t_path = pdir / f"{s_name}.md", pdir / f"{t_name}.md"
    if s_name == t_name or not s_path.exists() or not t_path.exists():
        return f"skipped (source or target missing): {s_name} -> {t_name}"
    src, tgt = Person(s_path), Person(t_path)
    today = datetime.now().strftime("%Y-%m-%d")
    plan = plan_rewrites(s_name, t_name, exclude={s_path})
    if len(plan) > MAX_REWRITES:
        return f"ABORTED (would rewrite {len(plan)} files > {MAX_REWRITES}): {s_name} -> {t_name}"
    summary = f"merge '{s_name}' into '{t_name}': {sum(n for _, _, n in plan)} link(s) in {len(plan)} note(s)"
    if dry:
        return "(dry run) would " + summary

    bdir = backup_files(f"{s_name}__into__{t_name}", [s_path, t_path] + [p for p, _, _ in plan])
    t_text = t_path.read_text(encoding="utf-8")
    new_t = t_text.rstrip("\n") + merged_block(src, today)
    new_t = add_aliases(new_t, [s_name] + [a for a in src.aliases if a.lower() != t_name.lower()])
    try:
        write_atomic(t_path, new_t)
        for note, text, _ in plan:
            if note == t_path:
                continue
            write_atomic(note, text)
        # T's own links to S (if any) were planned against the old T text; reapply on the new text
        fixed, _ = link_rewrite(new_t, s_name, t_name)
        if fixed != new_t:
            write_atomic(t_path, fixed)
        dest = VAULT / MERGED_REL / f"{s_name} (merged {today}).md"
        ensure_dir(dest.parent)
        if dest.exists():
            dest = dest.with_name(f"{s_name} (merged {today} {datetime.now().strftime('%H%M%S')}).md")
        shutil.move(str(s_path), str(dest))
        try:
            os.chmod(dest, 0o666)
        except OSError:
            pass
    except Exception as e:                                         # restore everything touched
        for f in [s_path, t_path] + [p for p, _, _ in plan]:
            b = bdir / f.relative_to(VAULT)
            if b.exists():
                shutil.copy2(b, f)
        return f"FAILED ({e}); restored from {bdir.name}: {summary}"
    return f"{summary}; backup {bdir.name}"


def do_rename(s_name: str, new_name: str, dry: bool) -> str:
    pdir = VAULT / PEOPLE_REL
    s_path, n_path = pdir / f"{s_name}.md", pdir / f"{new_name}.md"
    if not s_path.exists():
        return f"skipped (missing): {s_name}"
    if n_path.exists():
        return f"skipped (a note named '{new_name}' already exists): {s_name}"
    plan = plan_rewrites(s_name, new_name, exclude={s_path})
    if len(plan) > MAX_REWRITES:
        return f"ABORTED (would rewrite {len(plan)} files): {s_name} -> {new_name}"
    summary = f"rename '{s_name}' to '{new_name}': {sum(n for _, _, n in plan)} link(s) in {len(plan)} note(s)"
    if dry:
        return "(dry run) would " + summary
    bdir = backup_files(f"{s_name}__rename__{new_name}", [s_path] + [p for p, _, _ in plan])
    text = s_path.read_text(encoding="utf-8")
    text = re.sub(r'^title:[ \t]*.*$', f'title: "{new_name}"', text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^# " + re.escape(s_name) + r"[ \t]*$", f"# {new_name}", text, count=1, flags=re.MULTILINE)
    text = add_aliases(text, [s_name])
    try:
        write_atomic(n_path, text)
        for note, t, _ in plan:
            write_atomic(note, t)
        s_path.unlink()
    except Exception as e:
        for f in [s_path] + [p for p, _, _ in plan]:
            b = bdir / f.relative_to(VAULT)
            if b.exists():
                shutil.copy2(b, f)
        if n_path.exists():
            n_path.unlink()
        return f"FAILED ({e}); restored from {bdir.name}: {summary}"
    return f"{summary}; backup {bdir.name}"


def append_log(lines: list) -> None:
    p = VAULT / LOG_REL
    head = "---\ntype: review-log\n---\n\n# Merge log\n\nNewest last. Backups live on the NAS in `merge-review/data/backups/`.\n\n"
    cur = p.read_text(encoding="utf-8") if p.exists() else head
    write_atomic(p, cur.rstrip("\n") + "\n" + "\n".join(f"- {datetime.now().strftime('%Y-%m-%d %H:%M')} {l}" for l in lines) + "\n")


# ── cycle ───────────────────────────────────────────────────────────────────
def load_state() -> dict:
    f = DATA / "state.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except ValueError:
            pass
    return {"kept": [], "history": []}


def save_state(st: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "state.json").write_text(json.dumps(st, indent=2, ensure_ascii=False))


def cycle(dry: bool) -> None:
    state = load_state()
    qpath = VAULT / QUEUE_REL
    old_text = qpath.read_text(encoding="utf-8") if qpath.exists() else ""
    parsed = parse_queue(old_text) if old_text else {}

    # 1. carry out decisions
    log_lines = []
    applied = 0
    cleared = set()          # items whose action could not be carried out: unticked, not retried
    for iid, info in parsed.items():
        t = info["ticked"]
        if not t:
            continue
        if len(t) > 1:
            log(f"item {iid}: {len(t)} boxes ticked, tick exactly one -- skipped")
            continue
        if applied >= MAX_APPLY_PER_CYCLE:
            log("apply limit reached for this cycle; rest next cycle")
            break
        o = t[0]
        if o["a"] == "keep":
            msg = f"kept separate: item {iid}"
            if not dry:
                state["kept"] = sorted(set(state.get("kept", [])) | {o.get("id", iid)})
        elif o["a"] == "merge":
            msg = do_merge(o["s"], o["t"], dry)
        elif o["a"] == "rename":
            msg = do_rename(o["s"], o["t"], dry)
        else:
            continue
        applied += 1
        log(msg)
        log_lines.append(msg)
        if not dry and not msg.startswith(("skipped", "ABORTED", "FAILED")):
            state.setdefault("history", []).append({"ts": datetime.now().isoformat(timespec="seconds"), "item": iid, "result": msg})
        if msg.startswith(("skipped", "ABORTED", "FAILED")):
            cleared.add(iid)
    if log_lines and not dry:
        append_log(log_lines)
        save_state(state)

    # 2. re-sweep and (only if the set of items changed) rewrite the queue
    people = read_people()
    count_inbound(people)
    items = sweep(people, state)
    ticks = {iid: {o["oid"] for o in info["ticked"]} for iid, info in parsed.items() if iid not in cleared}
    new_sig = {(it["id"], tuple(sorted(o["oid"] for o in it["options"]))) for it in items}
    old_sig = {(iid, tuple(sorted(info["oids"]))) for iid, info in parsed.items()}
    if new_sig == old_sig and old_text and not log_lines and not cleared:
        log(f"queue unchanged ({len(items)} open item(s))")
        return
    text = render_queue(items, ticks, datetime.now().strftime("%Y-%m-%d %H:%M"))
    if dry:
        log(f"(dry run) queue would contain {len(items)} item(s):")
        for it in items:
            print(f"   [{it['kind']}] {it['title']}")
        return
    write_atomic(qpath, text)
    log(f"queue written: {len(items)} open item(s)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    log(f"merge-review: vault={VAULT} data={DATA} {'(dry run)' if a.dry_run else ''}")
    if a.loop and not a.dry_run:
        while True:
            try:
                cycle(False)
            except Exception as e:                                  # never die: retry next cycle
                log(f"cycle failed: {type(e).__name__}: {e}")
            time.sleep(INTERVAL)
    else:
        cycle(a.dry_run)


if __name__ == "__main__":
    main()
