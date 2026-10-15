import importlib.util, os, re, shutil, sys, tempfile, json
from datetime import datetime, timedelta, timezone
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="cptest_"))
V, D = T / "vault", T / "data"
os.environ.update(VAULT=str(V), DATA=str(D), HOME_TZ="America/New_York", SELF_NAMES="Emrys", BACKFILL_DAYS="7")
spec = importlib.util.spec_from_file_location("cp", str(Path(__file__).parent / "calendar_people.py"))
cp = importlib.util.module_from_spec(spec); sys.modules[spec.name] = cp; spec.loader.exec_module(cp)

ok = True
def check(label, cond):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

def w(rel, text):
    p = V / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text, encoding="utf-8"); return p

def person(name, typ="person", aliases=""):
    return w(f"Resources/People/{name}.md", f'---\ntitle: "{name}"\ntype: {typ}\n{aliases}tags:\n  - person\n---\n\n# {name}\n\n## Profile\n\n## Notes\n- 2026-01-01: existing line\n')

# ---- people fixture ----------------------------------------------------------
for n in ["Clara Meadows", "Marguerite Harlow", "Yves Hartley", "Martin Rowe", "Rosalind MacKay",
          "Daniel Eastwood", "Daniel Jarvis", "Daniel Pellegrini", "Alan Dale", "Alan Lowe", "Usha Anand",
          "Elodie Fournier-Teller", "Sam Dale"]:
    person(n)
person("Swiss Data Space Forum", typ="organization"); person("Global Governance Centre", typ="organization")
w("Resources/People/Matilda.md", "---\ntitle: \"Matilda\"\ntype: person\ntags:\n  - person\n---\n\n# Matilda\n\n## Notes\n")
person("Rosa Dunmore", aliases="aliases:\n  - Rosa\n"); person("Rosa Alva")
w("Resources/People/Rosa.md", "---\ntitle: \"Rosa\"\ntype: person\n---\n\n# Rosa\n\n## Notes\n")
w("Resources/People/Ethan Stub.md", "@people #people\n\nsomething\n")          # no frontmatter
w("Resources/People/Zara Quinn.md", "@people #people\n\nstub without a type\n")
w("Resources/People/Association for Responsible AI (ARAI).md", "org stub\n")

# ---- helper: synthetic ICS ----------------------------------------------------
home = cp.home_tz()
now = datetime.now(home)
today = now.date()
def ics_dt(dt, tzid=None):
    if tzid == "Z": return f"DTSTART:{dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    if tzid:
        return f"DTSTART;TZID={tzid}:{dt.strftime('%Y%m%dT%H%M%S')}"
    return f"DTSTART:{dt.strftime('%Y%m%dT%H%M%S')}"
def vevent(uid, title, dt, tzid="Eastern Standard Time", extra=""):
    return f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{title}\r\n{ics_dt(dt, tzid)}\r\n{extra}END:VEVENT\r\n"
def cal(*events): return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(events) + "END:VCALENDAR\r\n"
def at(days_ago, hour, minute=0):
    d = today - timedelta(days=days_ago)
    return datetime(d.year, d.month, d.day, hour, minute)          # naive wall time in home tz

# ---- 1. matching -------------------------------------------------------------
people = cp.load_people()
def m(title): return cp.match_event(title, people)
def names(r): return sorted(p.name for p, _ in r.people)

