import unittest
import result_hygiene as h

INDEX = ("Johnson, Mark 219, 249 Johnson, Steven 125, 179 Juul, Jesper 41n, 105, 133, 135, 212, 251, 326, 327 "
         "Kaipainen, Kirsikka 335 Kalaga, Wojciech 208 Kallio, Kirsi Pauliina 323, 328 Kane, Pat 70 Kapor, Mitchell 216 "
         "Karvinen, Juho 323 Katz, James 47, 196, 291, 303, 319, 320 Kay, Alan 97, 102 Keen, Andrew 153, 154 Keller, Paula E. 97, 106")
REFS = ("Government as a platform: how policy makers should think about the foundations of digital public infrastructure.’ "
        "Kennedy School Review, 126-131. Edwards, P. N., Bowker, G. C., Jackson, S. J. and Williams, R. (2009) ‘Introduction: an "
        "agenda for infrastructure studies.’ Journal of the Association for Information Systems, 10(5). Available at: "
        "https://aisel.aisnet.org/jais/vol10/iss5/6 Felber, A. (2010) ‘Information systems research.’ Information Systems "
        "Research, 21(4), 724–735. Available at: http://www.jstor.org/stable/23015640 Zuckerman, E. (2020). ‘The case for "
        "digital public infrastructure.’ Knight First Amendment Institute. Available at: https://knightcolumbia.org/content/the-case")
PROSE = ("We chose a case study approach as it is viewed as a preferred method to explore how power relationships between the "
         "public and private sector actors involved in the two national digital identity systems are inscribed into the IT "
         "governance patterns of each. Our unit of analysis was the partners implementing the specific systems, and we draw on "
         "interviews conducted over eighteen months to show how accountability was distributed in practice and who could redress harm.")


class R:
    def __init__(self, score, payload): self.score, self.payload = score, payload


