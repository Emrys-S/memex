import shutil, tempfile, unittest
from datetime import date
from pathlib import Path
import vault_tools as vt

TODAY = date(2026, 10, 14)


def w(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        w(self.d, "Projects/Tasks/Tasks.md",
          "- [x] 📅 2026-06-12 done thing ✅ 2026-06-12\n"
          "- [ ] 📅 2026-10-10 Send the op-ed draft\n"
          "- [ ] 📅 2026-10-20 Review Rosa's paper\n"
          "- [ ] Call Ana ([[2026-10-14]] · [[Resources/People/Ana Tavares]]) [marvin:abc123] ➕ 2026-10-14\n"
          "- [ ] \n- [ ] 📅 2026-06-18\n")
        w(self.d, "Resources/People/Ana Tavares.md",
          "## Notes\n- 2026-10-10 21:41 (handwritten, Boox, page 1): # Ana Tavares  T:Papm standards incubation\n  - 📌 Papm\n"
          "- 2026-09-01 10:00 (handwritten, Boox, page 1): old note\n")
        w(self.d, "Personal/Networking/Creds.md", "- [ ] secret task that must never appear\n")
        w(self.d, "Archive/old.md", "- [ ] archived task\n")
        w(self.d, "Resources/LinkedIn Posts/Reading List/Grievance Redress.md",
          "---\ntitle: \"Grievance Redress in DPI\"\ndate: 2026-10-12\nactions: [reacted, reshared]\nlink: https://x.org\n---\n\n# T\n\n## Summary\n\nOnly 4 of 15 systems had a live mechanism.\n\n## Original post\n\n> x\n")
        w(self.d, "Resources/LinkedIn Posts/Reading List/Old.md", "---\ntitle: \"Old one\"\ndate: 2026-08-01\nactions: [reacted]\n---\n\n## Summary\n\nold\n")
        w(self.d, "Resources/LinkedIn Posts/Activity/2026-W41.md",
          "---\nsource: linkedin\n---\n\n## 2026-10-13 — Jane Doe\n\n**Actions:** reacted\n**Post:** n/a\n\nJane on trust in DPI.\n\n> quote\n\nTags: #x\n\n---\n\n## 2026-09-01 — Old Poster\n\n**Actions:** saved\n\nold\n")
        w(self.d, "Resources/Bluesky/Activity/2026-W42.md",
          "---\nsource: bluesky\n---\n\n## 2026-10-14 — Aidan\n\n**Actions:** liked\n**Post:** https://bsky.app/x\n\nA post about open models.\n")

    def tearDown(self): shutil.rmtree(self.d)

    def test_open_tasks_sorted_cleaned_and_filtered(self):
        out = vt.open_tasks(self.d, today=TODAY)
        self.assertIn("3 open task(s)", out)                                  # stubs, done, archived and credentials excluded
        self.assertLess(out.index("Send the op-ed draft"), out.index("Review Rosa"))
        self.assertLess(out.index("Review Rosa"), out.index("Call Ana"))   # undated last
        self.assertIn("[OVERDUE]", out.split("Review Rosa")[0])
        self.assertNotIn("marvin:", out); self.assertNotIn("secret task", out); self.assertNotIn("archived task", out)
        self.assertNotIn("📌 Papm", out)                                       # plain bullets are not tasks

    def test_open_tasks_due_within_and_contains(self):
        out = vt.open_tasks(self.d, due_within_days=3, today=TODAY)
        self.assertIn("1 open task(s)", out); self.assertIn("op-ed", out)
        self.assertIn("1 open task(s)", vt.open_tasks(self.d, contains="ana", today=TODAY))
        self.assertEqual(vt.open_tasks(self.d, contains="zzz", today=TODAY), "No open tasks found.")

    def test_recent_captures_by_note_date_not_mtime(self):
        out = vt.recent_captures(self.d, days=7, today=TODAY)
        self.assertIn("Grievance Redress in DPI", out); self.assertIn("Jane Doe", out); self.assertIn("Aidan", out)
        self.assertIn("Handwritten note, Ana Tavares", out)
        self.assertNotIn("Old one", out); self.assertNotIn("Old Poster", out); self.assertNotIn("old note", out)
        self.assertLess(out.index("Aidan"), out.index("Grievance"))           # newest first

    def test_recent_captures_source_and_window(self):
        self.assertNotIn("Aidan", vt.recent_captures(self.d, days=7, source="linkedin", today=TODAY))
        self.assertIn("Aidan", vt.recent_captures(self.d, days=7, source="bluesky", today=TODAY))
        self.assertIn("Old Poster", vt.recent_captures(self.d, days=60, source="linkedin", today=TODAY))
        self.assertIn("No captures", vt.recent_captures(self.d, days=1, source="boox", today=TODAY))


if __name__ == "__main__":
    unittest.main()