r = m("Emrys/Clara lunch"); check("first name + 'Emrys' context -> unique Clara", names(r) == ["Clara Meadows"] and r.people[0][1] == "first name")
r = m("Yves - Emrys"); check("'Yves - Emrys' -> Yves Hartley", names(r) == ["Yves Hartley"])
r = m("Martin - Rowe"); check("full name across a dash", names(r) == ["Martin Rowe"] and r.people[0][1] == "name")
r = m("Meeting w/ Usha Anand "); check("'Meeting w/ Usha Anand' full name", names(r) == ["Usha Anand"])
r = m("Rector meeting Elodie Fournier Teller, Chief AI Officer"); check("hyphenated surname written with a space", names(r) == ["Elodie Fournier-Teller"])
r = m("UNU-CPR | Rosalind Fenwick (PAI)"); check("different surname next to a known first name is NOT matched to Rosalind MacKay", names(r) == [] and not r.ambiguous)
r = m("Swiss / Anthropic Drinks"); check("organisation notes never matched by first word", names(r) == [] and not r.ambiguous and not r.new)
r = m("Global Network on AI"); check("'Global Network on AI' matches nothing", r.empty())
r = m("Daniel J - Emrys"); check("'Daniel J' resolves by surname initial", names(r) == ["Daniel Jarvis"])
r = m("Emrys x Daniel"); check("'Daniel' alone is ambiguous between 3 Davids", not r.people and len(r.ambiguous) == 1 and len(r.ambiguous[0][1]) == 3)
r = m("Emrys Alan RSS"); check("ambiguous Alan -> 2 options", len(r.ambiguous) == 1 and {p.name for p in r.ambiguous[0][1]} == {"Alan Dale", "Alan Lowe"})
r = m("Matilda - Emrys"); check("bare-name note is used when it is the only Matilda", names(r) == ["Matilda"])
r = m("Emrys and Rosa"); check("bare note + 2 full Renatas -> ambiguous, bare note offered as an option",
                                  len(r.ambiguous) == 1 and {p.name for p in r.ambiguous[0][1]} == {"Rosa", "Rosa Dunmore", "Rosa Alva"})
r = m("Anita - Emrys"); check("person-shaped title with no note -> 'new person?'", r.new == ["Anita"] and not r.people)
r = m("Meeting with Jordan Smith"); check("'with Jordan Smith' -> new person 'Jordan Smith'", r.new == ["Jordan Smith"])
for t in ["Monthly all-team meeting", "School picnic", "Dishwasher service visit", "Rose Ceremony (boys starting ceremony)",
          "El Salvador - UNU", "Follow up conversation with XYZ", "On-boarding Emrys - hold", "Panel of Panels"]:
    check(f"no match / no suggestion for '{t}'", m(t).empty())
r = m("Lunch at noon"); check("no person-looking token -> nothing", r.empty())
r = m("Catch up with sam dale"); check("lower-case name not treated as a first-name match", names(r) == ["Sam Dale"])      # full name still matches (any case)


# ---- cases found by the first dry run on the real calendar -----------------------
r = m("Catch-up with Emrys, Marguerite, Marina re: Rahim's Policy Brief")
check("comma-separated first names: Marguerite matched, no 'Marguerite Marina', no junk suggestions", names(r) == ["Marguerite Harlow"] and r.new == [] and not r.ambiguous)
r = m("Mei Lin - Emrys"); check("two-word unknown name next to Emrys -> 'Mei Lin'", r.new == ["Mei Lin"])
r = m("Re: Request for meeting with UCL IIPP DPI Applied learning"); check("email-subject noise with acronyms -> nothing", r.empty())
r = m("Follow up conversation with XYZ"); check("short mixed-case acronym (XYZ) is not a name", r.empty())
r = m("Zara - Emrys"); check("type-less 2-word People stub counts for first-name matching", names(r) == ["Zara Quinn"])
r = m("Association - Emrys"); check("organisation stubs are not matched by their first word", names(r) == [] )
r = m("Amina - Emrys"); check("single unknown name -> new person", r.new == ["Amina"])
r = m("Rosalind Fenwick - Emrys"); check("known first name + unknown surname -> new person 'Rosalind Fenwick', not Rosalind MacKay", names(r) == [] and r.new == ["Rosalind Fenwick"])
r = m("Emrys, Clara, Nina"); check("list of names: Clara matched, unknown Nina NOT suggested (not adjacent to a pattern)", names(r) == ["Clara Meadows"] and r.new == [])

