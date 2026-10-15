import importlib.util, io, json, os, re, shutil, stat, sys, tempfile, contextlib
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="mrtest_"))
V, D = T / "vault", T / "data"
os.environ.update(VAULT=str(V), DATA=str(D))
spec = importlib.util.spec_from_file_location("mr", str(Path(__file__).parent / "merge_review.py"))
mr = importlib.util.module_from_spec(spec); spec.loader.exec_module(mr)

ok = True
def check(label, cond):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

def w(rel, text):
    p = V / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text, encoding="utf-8"); return p

def person(name, body, typ="person", extra=""):
    return w(f"Resources/People/{name}.md", f'---\ntitle: "{name}"\ntype: {typ}\n{extra}tags:\n  - person\n---\n\n# {name}\n\n## Profile\n\n## Notes\n{body}')

# ---- fixture vault ----------------------------------------------------------
person("Ana Lopez", "- 2026-08-01 (handwritten, Boox): digital identity workshop Geneva\n- 2026-08-05: call about refugees\n- 2026-09-01: paper review\n")
person("Ana Lopez 1", "- 2026-10-06 (handwritten, Boox, page 1): follow up on pilots\n  > [!note]- Handwritten original\n  > ![[Inbox/Boox notes/assets/Ana Lopez 1/Ana Lopez 1-p1.jpg]]\n")
person("Ethan Vandermeer", "- 2026-09-23: met about funding\n")
w("Resources/People/Ethan Vandermeir.md", "@people #people\n\nUNGA Event:\n")
person("Olivia Courtney (FXYZ)", "- 2026-10-06 (handwritten, Boox): FXYZ board prep\n")
person("Matilda", "- 2026-07-01 (handwritten, Boox): digital identity strategy Geneva consultation\n- 2026-08-02: agriculture subsidies unrelated\n")
person("Matilda Roy", "- 2026-06-01: digital identity strategy consultation Geneva panel\n")
person("Matilda Dupont", "- 2026-05-01: agriculture subsidies policy brief\n")
person("Jonas", "- 2026-10-06: notes\n")
person("Sam Dale", "- 2026-01-01: a\n"); person("Sam Lent", "- 2026-01-02: b\n")
person("Rosa", "- 2026-03-01: thing about governance\n")
person("Rosa Dunmore", "- 2026-03-02: governance\n", extra="aliases:\n  - Rosa\n")
person("The Future Society", "- x\n", typ="organization"); person("Association for Responsible AI (ARAI)", "- z\n", typ="organization"); person("Alex (Xingqiang) He", "- q\n"); w("Resources/People/United Nations University Centre for Policy Research (UNU-CPR).md", "no frontmatter org\n"); person("Gabriel W.", "- r\n"); person("The VC Corner", "- y\n", typ="organization")

CODE_LINK = "`[[Resources/People/Ana Lopez 1]]`"
A = w("Resources/LinkedIn Posts/Reading List/A.md",
      "see [[Resources/People/Ana Lopez 1]] and [[Ana Lopez 1|Ana]] and ![[Resources/People/Ana Lopez 1#Notes]]\n"
      f"inline {CODE_LINK} stays\n```\n[[Resources/People/Ana Lopez 1]]\n```\nand [[Resources/People/Ana Lopez]] untouched\n")
B = w("Inbox/Boox notes/X.md", "mention [[People/Ana Lopez 1]] here and [[Resources/People/Olivia Courtney (FXYZ)]]\n")
SELF = Path(V / "Resources/People/Ana Lopez.md"); SELF.write_text(SELF.read_text() + "see also [[Resources/People/Ana Lopez 1]]\n")
SECRET = w("Personal/Networking/secret.md", "[[Resources/People/Ana Lopez 1]] key stuff\n")
TRASH = w(".Trash/old.md", "[[Resources/People/Ana Lopez 1]] old\n")
APPLE = w("Apple notes/n.md", "[[Resources/People/Ana Lopez 1]] imported\n")
before = {p: p.read_text() for p in (SECRET, TRASH, APPLE)}

