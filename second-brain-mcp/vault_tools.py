"""
vault_tools.py
--------------
Structured, filesystem-based answers that semantic search cannot give (added 2026-10-15):
  open_tasks(root, ...)       open checkbox tasks across the vault, due-date aware
  recent_captures(root, ...)  what was recently captured (LinkedIn, Bluesky, Boox), by date

Pure functions over a vault root (read-only), so they can be tested against a temp directory.
Same exclusions as the MCP server's read tools (credentials folder, dot-folders).
"""
import re
from datetime import date, datetime, timedelta
from pathlib import Path

EXCLUDED = ("personal/networking", "google drive", "archive")
TASK_DIRS = ("Projects", "Inbox/Boox notes", "Resources/People", "Daily")
_TASK = re.compile(r"^\s*- \[ \] (.*)$")
_DUE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")
_CREATED = re.compile(r"➕\s*(\d{4}-\d{2}-\d{2})")
_MARVIN = re.compile(r"\s*\[marvin:[A-Za-z0-9]+\]")
_HEAD = re.compile(r"^## (\d{4}-\d{2}-\d{2}) — (.*)$")


def _excluded(rel: Path) -> bool:
    if any(p.startswith(".") for p in rel.parts):
        return True
    low = rel.as_posix().lower()
    return any(low == e or low.startswith(e + "/") or ("/" + e + "/") in ("/" + low) for e in EXCLUDED)


def _md_files(root: Path, folder: str):
    base = root / folder
    if not base.is_dir():
        return
    for f in base.rglob("*.md"):
        if f.is_file() and not f.is_symlink() and not _excluded(f.relative_to(root)):
            yield f


def open_tasks(root, contains: str = "", due_within_days: int = 0, limit: int = 60, today: date = None) -> str:
    root = Path(root)
    today = today or date.today()
    needle = contains.strip().lower()
    rows = []
    for folder in TASK_DIRS:
        for f in _md_files(root, folder):
            try:
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for ln in lines:
                m = _TASK.match(ln)
                if not m:
                    continue
                text = _MARVIN.sub("", m.group(1)).strip()
                if len(re.sub(r"[📅➕✅⏫🔼🔽\d\-\s]", "", text)) < 3:           # empty / date-only stubs
                    continue
                if needle and needle not in text.lower():
                    continue
                d = _DUE.search(text)
                due = datetime.strptime(d.group(1), "%Y-%m-%d").date() if d else None
                if due_within_days and (due is None or due > today + timedelta(days=due_within_days)):
                    continue
                c = _CREATED.search(text)
                rows.append((due or date.max, c.group(1) if c else "", text, f.relative_to(root).with_suffix("").as_posix(), due))
    rows.sort(key=lambda r: (r[0], r[1]))
    if not rows:
        return "No open tasks found."
    out = []
    for _, created, text, rel, due in rows[:max(1, min(limit, 200))]:
        flag = " [OVERDUE]" if due and due < today else ""
        out.append(f"- {text}  ({rel}){flag}")
    more = f"\n[... {len(rows) - len(out)} more; narrow with contains or due_within_days]" if len(rows) > len(out) else ""
    return f"{len(rows)} open task(s), soonest due first (undated last):\n" + "\n".join(out) + more


def _fm(text: str, key: str) -> str:
    m = re.search(rf"^{key}:[ \t]*(.*)$", text, re.M)
    return m.group(1).strip().strip('"') if m else ""


def _first_line(text: str, heading: str) -> str:
    m = re.search(rf"^## {heading}\s*\n+(.*?)(?=\n\s*\n|\Z)", text, re.M | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:200] if m else ""


def recent_captures(root, days: int = 7, source: str = "", limit: int = 40, today: date = None) -> str:
    """Notes captured in the last `days` days from linkedin, bluesky or boox, newest first.
    Dates come from the note itself (frontmatter `date`, `## YYYY-MM-DD — Author` log headings,
    '- YYYY-MM-DD HH:MM (handwritten, Boox' person-note entries), not file modification times."""
    root = Path(root)
    today = today or date.today()
    cutoff = today - timedelta(days=max(1, days))
    want = source.strip().lower()
    items = []   # (date, source, line)

    def add(d, src, line):
        if d and d >= cutoff.isoformat() and (not want or want == src):
            items.append((d, src, line))

    for src, base in (("linkedin", "Resources/LinkedIn Posts"), ("bluesky", "Resources/Bluesky")):
        if want and want != src:
            continue
        for f in _md_files(root, base):
            t = f.read_text(encoding="utf-8", errors="replace")
            rel = f.relative_to(root).with_suffix("").as_posix()
            if "/Reading List/" in rel:
                acts = _fm(t, "actions").strip("[]")
                summ = _first_line(t, "Summary")
                add(_fm(t, "date"), src, f"{_fm(t, 'title') or f.stem}  [{acts}]  [[{rel}]]" + (f"\n      {summ}" if summ else ""))
            elif "/Activity/" in rel:
                parts = re.split(r"(?m)^(?=## \d{4}-\d{2}-\d{2} — )", t)
                for sec in parts[1:]:
                    hm = _HEAD.match(sec.split("\n", 1)[0])
                    if not hm:
                        continue
                    acts = (re.search(r"\*\*Actions:\*\*\s*(.*)", sec) or [None, ""])[1].strip()
                    body = [l for l in sec.split("\n")[1:] if l.strip() and not l.startswith(("**", ">", "Tags:", "related:", "---"))]
                    add(hm.group(1), src, f"{hm.group(2)}  [{acts}]  [[{rel}#{hm.group(1)} — {hm.group(2)}]]" + (f"\n      {body[0][:200]}" if body else ""))
    if not want or want == "boox":
        for f in _md_files(root, "Resources/People"):
            t = f.read_text(encoding="utf-8", errors="replace")
            if "(handwritten, Boox" not in t:
                continue
            rel = f.relative_to(root).with_suffix("").as_posix()
            for m in re.finditer(r"(?m)^- (\d{4}-\d{2}-\d{2}) \d{2}:\d{2} \(handwritten, Boox[^)]*\)[^:\n]*:\s*(.*)$", t):
                add(m.group(1), "boox", f"Handwritten note, {f.stem}  [[{rel}]]\n      {re.sub(r'#+ ', '', m.group(2))[:160]}")
    if not items:
        return f"No captures in the last {days} day(s)" + (f" from {want}" if want else "") + "."
    items.sort(key=lambda x: x[0], reverse=True)
    out = [f"{d}  [{s}]  {line}" for d, s, line in items[:max(1, min(limit, 100))]]
    more = f"\n[... {len(items) - len(out)} more; narrow with source or days]" if len(items) > len(out) else ""
    return f"{len(items)} capture(s) in the last {days} day(s), newest first:\n" + "\n".join(out) + more
