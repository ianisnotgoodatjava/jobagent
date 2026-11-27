"""That drafts imitate first-contact outreach, leaning toward the emails that worked.

Names and addresses here are invented; nothing touches Gmail.
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jobagent.outreach import outreach_kind, reply_labels

OUTREACH = {"id": "a", "subject": "Recent Example Graduate Inquiring about ABS at Acme",
            "body": "Dear Nora,\n\nI hope you're doing well. My name is Jane Doe, and I "
                    "recently graduated from Example University.", "date": "2026-11-01", "to": "jo@acme.com"}
THANKS = {"id": "b", "subject": "Thank You - Jane Doe",
          "body": "Dear Sam,\n\nThank you for taking the time to speak with me today.",
          "date": "2026-11-02", "to": "sam@acme.com"}
AVAIL = {"id": "c", "subject": "Junior Trader Position Interview Availability – Jane Doe",
         "body": "Dear Kim,\n\nThank you for reaching out. I am available Tuesday.",
         "date": "2026-11-03", "to": "kim@acme.com"}


class Kind(unittest.TestCase):

    def test_kinds(self):
        self.assertEqual(outreach_kind.kind(OUTREACH), "outreach")
        self.assertEqual(outreach_kind.kind(THANKS), "thank_you")
        self.assertEqual(outreach_kind.kind(AVAIL), "logistics")
        self.assertEqual(outreach_kind.kind({**OUTREACH, "subject": "Re: hi"}), "follow_up")
        self.assertEqual(outreach_kind.kind({**OUTREACH, "subject": "Fwd: Offer"}), "forward")

    def test_thank_you_opening_without_the_subject(self):
        mail = {**THANKS, "subject": "Following our call"}
        self.assertEqual(outreach_kind.kind(mail), "thank_you")

    def test_same_words_to_several_addresses_are_one_email(self):
        copies = [{**OUTREACH, "id": str(i), "to": f"jo{i}@acme.com"} for i in range(7)]
        self.assertEqual(len(outreach_kind.dedupe(copies)), 1)


class Replies(unittest.TestCase):
    OWN = {"me@example.edu"}

    def msgs(self, *later):
        return [{"id": "s", "internalDate": "100", "from": "Me <me@example.edu>",
                 "subject": "Hi", "date": ""}, *later]

    def test_human_reply_counts(self):
        out = reply_labels.read_thread(self.msgs(
            {"id": "r", "internalDate": "200", "from": "Jo <jo@acme.com>",
             "subject": "Re: Hi", "date": "Tue, 3 Nov 2026"}), "s", self.OWN)
        self.assertTrue(out["replied"])

    def test_bounce_and_auto_reply_do_not(self):
        out = reply_labels.read_thread(self.msgs(
            {"id": "b", "internalDate": "150", "from": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
             "subject": "Delivery Status Notification", "date": ""},
            {"id": "o", "internalDate": "160", "from": "Jo <jo@acme.com>",
             "subject": "Automatic reply: Hi", "date": ""}), "s", self.OWN)
        self.assertFalse(out["replied"])
        self.assertTrue(out["bounced"])

    def test_his_own_second_message_is_not_a_reply(self):
        out = reply_labels.read_thread(self.msgs(
            {"id": "f", "internalDate": "300", "from": "me@example.edu",
             "subject": "Re: Hi", "date": ""}), "s", self.OWN)
        self.assertFalse(out["replied"])

    def test_one_answered_copy_marks_the_email_replied(self):
        copies = [{**OUTREACH, "id": "x1", "to": "a@acme.com"},
                  {**OUTREACH, "id": "x2", "to": "b@acme.com"}]
        out = reply_labels.annotate(copies, cache={"x2": {"replied": True}})
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["replied"])
        self.assertEqual(out[0]["kind"], "outreach")


class Weighting(unittest.TestCase):

    def test_retrieval_prefers_the_replied_email_on_a_tie(self):
        from jobagent.outreach import outreach_retrieval as orx
        a = {**OUTREACH, "id": "1", "area": "ABS", "company": "Acme", "replied": False,
             "kind": "outreach"}
        b = {**OUTREACH, "id": "2", "area": "ABS", "company": "Acme", "replied": True,
             "kind": "outreach", "body": OUTREACH["body"] + " Second."}
        idx = orx.OutreachIndex([a, b])
        top = idx.search("ABS Acme", k=1)[0][1]
        self.assertEqual(top["id"], "2")

    def test_retrieval_never_returns_a_thank_you(self):
        from jobagent.outreach import outreach_retrieval as orx
        t = {**THANKS, "area": "ABS", "company": "Acme", "kind": "thank_you"}
        idx = orx.OutreachIndex([t])
        self.assertEqual(idx.search("ABS Acme"), [])

    def test_style_window_counts_replied_emails_twice(self):
        from jobagent.outreach import style_profile as sp
        mails = [{**OUTREACH, "id": str(i), "body": OUTREACH["body"] + str(i),
                  "kind": "outreach", "replied": i == 0} for i in range(sp.MIN_EMAILS)]
        window = sp._initial(mails, days=None)
        self.assertEqual(len(window), sp.MIN_EMAILS + sp.REPLY_WEIGHT - 1)

    def test_style_ignores_thank_you_notes(self):
        from jobagent.outreach import style_profile as sp
        window = sp._initial([{**THANKS, "kind": "thank_you"}], days=None)
        self.assertEqual(window, [])


if __name__ == "__main__":
    unittest.main()