# ---- 2. ICS parsing ---------------------------------------------------------
evs = cp.parse_ics(cal(
    vevent("u1", "Utc event", datetime(2026, 9, 15, 14, 0, tzinfo=timezone.utc), "Z"),
    vevent("u2", "Zurich event", datetime(2026, 9, 15, 16, 0), "W. Europe Standard Time"),
    vevent("u3", "Cancelled one", datetime(2026, 9, 16, 9, 0), extra="STATUS:CANCELLED\r\n"),
    vevent("u4", "Canceled: Foo", datetime(2026, 9, 16, 9, 0)),
    "BEGIN:VEVENT\r\nUID:u5\r\nSUMMARY:All day\r\nDTSTART;VALUE=DATE:20260917\r\nEND:VEVENT\r\n",
    vevent("u6", "Long\r\n  folded title", datetime(2026, 9, 18, 9, 0), "Eastern Standard Time"),
    vevent("u7", "Odd\\, escaped\\; text", datetime(2026, 9, 18, 10, 0), "Not A Real Zone"),
    "BEGIN:VEVENT\r\nUID:bad\r\nSUMMARY:No start\r\nEND:VEVENT\r\n"))
by = {e.uid: e for e in evs}
check("cancelled events (STATUS and 'Canceled:' title) are skipped; broken entries skipped", set(by) == {"u1", "u2", "u5", "u6", "u7"})
check("UTC and 'W. Europe Standard Time' both converted to the home zone (same instant 10:00 EDT)", by["u1"].start.hour == 10 and by["u2"].start.hour == 10)
check("all-day event flagged", by["u5"].all_day and by["u5"].day.isoformat() == "2026-09-17")
check("folded title unfolded; escapes decoded; unknown tz falls back to home tz", by["u6"].title.startswith("Long") and by["u7"].title == "Odd, escaped; text" and by["u7"].start.hour == 10)

# ---- 3. a full cycle on a synthetic calendar ---------------------------------
for d in range(0, 5):
    day = today - timedelta(days=d)
    w(f"Daily/{day.isoformat()}.md", f"---\ndate: {day.isoformat()}\ntype: daily\n---\n\n# {day.isoformat()}\n\n## 📝 Running Notes\n\nmy own text\n\n## 🔗 Related\n- x\n")
feed = cal(
    vevent("e1", "Emrys/Clara lunch", at(1, 12, 30)),
    vevent("e2", "Meeting w/ Usha Anand", at(2, 9)),
    vevent("e3", "School picnic", at(1, 15)),
    vevent("e4", "Emrys x Daniel", at(1, 16)),
    vevent("e5", "Anita - Emrys", at(2, 14)),
    vevent("e6", "Emrys/Clara lunch", at(0, 23, 59) + timedelta(days=1)),          # tomorrow: not yet happened
    vevent("e7", "Old meeting with Yves - Emrys", at(30, 10)),                    # outside the window
)
ICS = T / "feed.ics"; ICS.write_text(feed)
os.environ["ICS_FILE"] = str(ICS); cp.ICS_FILE = str(ICS)

def snapshot(): return {str(p): p.read_text() for p in V.rglob("*.md")}
before = snapshot()
cp.cycle(dry=True)
check("dry run changes nothing in the vault", snapshot() == before and not (V / "Review").exists())

cp.cycle(dry=False)
claire = (V / "Resources/People/Clara Meadows.md").read_text()
urv = (V / "Resources/People/Usha Anand.md").read_text()
d1 = today - timedelta(days=1); d2 = today - timedelta(days=2)
check("person note got one dated calendar line (first-name match flagged)", f"- {d1.isoformat()} 12:30 (calendar, matched by first name): Emrys/Clara lunch" in claire)
check("full-name match line has no 'first name' tag", f"- {d2.isoformat()} 09:00 (calendar): Meeting w/ Usha Anand" in urv)
check("existing note content untouched (appended only)", claire.startswith(before[str(V / 'Resources/People/Clara Meadows.md')].rstrip("\n")))
check("future event (tomorrow) NOT written to the person note", claire.count("<!--cal:") == 1)
check("event outside the 7-day window not processed", "Yves" not in (V / "Resources/People/Yves Hartley.md").read_text() or "Old meeting" not in (V / "Resources/People/Yves Hartley.md").read_text())
day1 = (V / f"Daily/{d1.isoformat()}.md").read_text()
check("daily note for that day lists the meeting with a link, before 'Running Notes'",
      "<!--cal-start-->" in day1 and "Emrys/Clara lunch — [[Resources/People/Clara Meadows|Clara Meadows]]" in day1 and day1.index("📅 Meetings") < day1.index("📝 Running Notes"))
