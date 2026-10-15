import json, os, shutil, tempfile, unittest
from pathlib import Path
import boox_to_obsidian as b


class Resp:
    def __init__(self, code=200, js=None, text=""):
        self.status_code, self._js, self.text = code, js or {}, text
    def json(self): return self._js


class FakeRequests:
    def __init__(self): self.calls, self.created, self.done_docs = [], 0, {}
    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(("POST", url.rsplit("/", 1)[-1], json))
        if url.endswith("addTask"):
            self.created += 1
            return Resp(200, {"_id": f"id{self.created}", "title": json["title"]})
        if url.endswith("markDone"): return Resp(200, {"_id": json["itemId"]})
        return Resp(404)
    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url.rsplit("/", 1)[-1], params))
        return Resp(200, self.done_docs.get(params["id"], {"_id": params["id"], "done": False}))


class T(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "Projects/Tasks").mkdir(parents=True)
        (self.d / "Projects/Tasks/Tasks.md").write_text("- [x] 📅 2026-06-12 old task ✅ 2026-06-12\n- [ ] 📅 2026-06-18 GDGL logos")  # no trailing newline
        self.saved = {k: getattr(b, k) for k in ("VAULT_ROOT", "TASKS_SYNC_STATE", "MARVIN_API_KEY", "MARVIN_FULL_TOKEN", "requests", "time")}
        b.VAULT_ROOT = str(self.d); b.TASKS_SYNC_STATE = self.d / "sync.json"
        b.MARVIN_API_KEY = "k"; b.MARVIN_FULL_TOKEN = ""
        self.fake = FakeRequests(); b.requests = self.fake
        class NoSleep:
            def __getattr__(s, n): return getattr(__import__("time"), n)
            def sleep(s, x): pass
        b.time = NoSleep()
    def tearDown(self):
        for k, v in self.saved.items(): setattr(b, k, v)
        shutil.rmtree(self.d)
    def lines(self): return (self.d / "Projects/Tasks/Tasks.md").read_text().split("\n")

    def test_capture_creates_marvin_and_obsidian_line_with_links_and_id(self):
        sym = b.extract_symbols("T: Call Ana re draft")
        b.process_symbols(sym, "Ana Tavares", "x", "2026-10-14", link_target="Resources/People/Ana Tavares")
        last = [l for l in self.lines() if l][-1]
        self.assertEqual(last, "- [ ] Call Ana re draft ([[2026-10-14]] · [[Resources/People/Ana Tavares]]) [marvin:id1] ➕ 2026-10-14")
        self.assertTrue((self.d / "Daily/2026-10-14.md").exists())          # link target exists
        self.assertIn("- [ ] 📅 2026-06-18 GDGL logos", self.lines())      # existing content untouched, newline handled
        self.assertEqual(self.fake.created, 1)

    def test_reprocessing_does_not_duplicate(self):
        sym = b.extract_symbols("T: Call Ana")
        for _ in range(3): b.process_symbols(sym, "N", "x", "2026-10-14", link_target="N")
        self.assertEqual(sum("Call Ana" in l for l in self.lines()), 1)
        self.assertEqual(self.fake.created, 1)

    def test_same_text_from_different_notes_is_kept(self):
        sym = b.extract_symbols("T: Send paper")
        b.process_symbols(sym, "A", "x", "2026-10-14", link_target="A"); b.process_symbols(sym, "B", "x", "2026-10-14", link_target="B")
        self.assertEqual(sum("Send paper" in l for l in self.lines()), 2)

    def test_source_note_gets_plain_bullet_when_list_exists(self):
        self.assertEqual(b.task_bullet("x"), "- 📌 x")
        (self.d / "Projects/Tasks/Tasks.md").unlink()
        self.assertEqual(b.task_bullet("x"), "- [ ] x")                    # fallback keeps old behaviour

    def test_no_task_list_still_creates_marvin(self):
        (self.d / "Projects/Tasks/Tasks.md").unlink()
        sym = b.extract_symbols("T: Solo")
        b.process_symbols(sym, "N", "x", "2026-10-14")
        self.assertEqual(self.fake.created, 1)

    def test_tick_in_obsidian_marks_done_in_marvin_once(self):
        sym = b.extract_symbols("T: Do thing")
        b.process_symbols(sym, "N", "x", "2026-10-14", link_target="N")
        f = self.d / "Projects/Tasks/Tasks.md"; f.write_text(f.read_text().replace("- [ ] Do thing", "- [x] Do thing"))
        b.sync_tasks(); b.sync_tasks()
        done_calls = [c for c in self.fake.calls if c[1] == "markDone"]
        self.assertEqual(len(done_calls), 1); self.assertEqual(done_calls[0][2]["itemId"], "id1")

    def test_untracked_lines_are_never_touched(self):
        before = (self.d / "Projects/Tasks/Tasks.md").read_text()
        b.sync_tasks()
        self.assertEqual((self.d / "Projects/Tasks/Tasks.md").read_text(), before)
        self.assertEqual(self.fake.calls, [])

    def test_marvin_done_ticks_obsidian_only_with_full_token(self):
        sym = b.extract_symbols("T: Do thing")
        b.process_symbols(sym, "N", "x", "2026-10-14", link_target="N")
        self.fake.done_docs["id1"] = {"_id": "id1", "done": True, "doneAt": 1792100000000}
        b.sync_tasks()                                                       # no full token: no change
        self.assertTrue(any(l.startswith("- [ ] Do thing") for l in self.lines()))
        b.MARVIN_FULL_TOKEN = "full"; b.sync_tasks()
        line = [l for l in self.lines() if "Do thing" in l][0]
        self.assertTrue(line.startswith("- [x] Do thing") and "✅ 2026-" in line, line)
        n = len(self.fake.calls); b.sync_tasks()                             # not re-checked once ticked
        self.assertEqual([c for c in self.fake.calls[n:] if c[1] == "markDone"], [])

    def test_person_entry_and_note_builders_use_bullet(self):
        sym = b.extract_symbols("T: Ring Bo")
        entry = b.build_person_entry([("hi", Path("p1.jpg"))], "stem", sym, "2026-10-14", "10:00", 1)
        self.assertIn("  - 📌 Ring Bo", entry); self.assertNotIn("- [ ] Ring Bo", entry)

if __name__ == "__main__": unittest.main()
