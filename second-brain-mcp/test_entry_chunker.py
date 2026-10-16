import unittest
import entry_chunker as c

NOTE = """- 2026-10-06 21:41 (handwritten, Boox, page 1):
  # Ana Tavares

  T:Papm
  - Standards Incubation
  S917
  > [!note]- Handwritten original
  > ![[Inbox/Boox notes/assets/Ana Tavares/Ana Tavares-p1.jpg]]
  - [ ] Papm
- 2026-10-14 21:26 (handwritten, Boox, page 1):
  Ana Tavares

  Standards Industry
  > [!note]- Handwritten original
  > ![[Inbox/Boox notes/assets/Work-Ana Tavares/Work-Ana Tavares-p1.jpg]]
  - [ ] Piper"""


class T(unittest.TestCase):
    def test_splits_one_chunk_per_dated_entry(self):
        parts = c.split_entries(NOTE)
        self.assertEqual([p[0] for p in parts], ["2026-10-06 21:41", "2026-10-14 21:26"])
        self.assertIn("Standards Incubation", parts[0][1])
        self.assertNotIn("Standards Industry", parts[0][1])      # sessions no longer share a chunk
        self.assertTrue(parts[1][1].startswith("- 2026-10-14"))

    def test_single_entry_or_plain_text_left_whole(self):
        one = "- 2026-10-06 21:41 (handwritten):\n  only one"
        self.assertEqual(c.split_entries(one), [(None, one)])
        plain = "Just some prose.\n\nAnd a bullet list:\n- apple\n- pear"
        self.assertEqual(c.split_entries(plain), [(None, plain)])

    def test_preamble_kept_and_undated_bullets_do_not_split(self):
        t = "Intro line\n- not dated\n- 2026-01-01 a\n- 2026-01-02 b"
        parts = c.split_entries(t)
        self.assertEqual([p[0] for p in parts], [None, "2026-01-01", "2026-01-02"])
        self.assertIn("not dated", parts[0][1])

    def test_indented_dates_are_not_entries(self):
        t = "- 2026-01-01 a\n  - 2026-01-02 nested, not an entry\n- 2026-01-03 b"
        self.assertEqual([p[0] for p in c.split_entries(t)], ["2026-01-01", "2026-01-03"])

    def test_nothing_lost(self):
        parts = c.split_entries(NOTE)
        self.assertEqual("\n".join(p[1] for p in parts).replace("\n", "").replace(" ", ""),
                         NOTE.strip().replace("\n", "").replace(" ", ""))

    def test_embed_text_adds_context_and_drops_image_callout(self):
        e = c.embed_text("Ana Tavares", "Notes", c.split_entries(NOTE)[0][1])
        self.assertTrue(e.startswith("Ana Tavares - Notes\n- 2026-10-06"))
        self.assertIn("Standards Incubation", e)
        self.assertNotIn("Handwritten original", e)
        self.assertNotIn(".jpg", e)
        self.assertEqual(c.embed_text("T", None, "body"), "T\nbody")


if __name__ == "__main__":
    unittest.main()
