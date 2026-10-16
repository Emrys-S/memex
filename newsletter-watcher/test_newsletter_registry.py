import json
import unittest
import newsletter_registry as r

GMAIL = """FYI, worth a read

---------- Forwarded message ---------
From: Example Weekly <hello@exampleweekly.example>
Date: Mon, Oct 5, 2026 at 7:00 AM
Subject: The week in testing
To: <reader@gmail.example>

Top story here.

Unsubscribe: https://exampleweekly.example/u/abc123
"""
APPLE = """Begin forwarded message:

From: Policy Brief <news@policybrief.example>
Subject: Fwd mess
Date: 5 October 2026 at 08:10:00 CEST
To: Reader <reader@icloud.example>

Body text.
"""
OUTLOOK = """Hi,

From: Policy Brief <news@policybrief.example>
Sent: Monday, October 5, 2026 8:10 AM
To: Reader
Subject: Weekly brief

Body.
"""
OUTLOOK_NO_ADDR = "FW\n\nFrom: Tech Digest via Example Platform\nSent: Monday\nTo: Reader\nSubject: Digest #4\n\nBody"
HTML_ONLY = ('<div>Fwd</div><div>---------- Forwarded message ---------<br>From: <b>Example Weekly</b> '
             '&lt;hello@exampleweekly.example&gt;<br>Date: Mon<br>Subject: Issue 12<br>To: me<br></div>'
             '<p><a href="https://exampleweekly.example/manage?t=SECRET">Manage your preferences</a> | '
             '<a href="https://exampleweekly.example/unsub?t=SECRET">Unsubscribe</a></p>')


