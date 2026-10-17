import json
import unittest
from datetime import datetime, timezone
import watchlist_core as w

RSS = b"""<?xml version="1.0"?><rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>Example Blog</title>
<item><title>Older essay</title><link>https://blog.example/older</link><pubDate>Mon, 05 Oct 2026 08:00:00 GMT</pubDate><dc:creator>Ana Tavares</dc:creator><description>&lt;p&gt;Old &lt;b&gt;text&lt;/b&gt;&lt;/p&gt;</description></item>
<item><title>Newer essay</title><link>https://blog.example/newer?utm_source=x</link><pubDate>Wed, 14 Oct 2026 09:30:00 +0200</pubDate><content:encoded><![CDATA[<p>Full <i>body</i> here.</p>]]></content:encoded><description>short</description></item>
<item><title>No link item</title></item></channel></rss>"""
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Blog</title>
<entry><title>Atom entry</title><link rel="alternate" href="https://atom.example/e1"/><link rel="self" href="https://atom.example/e1.atom"/>
<updated>2026-10-12T10:00:00Z</updated><author><name>Jo Park</name></author><summary>Atom summary</summary></entry></feed>"""

POST_LINK = {"post": {"uri": "at://did:plc:abc/app.bsky.feed.post/3kxyz", "likeCount": 120, "repostCount": 30, "quoteCount": 5, "replyCount": 10,
             "record": {"text": "New piece on public infrastructure, worth reading in full.", "createdAt": "2026-10-15T08:00:00Z"},
             "embed": {"$type": "app.bsky.embed.external#view", "external": {"uri": "https://news.example/story?utm_campaign=z", "title": "Public infrastructure", "description": "A long read"}}}}
POST_PLAIN = {"post": {"uri": "at://did:plc:abc/app.bsky.feed.post/3kabc", "likeCount": 2, "record": {"text": "lol", "createdAt": "2026-10-15T09:00:00Z"}}}
POST_REPLY = {"post": {"uri": "at://did:plc:abc/app.bsky.feed.post/3krep", "record": {"text": "yes", "reply": {"parent": {}}, "createdAt": "2026-10-15T09:00:00Z"}}}
POST_REPOST = {"reason": {"$type": "app.bsky.feed.defs#reasonRepost"}, "post": {"uri": "at://did:plc:other/app.bsky.feed.post/3kzzz", "record": {"text": "someone else", "createdAt": "2026-10-15T09:00:00Z"}}}
POST_FACET = {"post": {"uri": "at://did:plc:abc/app.bsky.feed.post/3kfac", "record": {"text": "Read this " + "x" * 50, "createdAt": "2026-10-14T09:00:00Z",
              "facets": [{"features": [{"$type": "app.bsky.richtext.facet#link", "uri": "https://facet.example/a"}]}]}}}

TABLE = """# Watchlist
Intro text with a | pipe that is not a table.

| Name | Community | Tier | Bluesky | Feeds | Status | Notes |
|---|---|---|---|---|---|---|
| Ana Tavares | DPI | A | @ana.example | https://blog.example/feed https://x.example/rss | confirmed | note |
| Org One | Foreign policy | B | | https://org.example/feed.xml | Candidate | |
| Skipper | AI | A | | | skip | not them |