class T(unittest.TestCase):
    def test_index_detected(self): self.assertTrue(h.looks_like_index(INDEX))
    def test_reference_detected(self): self.assertTrue(h.looks_like_reference_list(REFS))
    def test_prose_kept(self):
        self.assertFalse(h.looks_like_index(PROSE)); self.assertFalse(h.looks_like_reference_list(PROSE))
        self.assertFalse(h.is_low_value_zotero({"chunk_type": "body", "text": PROSE}))
    def test_metadata_chunks_always_kept(self):
        self.assertFalse(h.is_low_value_zotero({"chunk_type": "metadata", "text": REFS}))
    def test_front_matter(self):
        self.assertTrue(h.looks_like_front_matter("Copyright 2019. All rights reserved. ISBN 978-0-1234. Library of Congress Cataloging"))
    def test_vault_title_only(self):
        self.assertTrue(h.is_low_value_vault({"text": "# Ana Tavares..."}))
        self.assertTrue(h.is_low_value_vault({"text": "## Notes\n"}))
    def test_title_only_vs_boilerplate(self):
        self.assertTrue(h.is_title_only({"text": "# Ana Tavares..."}))
        self.assertFalse(h.is_title_only({"text": "```dataview\nLIST\n```"}))
        self.assertFalse(h.is_title_only({"text": "## Notes\n- 2026-10-06 handwritten note with plenty of real content in it about standards incubation and more"}))

    def test_vault_dataview_boilerplate(self):
        self.assertTrue(h.is_low_value_vault({"text": "```dataview\nLIST\nFROM \"Resources/People\"\nWHERE file.mday = this.file.cday\n```"}))
        self.assertTrue(h.is_low_value_vault({"text": "```tasks\nnot done\npath includes Projects\n```"}))
    def test_vault_backlink_block(self):
        t = "\n".join(f"- 2026-10-{d:02d}: mentioned in [[Resources/LinkedIn Posts/Activity/2026-W41#x{d}]] (via LinkedIn)" for d in range(1, 6))
        self.assertTrue(h.is_low_value_vault({"text": t}))
    def test_vault_real_note_kept(self):
        self.assertFalse(h.is_low_value_vault({"text": "- 2026-10-06 21:41 (handwritten, Boox, page 1): Ana: standards incubation, S917 = us/china club, UNICEF child protection"}))
    def test_clean_scores_dedup_and_cap(self):
        z = lambda s, title, text=PROSE, k="a": R(s, {"title": title, "item_key": k, "chunk_type": "body", "text": text})
        res = [z(0.70, "Paper A", k="1"), z(0.69, "Paper A", k="2"), z(0.68, "Paper A", k="3"), z(0.60, "Paper B"),
               z(0.58, "Refs", REFS), z(0.30, "Weak")]
        out = h.clean(res, True, 0.42)
        self.assertEqual([r.payload["title"] for r in out], ["Paper A", "Paper A", "Paper B"])   # 3rd copy capped, refs + weak dropped
    def test_fuse_keeps_vault_visible(self):
        v = [R(0.60, {"path": f"v{i}"}) for i in range(3)]
        zt = [R(0.80 - i * 0.01, {"item_key": f"z{i}"}) for i in range(8)]
        top = h.fuse(v, zt, 6)
        self.assertEqual(sum(1 for r in top if "path" in r.payload), 3)    # 3 vault + 3 zotero, not 6 zotero
    def test_fuse_fills_when_one_side_empty(self):
        zt = [R(0.8 - i * 0.01, {"item_key": f"z{i}"}) for i in range(8)]
        self.assertEqual(len(h.fuse([], zt, 5)), 5)


    def test_content_terms_drops_filler(self):
        self.assertEqual(h.content_terms("what did I note about standards incubation"), ["standards", "incubation"])
        self.assertEqual(h.content_terms("the Standards, standards & Incubation!"), ["standards", "incubation"])
        self.assertEqual(h.content_terms("what did I say about it"), [])
        self.assertEqual(h.content_terms("one two three four five six", 3), ["three", "four", "five"])   # longest kept, order preserved

    def test_lexical_wanted(self):
        weak = [R(0.30, {"path": "a"}), R(0.40, {"path": "b"})]
        strong = [R(0.60, {"path": str(i)}) for i in range(3)]
        self.assertFalse(h.lexical_wanted([], weak))
        self.assertFalse(h.lexical_wanted(["list"], weak))                  # one short word is too common
        self.assertTrue(h.lexical_wanted(["incubation"], weak))             # one long word is fine
        self.assertTrue(h.lexical_wanted(["standards", "incubation"], weak))
        self.assertFalse(h.lexical_wanted(["standards", "incubation"], strong))   # semantic already found plenty

    def test_merge_keyword_order_and_dedup(self):
        sem = [R(0.60, {"path": "s1"}), R(0.30, {"path": "w1"}), R(0.28, {"path": "k1"})]
        kw = [R(0.29, {"path": "k1"}), R(0.27, {"path": "k2"})]
        out = [r.payload["path"] for r in h.merge_keyword(sem, kw)]
        self.assertEqual(out, ["s1", "k1", "k2", "w1"])                      # strong, keyword, weak; k1 once

    def test_merge_keyword_treats_entries_of_one_note_as_distinct(self):
        a = {"path": "p", "section_heading": "Notes", "entry": "2026-10-06"}
        b = {"path": "p", "section_heading": "Notes", "entry": "2026-10-14"}
        out = h.merge_keyword([R(0.60, a)], [R(0.30, b)])
        self.assertEqual([r.payload["entry"] for r in out], ["2026-10-06", "2026-10-14"])

    def test_cosine(self):
        self.assertAlmostEqual(h.cosine([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(h.cosine([1, 0], [0, 1]), 0.0)
        self.assertEqual(h.cosine([0, 0], [1, 1]), 0.0)


if __name__ == "__main__":
    unittest.main()