def run_cycle(dry=False):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mr.cycle(dry)
    return buf.getvalue()

def tick(item_title_part, opt_label_part):
    q = V / mr.QUEUE_REL; lines = q.read_text().split("\n"); cur = False; hit = 0
    for i, l in enumerate(lines):
        if l.startswith("### "): cur = item_title_part in l
        elif cur and re.match(r"^- \[ \]", l) and opt_label_part in l:
            lines[i] = l.replace("- [ ]", "- [x]", 1); hit += 1
    q.write_text("\n".join(lines)); return hit

# ---- 1. dry run writes nothing ---------------------------------------------
out = run_cycle(dry=True)
check("dry run creates no Review folder / queue", not (V / "Review").exists())
check("dry run lists the items", "queue would contain" in out)

# ---- 2. real sweep ----------------------------------------------------------
run_cycle()
q = (V / mr.QUEUE_REL).read_text()
titles = re.findall(r"^### (.*?) <!--mq-item", q, re.MULTILINE)
print("   items:", titles)
check("same-name item: Ana Lopez 1 -> Ana Lopez", any(t.startswith("Ana Lopez 1  →  Ana Lopez") for t in titles))
check("typo item for the Vandermeer/Vandermeir pair", any("Vandermeer" in t and "Vandermeir" in t for t in titles))
check("rename item for 'Olivia Courtney (FXYZ)'", any(t.startswith("Olivia Courtney (FXYZ)  →  Olivia Courtney") for t in titles))
check("bare-name item for Matilda", any(t.startswith("Matilda  (which one?)") for t in titles))
check("bare-name item for Rosa", any(t.startswith("Rosa  (which one?)") for t in titles))
check("rename is NOT proposed for orgs, mid-name parentheticals or trailing dots", not any(x in t for t in titles for x in ("Association for Responsible AI", "Alex (Xingqiang)", "Gabriel W", "United Nations University")))
check("typo pair found even though one note is a frontmatter-less @mention stub", any("Vandermeer" in t and "Vandermeir" in t for t in titles))
check("NO false positives: Sam Dale/Sam Lent, Jonas, organisations", not any(("Sam" in t) or t.startswith("Jonas") or "Future Society" in t or "VC Corner" in t for t in titles))
check("Matilda Roy ranked above Matilda Dupont (content overlap)", q.index("Candidate [[Resources/People/Matilda Roy") < q.index("Candidate [[Resources/People/Matilda Dupont"))
check("existing alias is noted for Rosa Dunmore", 'already has the alias "Rosa"' in q)
check("queue never lists the excluded folders' notes", "secret" not in q.lower() and "old.md" not in q)
check("Review/ and Review/merged are world-writable dirs too", stat.S_IMODE((V / "Review").stat().st_mode) == 0o777)
check("queue file is world-writable (Syncthing)", stat.S_IMODE((V / mr.QUEUE_REL).stat().st_mode) == 0o666)

# ---- 3. unchanged queue is not rewritten (no churn while the user edits) ----
m1 = (V / mr.QUEUE_REL).stat().st_mtime_ns; run_cycle()
check("second cycle with no ticks leaves the queue file untouched", (V / mr.QUEUE_REL).stat().st_mtime_ns == m1)

# ---- 4. nothing happens without a tick; 5. two ticks are refused ------------
check("no merges happen without ticks", (V / "Resources/People/Ana Lopez 1.md").exists())
n1 = tick("Vandermeer", "Merge [[Resources/People/Ethan Vandermeir|Ethan Vandermeir]] into")
n2 = tick("Vandermeer", "Merge [[Resources/People/Ethan Vandermeer|Ethan Vandermeer]] into")
run_cycle()
check("two boxes ticked on one item -> refused, both notes still exist",
      (V / "Resources/People/Ethan Vandermeer.md").exists() and (V / "Resources/People/Ethan Vandermeir.md").exists())