class T(unittest.TestCase):
    def test_gmail_forward(self):
        i = r.analyse("Fwd: The week in testing", GMAIL, "", "", "Reader <reader@gmail.example>", "inbox@ingest.example.org")
        self.assertTrue(i["forwarded"])
        self.assertEqual((i["sender_name"], i["sender_email"]), ("Example Weekly", "hello@exampleweekly.example"))
        self.assertEqual(i["forwarded_by"], "reader@gmail.example")
        self.assertEqual(i["original_subject"], "The week in testing")
        self.assertEqual(i["unsubscribe"], "https://exampleweekly.example/u/abc123")

    def test_apple_and_outlook_forwards(self):
        for body, subj in ((APPLE, "Fwd: Policy"), (OUTLOOK, "FW: Weekly brief")):
            i = r.analyse(subj, body, "", "", "Reader <reader@x.example>")
            self.assertEqual(i["sender_email"], "news@policybrief.example", subj)
            self.assertEqual(i["sender_name"], "Policy Brief")

    def test_forward_without_address_keeps_name_and_cleans_via(self):
        i = r.analyse("FW: Digest #4", OUTLOOK_NO_ADDR, "", "", "Reader <reader@x.example>")
        self.assertEqual((i["sender_name"], i["sender_email"]), ("Tech Digest", ""))

    def test_html_only_forward_finds_links(self):
        i = r.analyse("Fwd: Issue 12", "", HTML_ONLY, "", "Reader <reader@x.example>")
        self.assertEqual(i["sender_email"], "hello@exampleweekly.example")
        self.assertTrue(i["manage"].startswith("https://exampleweekly.example/manage"))
        self.assertTrue(i["unsubscribe"].startswith("https://exampleweekly.example/unsub"))

    def test_direct_message_uses_envelope_sender_and_list_header(self):
        hdr = json.dumps([["List-Unsubscribe", "<mailto:u@x.example>, <https://news.example/unsub/zzz>"]])
        i = r.analyse("Issue 7", "Hello", "", hdr, "Direct News <hi@news.example>", "directnews@ingest.example.org")
        self.assertFalse(i["forwarded"])
        self.assertEqual(i["sender_email"], "hi@news.example")
        self.assertEqual(i["unsubscribe"], "https://news.example/unsub/zzz")
        self.assertEqual(i["recipient_local"], "directnews")

    def test_forward_with_unparsable_block_does_not_guess_the_publisher(self):
        i = r.analyse("Fwd: hello", "Just a note, nothing forwarded inline.", "", "", "Reader <reader@x.example>")
        self.assertTrue(i["forwarded"])
        self.assertEqual((i["sender_name"], i["sender_email"]), ("", ""))
        self.assertIsNone(r.observe({}, i, "2026-10-16"))

    def test_prose_containing_from_is_not_a_header_block(self):
        self.assertIsNone(r.find_original_sender("The letter was From: nobody in particular\nand then it ended"))

    def test_platform_detection(self):
        self.assertEqual(r.detect_platform("a@x.example", ["https://click.mlsend.com/x"]), "MailerLite")
        self.assertEqual(r.detect_platform("a@y.substack.com", []), "Substack")
        self.assertEqual(r.detect_platform("a@x.example", ["https://x.us6.list-manage.com/u"]), "Mailchimp")
        self.assertEqual(r.detect_platform("a@x.example", []), "unknown")

    def test_slug_and_collision(self):
        self.assertEqual(r.slugify("Tech Policy Press", "x@y.example"), "techpolicypress")
        self.assertEqual(r.slugify("The Digest Newsletter", "x@y.example"), "digest")
        self.assertEqual(r.slugify("", "news@brief.example"), "brief")   # generic local part: use the domain stem
        reg = {}
        a = r.observe(reg, r.analyse("Fwd: a", GMAIL, "", "", "R <r@x.example>"), "2026-10-01")
        other = GMAIL.replace("hello@exampleweekly.example", "hello@weekly.other.example")
        b = r.observe(reg, r.analyse("Fwd: b", other, "", "", "R <r@x.example>"), "2026-10-02")
        self.assertNotEqual(a["slug"], b["slug"])
        self.assertEqual(len(reg), 2)

    def test_observe_counts_and_status(self):
        reg = {}
        for _ in range(2):
            r.observe(reg, r.analyse("Fwd: x", GMAIL, "", "", "R <r@x.example>"), "2026-10-05", "note-1", "ingest.example.org")
        e = next(iter(reg.values()))
        self.assertEqual((e["seen_forwarded"], e["seen_direct"]), (2, 0))
        self.assertTrue(r.status_of(e).startswith("forwarded only"))
        self.assertEqual(e["address"], "exampleweekly@ingest.example.org")
        r.observe(reg, r.analyse("Issue", "Hi", "", "", "Example Weekly <hello@exampleweekly.example>"), "2026-10-09")
        self.assertEqual(r.status_of(e), "direct and forwarded")

    def test_render_never_prints_full_subscription_links(self):
        reg = {}
        r.observe(reg, r.analyse("Fwd: Issue 12", "", HTML_ONLY, "", "R <r@x.example>"), "2026-10-05")
        md = r.render_registry(reg, "ingest.example.org")
        self.assertIn("exampleweekly@ingest.example.org", md)
        self.assertIn("manage: exampleweekly.example", md)
        self.assertNotIn("SECRET", md)
        self.assertNotIn("/manage?t=", md)

    def test_ease_labels(self):
        reg = {}
        for subj, body, frm in (("Fwd: s", GMAIL.replace("exampleweekly.example", "x.substack.com"), "R <r@x.example>"),
                                ("Fwd: f", GMAIL.replace("exampleweekly.example", "ft.com").replace("Example Weekly", "FT Brief"), "R <r@x.example>")):
            r.observe(reg, r.analyse(subj, body, "", "", frm), "2026-10-05")
        eases = {e["name"]: r._ease(e) for e in reg.values()}
        self.assertEqual(eases["Example Weekly"], "can automate (simple signup form)")
        self.assertEqual(eases["FT Brief"], "manual (tied to an account)")


if __name__ == "__main__":
    unittest.main()
