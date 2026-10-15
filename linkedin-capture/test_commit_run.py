import json, os, shutil, tempfile, unittest
import commit_run as c

HEAD = "---\ntitle: \"T\"\nsource: linkedin\nlink: https://example.org/a\n---\n\n# T\n\n## Archived content\n\nsome archived text that is long enough to count as real content here.\n"
ACT = "---\nsource: linkedin\n---\n\n## 2026-10-13 — Jane Doe\n\n**Actions:** reacted\n\nsummary text long enough to count as a non-empty log entry.\n"

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        for p in ("Resources/LinkedIn Posts/Reading List", "Resources/LinkedIn Posts/Activity", "Resources/People", "Resources/Ingest Digest"):
            os.makedirs(f"{self.d}/{p}")
        open(f"{self.d}/Resources/LinkedIn Posts/Reading List/A.md", "w").write(HEAD)
        open(f"{self.d}/Resources/LinkedIn Posts/Activity/2026-W41.md", "w").write(ACT)
        open(f"{self.d}/Resources/People/Jane Doe.md", "w").write("---\ntype: person\n---\n\n# Jane Doe\n\n## Notes\n- x\n")
        open(f"{self.d}/Resources/Ingest Digest/2026-10-14-linkedin.md", "w").write("# digest " + "x" * 100)
        self.m = {"run_date": "2026-10-14", "vault": self.d,
                  "sources": {"reactions": {"last_seen_sig": "sig:a"}},
                  "items": [{"key": "sig:a", "kind": "link", "file": "Resources/LinkedIn Posts/Reading List/A.md", "author": "Jane Doe"},
                            {"key": "sig:b", "kind": "general", "file": "Resources/LinkedIn Posts/Activity/2026-W41.md", "heading": "2026-10-13 — Jane Doe", "author": "Jane Doe"}],
                  "digest": "Resources/Ingest Digest/2026-10-14-linkedin.md"}
    def tearDown(self): shutil.rmtree(self.d)
    def v(self):
        # tests use a temp vault, so bypass only the canonical-path check
        return [p for p in c.verify(self.m, self.d) if "canonical vault" not in p]
    def test_ok(self): self.assertEqual(self.v(), [])
    def test_missing_file(self):
        os.remove(f"{self.d}/Resources/LinkedIn Posts/Reading List/A.md")
        self.assertTrue(any("file not found" in p for p in self.v()))
    def test_missing_heading(self):
        self.m["items"][1]["heading"] = "nope"
        self.assertTrue(any("heading" in p for p in self.v()))
    def test_missing_person(self):
        os.remove(f"{self.d}/Resources/People/Jane Doe.md")
        self.assertTrue(any("People note missing" in p for p in self.v()))
    def test_missing_digest(self):
        os.remove(f"{self.d}/Resources/Ingest Digest/2026-10-14-linkedin.md")
        self.assertTrue(any("digest" in p for p in self.v()))
    def test_icloud_vault_rejected(self):
        self.m["vault"] = "/Users/x/Library/Mobile Documents/iCloud~md~obsidian/Documents/Obsidian Vault"
        self.assertTrue(any("iCloud" in p for p in c.verify(self.m)))
    def test_noncanonical_vault_rejected(self):
        self.assertTrue(any("canonical" in p for p in c.verify(self.m, self.d)))
    def test_link_without_link_field(self):
        open(f"{self.d}/Resources/LinkedIn Posts/Reading List/A.md", "w").write(HEAD.replace("link: https://example.org/a", "link:"))
        self.assertTrue(any("non-empty link" in p for p in self.v()))
    def test_duplicate_key(self):
        self.m["items"][1]["key"] = "sig:a"
        self.assertTrue(any("duplicate" in p for p in self.v()))
    def test_commit_updates_state_only_via_commit(self):
        sp = f"{self.d}/state.json"
        json.dump({"reactions": {}, "captured_post_urls": ["old"]}, open(sp, "w"))
        n = c.commit(self.m, sp)
        s = json.load(open(sp))
        self.assertEqual(n, 2); self.assertEqual(s["captured_post_urls"], ["old", "sig:a", "sig:b"])
        self.assertEqual(s["reactions"]["last_seen_sig"], "sig:a"); self.assertEqual(s["reactions"]["last_run"], "2026-10-14")
    def test_failure_leaves_state_untouched(self):
        os.remove(f"{self.d}/Resources/LinkedIn Posts/Reading List/A.md")
        sp = f"{self.d}/state.json"; json.dump({"captured_post_urls": ["old"]}, open(sp, "w"))
        mp = f"{self.d}/m.json"; json.dump(self.m, open(mp, "w"))
        orig = c.STATE; c.STATE = sp
        try: rc = c.main(["x", mp])
        finally: c.STATE = orig
        self.assertEqual(rc, 1); self.assertEqual(json.load(open(sp))["captured_post_urls"], ["old"])

if __name__ == "__main__": unittest.main()