# the user un-ticks both again (a single remaining tick would be a valid decision)
q = (V / mr.QUEUE_REL).read_text().replace("- [x] Merge", "- [ ] Merge")
(V / mr.QUEUE_REL).write_text(q)
run_cycle()
check("after un-ticking, nothing was merged and the item is still open", (V / "Resources/People/Ethan Vandermeer.md").exists() and (V / "Resources/People/Ethan Vandermeir.md").exists() and "Vandermee" in (V / mr.QUEUE_REL).read_text())

# ---- 6. merge Ana Lopez 1 into Ana Lopez -----------------------------------
tick("Ana Lopez 1  →  Ana Lopez", "Merge")
run_cycle()
ana = (V / "Resources/People/Ana Lopez.md").read_text()
check("source note moved out of People into Review/merged (not deleted)", not (V / "Resources/People/Ana Lopez 1.md").exists() and any((V / "Review/merged").glob("Ana Lopez 1 (merged*")))
check("target keeps ALL its original entries", all(x in ana for x in ("digital identity workshop Geneva", "call about refugees", "paper review")))
check("source's content appended under a 'Merged from' heading", '### Merged from "Ana Lopez 1"' in ana and "follow up on pilots" in ana and "Handwritten original" in ana)
check("source's headings demoted so T keeps one '## Notes'", ana.count("\n## Notes") == 1 and "#### Notes" in ana)
check("image embed (vault-relative path) preserved", "![[Inbox/Boox notes/assets/Ana Lopez 1/Ana Lopez 1-p1.jpg]]" in ana)
check("old name becomes an alias on the target", re.search(r"aliases:\n  - Ana Lopez 1\n", ana) is not None)
a_txt, b_txt = A.read_text(), B.read_text()
check("links rewritten: Resources/People/ style, alias+heading forms kept", "[[Resources/People/Ana Lopez]]" in a_txt and "[[Ana Lopez|Ana]]" in a_txt and "![[Resources/People/Ana Lopez#Notes]]" in a_txt)
check("links rewritten: People/ style (Boox) and links inside the target itself", "[[People/Ana Lopez]]" in b_txt and "[[Resources/People/Ana Lopez 1]]" not in ana)
check("inline code and fenced code untouched", CODE_LINK in a_txt and "```\n[[Resources/People/Ana Lopez 1]]\n```" in a_txt)
check("excluded folders (Personal, .Trash, Apple notes) never touched", all(p.read_text() == t for p, t in before.items()))
bk = list((D / "backups").glob("*Ana_Lopez_1__into__Ana_Lopez*"))
check("backup dir holds the ORIGINAL source, target and every rewritten note", len(bk) == 1 and (bk[0] / "Resources/People/Ana Lopez 1.md").exists() and "follow up on pilots" in (bk[0] / "Resources/People/Ana Lopez 1.md").read_text() and (bk[0] / "Inbox/Boox notes/X.md").read_text().count("[[People/Ana Lopez 1]]") == 1)
check("merge log written", "merge 'Ana Lopez 1' into 'Ana Lopez'" in (V / mr.LOG_REL).read_text())
check("item removed from the queue after being applied", "Ana Lopez 1  →" not in (V / mr.QUEUE_REL).read_text())
run_cycle()
check("idempotent: another cycle changes nothing", ana == (V / "Resources/People/Ana Lopez.md").read_text())

# ---- 7. keep separate is remembered ----------------------------------------
tick("Vandermeer", "Keep separate")
run_cycle()
run_cycle()
check("'Keep separate' removes the item and it never comes back", "Vandermeer" not in (V / mr.QUEUE_REL).read_text() and (V / "Resources/People/Ethan Vandermeir.md").exists())

# ---- 8. rename --------------------------------------------------------------
tick("Olivia Courtney (FXYZ)", "Rename")
run_cycle()
oc = V / "Resources/People/Olivia Courtney.md"
txt = oc.read_text() if oc.exists() else ""
check("renamed: new file exists, old one gone", oc.exists() and not (V / "Resources/People/Olivia Courtney (FXYZ).md").exists())
check("title + H1 updated, old name kept as alias, content intact", 'title: "Olivia Courtney"' in txt and "\n# Olivia Courtney\n" in txt and "  - Olivia Courtney (FXYZ)" in txt and "FXYZ board prep" in txt)
check("links to the old name re-pointed", "[[Resources/People/Olivia Courtney]]" in B.read_text())