Text after the table.
"""


class T(unittest.TestCase):
    def test_table_parse_and_roundtrip(self):
        rows = w.parse_watchlist(TABLE)
        self.assertEqual([r["name"] for r in rows], ["Ana Tavares", "Org One", "Skipper"])
        self.assertEqual(rows[0]["bluesky"], "ana.example")
        self.assertEqual(rows[0]["feeds"], ["https://blog.example/feed", "https://x.example/rss"])
        self.assertEqual((rows[1]["status"], rows[1]["tier"]), ("candidate", "B"))
        again = w.parse_watchlist(w.render_table(rows))
        self.assertEqual([(r["name"], r["bluesky"], r["feeds"], r["status"]) for r in again],
                         [(r["name"], r["bluesky"], r["feeds"], r["status"]) for r in rows])

    def test_canonical_url_drops_tracking_and_scheme(self):
        a = w.canonical_url("https://www.News.example/story/?utm_source=a&id=7#frag")
        b = w.canonical_url("http://news.example/story?id=7&fbclid=zz")
        self.assertEqual(a, b)
        self.assertEqual(a, "news.example/story?id=7")
        self.assertEqual(w.canonical_url("not a url"), "")

    def test_rss(self):
        items = w.parse_feed(RSS)
        self.assertEqual([i["title"] for i in items], ["Newer essay", "Older essay"])     # newest first, link-less item dropped
        self.assertEqual(items[0]["summary"], "Full body here.")                           # content:encoded wins, tags stripped
        self.assertEqual(items[1]["author"], "Ana Tavares")
        self.assertEqual(items[0]["published"], datetime(2026, 10, 14, 7, 30, tzinfo=timezone.utc))

    def test_atom_and_garbage(self):
        items = w.parse_feed(ATOM)
        self.assertEqual((items[0]["url"], items[0]["author"]), ("https://atom.example/e1", "Jo Park"))
        self.assertEqual(w.parse_feed(b"<html><body>not a feed</body></html>"), [])
        self.assertEqual(w.parse_feed(b"\x00\x01 broken"), [])
        self.assertEqual(w.parse_feed(b""), [])

    def test_find_feed_links(self):
        html = ('<html><head><link rel="alternate" type="application/rss+xml" title="Comments" href="/comments/feed">'
                '<link rel="alternate" type="application/atom+xml" title="Posts" href="/atom.xml">'
                '<link rel="stylesheet" href="/x.css"></head></html>')
        self.assertEqual(w.find_feed_links(html, "https://blog.example/"), ["https://blog.example/atom.xml"])

    def test_bluesky_normalisation(self):
        a = w.normalise_bsky(POST_LINK, "ana.example")
        self.assertEqual(a["link"], "https://news.example/story?utm_campaign=z")
        self.assertEqual(a["url"], "https://bsky.app/profile/ana.example/post/3kxyz")
        self.assertEqual(a["engagement"], 120 + 60 + 10 + 10)
        self.assertEqual(w.normalise_bsky(POST_FACET, "ana.example")["link"], "https://facet.example/a")
        self.assertIsNone(w.normalise_bsky(POST_REPLY, "ana.example"))
        self.assertIsNone(w.normalise_bsky(POST_REPOST, "ana.example"))
        self.assertEqual(w.normalise_bsky(POST_PLAIN, "ana.example")["link"], "")

    def test_scores_rank_sensibly(self):
        art = {"kind": "article"}; linked = {"kind": "post", "link": "https://x", "text_len": 80}; chat = {"kind": "post", "text_len": 10}
        self.assertGreater(w.format_score(art), w.format_score(linked))
        self.assertGreater(w.format_score(linked), w.format_score(chat))
        self.assertEqual(w.reach_score(10, None), None)
        self.assertEqual(w.reach_score(5, 10), 0.0)                      # below the author's usual
        self.assertGreater(w.reach_score(200, 10), w.reach_score(30, 10))
        self.assertEqual(w.reach_score(10 ** 9, 10), 1.0)
        self.assertEqual(w.relevance_score(0.1), 0.0)
        self.assertEqual(w.relevance_score(0.9), 1.0)
        hi = w.total_score(1.0, 0.8, 0.5, 0.5, "A"); lo = w.total_score(0.2, 0.1, 0.0, 0.0, "A")
        self.assertGreater(hi, 0.6); self.assertLess(lo, 0.15)
        self.assertLess(w.total_score(1, 1, 1, 1, "B"), w.total_score(1, 1, 1, 1, "A"))
        self.assertAlmostEqual(w.total_score(1, 1, 1, None, "A"), 1.0)    # without a reach signal weights redistribute

    def test_selection_caps_threshold_and_novelty(self):
        c = [{"id": str(i), "score": s, "source": src, "seen_in_vault": seen} for i, (s, src, seen) in enumerate(
            [(0.9, "A", False), (0.8, "A", False), (0.7, "A", False), (0.6, "B", True), (0.5, "C", False), (0.3, "D", False)])]
        got = [x["id"] for x in w.select_for_digest(c, [], threshold=0.45, per_source_cap=2, global_cap=7)]
        self.assertEqual(got, ["0", "1", "4"])      # A capped at 2, seen item skipped, below-threshold skipped
        got = [x["id"] for x in w.select_for_digest(c, [{"id": "x", "source": "A"}], threshold=0.45, per_source_cap=2, global_cap=2)]
        self.assertEqual(got, ["0"])                # one A already used today, global cap 2

    def test_bluesky_confidence(self):
        real = {"handle": "ana.example", "displayName": "Prof. Ana Tavares", "followersCount": 24000, "postsCount": 460}
        decoy = {"handle": "ana2.example", "displayName": "Ana Tavares", "followersCount": 3, "postsCount": 0}
        other = {"handle": "x.example", "displayName": "Someone Else", "followersCount": 99999, "postsCount": 9000}
        p, conf, _ = w.bsky_confidence([decoy, other, real], "Ana Tavares")
        self.assertEqual((p["handle"], conf), ("ana.example", "high"))
        p, conf, _ = w.bsky_confidence([decoy], "Ana Tavares")
        self.assertEqual(conf, "candidate")                              # exact name but no activity: needs a human
        self.assertEqual(w.bsky_confidence([other], "Ana Tavares")[1], "none")
        twin = dict(real, handle="twin.example", followersCount=20000)
        self.assertEqual(w.bsky_confidence([real, twin], "Ana Tavares")[1], "candidate")   # ambiguous namesakes
        self.assertEqual(w.bsky_confidence([decoy], "Ana Tavares", known_handle="ana2.example")[1], "high")

    def test_bulk_dated_feed_is_detected(self):
        d = lambda day: datetime(2026, 10, day, 12, 0, tzinfo=timezone.utc)
        bulk = [{"published": d(16)} for _ in range(10)] + [{"published": d(3)}]
        normal = [{"published": d(1 + i)} for i in range(10)]
        self.assertTrue(w.dates_look_bulk(bulk)); self.assertFalse(w.dates_look_bulk(normal))
        self.assertFalse(w.dates_look_bulk([{"published": d(16)}] * 3))          # too few entries to judge
        rebuilt = [{"published": datetime(2026, 10, 14, 14, 21, tzinfo=timezone.utc)} for _ in range(14)] + \
                  [{"published": datetime(2025, 1, 1 + i, 9, 0, tzinfo=timezone.utc)} for i in range(20)]
        self.assertTrue(w.dates_look_bulk(rebuilt))                              # a block at one instant among older dates
        wire = [{"published": datetime(2026, 10, 12 + (i // 6), 12, 0, tzinfo=timezone.utc)} for i in range(30)]
        self.assertFalse(w.dates_look_bulk(wire))                                # date-only precision, ~6 per stamp: legitimate

    def test_selection_one_item_per_link(self):
        c = [{"id": "a", "score": 0.9, "source": "Outlet", "target": "x.example/story", "seen_in_vault": False},
             {"id": "b", "score": 0.8, "source": "Person", "target": "x.example/story", "seen_in_vault": False},
             {"id": "c", "score": 0.7, "source": "Other", "target": "y.example/other", "seen_in_vault": False}]
        self.assertEqual([x["id"] for x in w.select_for_digest(c, [])], ["a", "c"])
        self.assertEqual([x["id"] for x in w.select_for_digest(c, [{"id": "z", "source": "Q", "target": "x.example/story"}])], ["c"])

    def test_bio_topical_rejects_namesakes(self):
        self.assertTrue(w.bio_topical("Open government, digital policy and public sector innovation. Views my own.", "Digital governance"))
        self.assertFalse(w.bio_topical("Network scientist. Professor of physics. Developer of a graph library.", "Digital governance"))
        self.assertTrue(w.bio_topical("Professor of AI ethics and law", "AI governance"))
        self.assertFalse(w.bio_topical("", "AI governance"))
        self.assertTrue(w.bio_topical("Senior fellow, geopolitics and trade", "Foreign policy"))
        self.assertFalse(w.bio_topical("anything", "Unknown community"))

    def test_health(self):
        e = w.update_health({}, True, "2026-10-16T10:00:00+00:00", "2026-10-15T00:00:00+00:00")
        now = datetime(2026, 10, 16, tzinfo=timezone.utc)
        self.assertEqual(w.health_label(e, now), "ok")
        for _ in range(3): e = w.update_health(e, False, "2026-10-16T11:00:00+00:00")
        self.assertEqual(w.health_label(e, now), "failing")
        old = w.update_health({}, True, "2026-10-16T10:00:00+00:00", "2026-01-01T00:00:00+00:00")
        self.assertEqual(w.health_label(old, now), "quiet 90+ days")

    def test_median_and_ids(self):
        self.assertEqual(w.median([3, 1, 2]), 2.0); self.assertEqual(w.median([1, 2, 3, 4]), 2.5); self.assertEqual(w.median([]), 0.0)
        self.assertEqual(w.item_id("a", "b"), w.item_id("a", "b")); self.assertNotEqual(w.item_id("a"), w.item_id("b"))


if __name__ == "__main__":
    unittest.main()
