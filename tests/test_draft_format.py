"""That a draft reaches Gmail as paragraphs, under a subject that reads.

Two separate breakages, both found in the drafts folder rather than in a
test. Bodies arrived with a newline every 75 characters — the generator had
copied the wrapping in the corpus exemplars — and Gmail shows each of those
newlines, so paragraphs read as ragged half-lines. And topics cut to four
words ended on "and", which put "Sales and at U.S. Bank" in a subject line.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jobagent.core.email_text import unwrap
from jobagent.outreach.draft_writer import subject_topic

# An invented sender writing to an invented reader: this exercises the
# hard-wrap shape, so the words only have to wrap in the same places.
WRAPPED = (
    "Dear Nora,\n"
    "\n"
    "I hope you're doing well. My name is Jane Doe, and I recently graduated\n"
    "from Example University with a degree in Economics. I'm currently at\n"
    "Example Corp and wanted to reach out because I recently applied to a\n"
    "hedging role at Acme Capital.\n"
    "\n"
    "Thank you very much for your time, and I hope we have a chance to\n"
    "connect.\n"
    "\n"
    "Best regards,\n"
    "Jane Doe"
)


class Unwrap(unittest.TestCase):

    def test_wrapped_paragraph_becomes_one_line(self):
        out = unwrap(WRAPPED)
        self.assertIn("I recently graduated from Example University", out)
        self.assertIn("at Example Corp and wanted", out)
        self.assertIn("role at Acme Capital.", out)
        self.assertIn("a chance to connect.", out)

    def test_blank_lines_and_sign_off_survive(self):
        out = unwrap(WRAPPED)
        self.assertTrue(out.startswith("Dear Nora,\n\nI hope"))
        self.assertTrue(out.endswith("\n\nBest regards,\nJane Doe"))
        self.assertEqual(out.count("\n\n"), WRAPPED.count("\n\n"))

    def test_paragraph_running_into_sign_off(self):
        body = ("I've attached my resume as well for some additional context, and I'm\n"
                "happy to work around your schedule.\n"
                "Best regards,\n"
                "Jane Doe")
        self.assertEqual(
            unwrap(body),
            "I've attached my resume as well for some additional context, and "
            "I'm happy to work around your schedule.\nBest regards,\nJane Doe")

    def test_deliberate_short_lines_are_kept(self):
        # A follow-up listing job IDs: each pair is meant to be two lines.
        body = ("Job Title: Analyst, Global Markets Corporate Banking\n"
                "Job ID: R260027055\n"
                "\n"
                "Job Title: Analyst, Global Markets, U.S. Commercial FX and Interest Rate Sales\n"
                "Job ID: R260029569")
        self.assertEqual(unwrap(body), body)

    def test_lists_and_quoted_history_are_kept(self):
        body = ("- Built a model of loss development across twelve cohorts of policies\n"
                "- Wrote the report\n"
                "\n"
                "> On the earlier thread you mentioned that the desk had been hiring for\n"
                "> two seats this year")
        self.assertEqual(unwrap(body), body)

    def test_unwrapped_text_is_unchanged(self):
        flat = unwrap(WRAPPED)
        self.assertEqual(unwrap(flat), flat)
        self.assertEqual(unwrap(""), "")


class SubjectTopic(unittest.TestCase):

    def test_never_ends_on_a_connective(self):
        for role in ("Equity Derivatives Sales and Trading Associate",
                     "Fixed Income Strategy and Analytics Analyst",
                     "Capital Markets and Investor Relations Associate",
                     "Research, Data and Analytics for the Credit Desk"):
            topic = subject_topic(role)
            self.assertNotIn(topic.split()[-1].lower(),
                             {"and", "&", "or", "of", "for", "the", "at"},
                             f"{role!r} -> {topic!r}")

    def test_cuts_at_the_and(self):
        self.assertEqual(
            subject_topic("Equity Derivatives Sales and Trading Associate"),
            "Equity Derivatives Sales")

    def test_short_and_phrase_is_kept_whole(self):
        self.assertEqual(subject_topic("Mergers and Acquisitions Analyst"),
                         "Mergers and Acquisitions")

    def test_company_in_the_title_is_dropped(self):
        # subject_for adds " at <company>"; leaving it here printed it twice.
        self.assertEqual(
            subject_topic("NE Energy Trading Analyst at Brookfield Renewable"),
            "NE Energy Trading")

class ReplyHeaders(unittest.TestCase):
    """That a follow-up actually threads at the other end.

    EmailMessage folds In-Reply-To and References to 78 columns, and a
    Message-ID has no whitespace to fold at, so the folder falls back to RFC
    2047 encoded words. An Exchange Message-ID is 70-90 characters, over the
    limit every time, so every follow-up this project drafted carried

      In-Reply-To: =?utf-8?q?=3CEX1PR00MB0000...=40PH0...

    RFC 5322 says those headers carry msg-id tokens, not encoded words, so
    the recipient's client finds no parent and shows the reply as a new
    message. Gmail threads on its own threadId, which is exactly why this
    stayed invisible while being broken for the person being written to.
    """

    MID = ("<EX1PR00MB000011AA22BBC033D0E4455FF6A71"
           "@EX1PR00MB0000.namprd00.prod.outlook.com>")

    def build(self, **kw):
        from email import policy
        from email.message import EmailMessage
        from jobagent.store import gmail_client
        message = EmailMessage(policy=gmail_client._HEADER_POLICY)
        message["To"] = "rajiv@example.com"
        message["Subject"] = kw.get("subject", "RE: [External] Recent Example "
                                    "Graduate Inquiring about Portfolio "
                                    "Management in Financial Institutions "
                                    "Group at MUFG")
        message["In-Reply-To"] = self.MID
        message["References"] = self.MID
        message.set_content("Hello Sam,\n\nFollowing up.\n\n"
                            "Best regards,\nJane Doe")
        return message.as_string()

    def test_a_long_message_id_survives_intact(self):
        self.assertGreater(len(self.MID), 78)
        self.assertIn(self.MID, self.build())

    def test_no_encoded_words_in_the_headers(self):
        headers = self.build().split("\n\n")[0]
        self.assertNotIn("=?utf-8?q?", headers)
        self.assertNotIn("=3C", headers)

    def test_a_reference_list_is_not_broken_up(self):
        from email import policy
        from email.message import EmailMessage
        from jobagent.store import gmail_client
        refs = f"{self.MID} <second@example.com>"
        message = EmailMessage(policy=gmail_client._HEADER_POLICY)
        message["To"] = "x@y.com"
        message["References"] = refs
        message.set_content("hi")
        self.assertIn(refs, message.as_string())

    def test_a_non_ascii_subject_is_still_encoded_properly(self):
        """Widening the line limit must not stop RFC 2047 doing its real
        job, which is non-ASCII rather than length."""
        text = self.build(subject="Re: café and 15–20 minutes")
        headers = text.split("\n\n")[0]
        self.assertIn("=?utf-8?q?", headers)
        self.assertIn(self.MID, text)

if __name__ == "__main__":
    unittest.main()