# ---- 9. bare name: merge only into the chosen person -------------------------
before_dupont = (V / "Resources/People/Matilda Dupont.md").read_text()
tick("Matilda  (which one?)", "Merge [[Resources/People/Matilda|Matilda]] into [[Resources/People/Matilda Roy|Matilda Roy]]")
run_cycle()
roy = (V / "Resources/People/Matilda Roy.md").read_text()
check("bare note merged into the chosen Matilda only", 'Merged from "Matilda"' in roy and "agriculture subsidies unrelated" in roy and (V / "Resources/People/Matilda Dupont.md").read_text() == before_dupont and not (V / "Resources/People/Matilda.md").exists())

# ---- 10. impossible action does not retry forever ---------------------------
tick("Rosa  (which one?)", "Merge [[Resources/People/Rosa|Rosa]] into [[Resources/People/Rosa Dunmore|Rosa Dunmore]]")
(V / "Resources/People/Rosa Dunmore.md").unlink()               # target vanishes before the cycle
out = run_cycle()
q = (V / mr.QUEUE_REL).read_text()
check("skipped action is logged and its tick cleared (no endless retry)", "skipped" in out and "- [x]" not in q)

# ---- 11. failure mid-merge restores everything --------------------------------
person("Pat Lee", "- 2026-01-01: a\n"); person("Pat Lee 2", "- 2026-02-02: b\n")
w("Resources/X1.md", "[[Resources/People/Pat Lee 2]]\n")
snap = {p: p.read_text() for p in (V / "Resources/People/Pat Lee.md", V / "Resources/People/Pat Lee 2.md", V / "Resources/X1.md")}
real = mr.write_atomic
def boom(path, text):
    if Path(path).name == "X1.md": raise OSError("disk full (simulated)")
    return real(path, text)
mr.write_atomic = boom
res = mr.do_merge("Pat Lee 2", "Pat Lee", False)
mr.write_atomic = real
check("failure mid-merge reports FAILED and restores every file", res.startswith("FAILED") and all(p.read_text() == t for p, t in snap.items()))

# ---- 12. helpers -------------------------------------------------------------
check("add_aliases: inline list", "aliases:\n  - a\n  - b\n  - New\n" in mr.add_aliases("---\ntype: person\naliases: [a, b]\n---\nx", ["New"]))
check("add_aliases: block list, no duplicate (case-insensitive)", mr.add_aliases("---\ntype: person\naliases:\n  - Rosa\n---\nx", ["rosa"]) == "---\ntype: person\naliases:\n  - Rosa\n---\nx")
check("add_aliases: none yet / no frontmatter / special chars quoted",
      "aliases:\n  - Z\n" in mr.add_aliases("---\ntype: person\n---\nx", ["Z"]) and mr.add_aliases("plain", ["Q"]).startswith("---\naliases:\n  - Q\n---") and '"A: B"' in mr.add_aliases("---\ntype: person\n---\nx", ["A: B"]))
check("clean_display / norm_name", mr.clean_display("Olivia Courtney (FXYZ)") == "Olivia Courtney" and mr.clean_display("Francesca Bosco 1") == "Francesca Bosco" and mr.norm_name("Laura Gutiérrez") == mr.norm_name("laura gutierrez 2"))
check("link_rewrite is case-insensitive and leaves other names alone", mr.link_rewrite("[[resources/people/ana lopez 1]] [[Ana Lopez 12]] [[Ana Lopez 1x]]", "Ana Lopez 1", "Ana Lopez")[0] == "[[resources/people/Ana Lopez]] [[Ana Lopez 12]] [[Ana Lopez 1x]]")

shutil.rmtree(T, ignore_errors=True)
print("\nALL PASSED" if ok else "\nSOME FAILED"); sys.exit(0 if ok else 1)