check("personal event (School picnic) never copied anywhere", all("School picnic" not in t for t in snapshot().values()))
check("ambiguous Daniel is flagged in the daily note, not guessed", "❓ Daniel" in day1 and "Daniel Eastwood" not in day1.split("<!--cal-start-->")[1].split("<!--cal-end-->")[0])
check("user's own daily text preserved", "my own text" in day1)
q = (V / cp.QUEUE_REL).read_text()
check("queue has the ambiguous-Daniel item with 3 candidates + 'none of these'", q.count("It was [[Resources/People/Daniel") == 3 and "None of these" in q)
check("queue has the new-person item for Anita", "New person? “Anita”" in q and "Create a People note **Anita**" in q)

# ---- 4. idempotent ------------------------------------------------------------
snap = snapshot(); cp.cycle(dry=False)
check("second cycle changes nothing (markers + unchanged queue)", snapshot() == snap)

# ---- 5. decisions from the queue ----------------------------------------------
def tick(title_part, label_part):
    f = V / cp.QUEUE_REL; lines = f.read_text().split("\n"); cur = False; hit = 0
    for i, l in enumerate(lines):
        if l.startswith("### "): cur = title_part in l
        elif cur and re.match(r"^- \[ \]", l) and label_part in l:
            lines[i] = l.replace("- [ ]", "- [x]", 1); hit += 1
    f.write_text("\n".join(lines)); return hit
check("ticked 'It was Daniel Jarvis'", tick("“Daniel”", "It was [[Resources/People/Daniel Jarvis") == 1)
cp.cycle(dry=False)
dj = (V / "Resources/People/Daniel Jarvis.md").read_text()
check("picked person gets the meeting line", f"({'calendar'}): Emrys x Daniel" in dj)
check("other Davids untouched", "Emrys x Daniel" not in (V / "Resources/People/Daniel Eastwood.md").read_text())
day1 = (V / f"Daily/{d1.isoformat()}.md").read_text()
check("daily note now links Daniel Jarvis and no longer shows '❓ Daniel'", "Daniel Jarvis" in day1 and "❓ Daniel" not in day1)
check("resolved item leaves the queue", "“Daniel”" not in (V / cp.QUEUE_REL).read_text())

tick("New person? “Anita”", "Create a People note")
cp.cycle(dry=False)
ast = V / "Resources/People/Anita.md"
check("'Create' makes the People note (standard skeleton) and logs the meeting", ast.exists() and "type: person" in ast.read_text() and "Anita - Emrys" in ast.read_text())

# ignore is remembered
feed2 = feed + "";
w("Resources/People/Tmp.md", "---\ntype: person\n---\n")
os.environ["ICS_FILE"] = str(ICS)
ICS.write_text(cal(vevent("e9", "Zed - Emrys", at(1, 8)), vevent("e1", "Emrys/Clara lunch", at(1, 12, 30))))
cp.cycle(dry=False)
check("new unknown name queued", "New person? “Zed”" in (V / cp.QUEUE_REL).read_text())
tick("New person? “Zed”", "Ignore")
cp.cycle(dry=False); cp.cycle(dry=False)
check("'Ignore' removes it and it stays gone", "Zed" not in (V / cp.QUEUE_REL).read_text() and not (V / "Resources/People/Zed.md").exists())

# two ticks refused
ICS.write_text(cal(vevent("e8", "Emrys Alan RSS", at(1, 11))))
cp.cycle(dry=False)
tick("“Alan”", "It was [[Resources/People/Alan Dale"); tick("“Alan”", "It was [[Resources/People/Alan Lowe")
cp.cycle(dry=False)
check("two boxes ticked -> refused, nobody gets the line", "Emrys Alan RSS" not in (V / "Resources/People/Alan Dale.md").read_text() and "Emrys Alan RSS" not in (V / "Resources/People/Alan Lowe.md").read_text())

# ---- 6. feed failure ------------------------------------------------------------
os.environ.pop("ICS_FILE"); cp.ICS_FILE = ""
cp.ICS_URL = "http://127.0.0.1:9/nothing"          # connection refused
st = cp.load_state(); st["last_fetch"] = 0; cp.save_state(st)
(D / "last.ics").write_text(cal(vevent("e1", "Emrys/Clara lunch", at(1, 12, 30))))
cp.cycle(dry=False)
check("feed failure: banner shown in the queue note, last good copy still used", "⚠️" in (V / cp.QUEUE_REL).read_text() and "Calendar feed problem" in (V / cp.QUEUE_REL).read_text())
class FakeResp:
    def __init__(s, b): s.b = b
    def read(s): return s.b
    def __enter__(s): return s
    def __exit__(s, *a): return False
cp.urllib.request.urlopen = lambda req, timeout=0: FakeResp(b"<html>error page</html>")
st = cp.load_state(); st["last_fetch"] = 0; cp.save_state(st)
try:
    cp.fetch_feed(cp.load_state(), force=True); raised = False
except cp.FeedError as e:
    raised = "no longer returns a calendar" in str(e)
check("a revoked link (HTML error page) is reported, never parsed as a calendar", raised)
cp.urllib.request.urlopen = lambda req, timeout=0: FakeResp(cal(vevent("e1", "Emrys/Clara lunch", at(1, 12, 30))).encode())
st = cp.load_state(); st["last_fetch"] = 0; cp.save_state(st)
cp.cycle(dry=False)
check("feed recovers: banner cleared", "⚠️" not in (V / cp.QUEUE_REL).read_text())
check("cached feed file is private (0600)", (D / "last.ics").stat().st_mode & 0o777 == 0o600)

# ---- 7. alias: "Alan RSS" is Alan Dale (vault-native, via aliases on his note) -------------------
(V / "Resources/People/Alan Dale.md").write_text('---\ntitle: "Alan Dale"\ntype: person\naliases:\n  - Alan RSS\ntags:\n  - person\n---\n\n# Alan Dale\n\n## Notes\n')
people = cp.load_people()
r = cp.match_event("Emrys Alan RSS", people)
check("'Emrys Alan RSS' resolves to Alan Dale through his alias (no ambiguity with Alan Lowe)", [p.name for p, _ in r.people] == ["Alan Dale"] and not r.ambiguous and not r.new)
r = cp.match_event("Emrys Alan RSS", cp.load_people())
r2 = cp.match_event("Alan - Emrys", cp.load_people())
check("plain 'Alan' (no alias text) is still ambiguous between the two Adams", not r2.people and len(r2.ambiguous) == 1)
# the generic ignore-pattern hook still works for anything else
cp.IGNORE_PATTERNS = [r"\bPTA\b"]
ICS.write_text(cal(vevent("r1", "Emrys PTA meeting Daniel", at(1, 11))))
os.environ["ICS_FILE"] = str(ICS); cp.ICS_FILE = str(ICS); cp.ICS_URL = ""
before_q = (V / cp.QUEUE_REL).read_text()
cp.cycle(dry=False)
check("ignore pattern hook still skips matching events", "PTA" not in (V / cp.QUEUE_REL).read_text())
cp.IGNORE_PATTERNS = []

shutil.rmtree(T, ignore_errors=True)
print("\nALL PASSED" if ok else "\nSOME FAILED"); sys.exit(0 if ok else 1)
