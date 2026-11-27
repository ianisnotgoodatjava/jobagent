"""That the style machinery measures how he writes now, not how he wrote.

Nothing here asserts a specific percentage. The corpus is rebuilt from Gmail
and the numbers move every time he sends mail — that is the whole point of
measuring instead of writing them down — so a test that pinned 85% would fail
for the right reason and still be a nuisance. These assert properties: that
the window is bounded, that it cannot come back empty, that recent beats old,
and that a draft written in his older voice is caught while one written in
his current voice is not.

The corpus is gitignored (two years of one person's job search, public repo),
so every test that needs it skips without it.
"""

import datetime
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jobagent.outreach import outreach_retrieval as orx
from jobagent.outreach import style_check
from jobagent.outreach import style_profile as sp

CORPUS = os.path.join(ROOT, sp.CORPUS)


def _corpus():
    if not os.path.exists(CORPUS):
        raise unittest.SkipTest(f"no corpus at {CORPUS}")
    return sp.load(CORPUS)


class TestWindow(unittest.TestCase):
    def test_window_is_anchored_on_the_corpus_not_on_today(self):
        """The failure this prevents is silent and total.

        The corpus is rebuilt by hand and was twelve weeks behind when the
        window was added. A window measured from today selected zero emails,
        measure() returned {}, prompt_block() returned "", and auto_draft
        fell back to a one-line description of the voice without saying so. A
        stale corpus must make the profile old, never make it vanish.
        """
        corpus = _corpus()
        newest = max(d for e in corpus if (d := sp._sent_on(e)))
        # A window far narrower than the corpus's own age still yields emails.
        self.assertGreater(len(sp._initial(corpus, days=1)), 0)
        self.assertTrue(sp.measure(CORPUS, days=1))
        self.assertTrue(sp.prompt_block(CORPUS, days=1))
        # And it is genuinely anchored on the newest record, not on the clock.
        if (datetime.date.today() - newest).days > 30:
            self.assertGreater(len(sp._initial(corpus, days=7)), 0,
                               "window measured from today, not the corpus")

    def test_a_narrow_window_falls_back_to_a_usable_number(self):
        narrow = sp._initial(_corpus(), days=1)
        self.assertGreaterEqual(len(narrow), min(sp.MIN_EMAILS, len(narrow)))
        self.assertGreaterEqual(len(narrow), 1)

    def test_the_window_excludes_follow_ups(self):
        """A three-line nudge is no model for a first approach, and his
        follow-ups measurably did not drift when his first-contact emails
        did — 79 words against 190, two body paragraphs, no introduction."""
        self.assertFalse(any(e.get("is_follow_up")
                             for e in sp._initial(_corpus())))

    def test_the_window_is_a_subset_and_newer(self):
        corpus = _corpus()
        windowed = sp._initial(corpus, days=sp.RECENT_DAYS)
        everything = sp._initial(corpus, days=None)
        self.assertLessEqual(len(windowed), len(everything))
        if len(windowed) < len(everything):
            oldest_in = min(d for e in windowed if (d := sp._sent_on(e)))
            oldest_all = min(d for e in everything if (d := sp._sent_on(e)))
            self.assertGreater(oldest_in, oldest_all)


class TestProfile(unittest.TestCase):
    def test_the_profile_says_which_emails_it_measured(self):
        """"Measured over 345 emails" while reading 150 of them is the kind of
        false dunwell that makes a profile impossible to trust."""
        profile = sp.measure(CORPUS)
        self.assertIsNotNone(profile["span"])
        first, last = profile["span"]
        self.assertLessEqual(first, last)
        block = sp.prompt_block(CORPUS)
        self.assertIn(first, block)
        self.assertIn(last, block)
        self.assertIn(str(profile["n"]), block)

    def test_the_sign_off_is_not_double_punctuated(self):
        """.replace(chr(10), ', ') was applied to the finished f-string rather
        than to the value, so every prompt ever generated carried
        `Signs off "Best regards,, Jane Doe"`."""
        block = sp.prompt_block(CORPUS)
        self.assertNotIn(",,", block)
        self.assertNotIn("\n", block.split("Signs off")[-1].strip('" .'))

    def test_percentiles_are_wider_than_the_interquartile_range(self):
        """The IQR is the middle half by construction. Using it as a length
        limit rejected three quarters of his real emails."""
        profile = sp.measure(CORPUS)
        p5, p95 = profile["words_p5_p95"]
        q1, q3 = profile["words_range"]
        self.assertLessEqual(p5, q1)
        self.assertGreaterEqual(p95, q3)

    def test_phrase_rates_cover_every_declared_phrase(self):
        rates = sp.phrase_rates(CORPUS)
        self.assertEqual(set(rates), set(sp.PHRASES))
        for name, share in rates.items():
            self.assertGreaterEqual(share, 0.0, name)
            self.assertLessEqual(share, 1.0, name)

    def test_phrases_are_matched_across_a_hard_wrap(self):
        """His bodies are wrapped at about 75 characters, so a phrase regex
        run over the raw text undercounts badly — "I look forward to hearing
        from you" reads 6 times wrapped and 59 unwrapped."""
        wrapped = "I've attached my resume for\nyour reference."
        self.assertNotIn("resume for your reference", wrapped)
        self.assertIn("resume for your reference", sp._collapse(wrapped))


class TestRecencyBeatsTopic(unittest.TestCase):
    def test_retrieval_prefers_a_recent_email(self):
        """Topic similarity alone ranked February emails above August ones for
        the same desk, and those were the examples the generator was told to
        match the rhythm of."""
        if not os.path.exists(CORPUS):
            self.skipTest("no corpus")
        index = orx.load_index(CORPUS)
        query = "Investment Analyst credit"
        plain = index.search(query, k=5, half_life=None)
        decayed = index.search(query, k=5)
        if not plain or not decayed:
            self.skipTest("no hits for the probe query")
        newest_plain = max(r["date"] for _, r in plain)
        newest_decayed = max(r["date"] for _, r in decayed)
        self.assertGreaterEqual(newest_decayed, newest_plain)
        mean = lambda hits: sum(
            orx._age_days(r, index.newest) for _, r in hits) / len(hits)
        self.assertLessEqual(mean(decayed), mean(plain))

    def test_age_is_measured_against_the_corpus_not_today(self):
        """Anchoring decay on today would decay every record once the corpus
        fell behind, eventually weighting them all to nothing and leaving the
        ranking to floating-point noise."""
        if not os.path.exists(CORPUS):
            self.skipTest("no corpus")
        index = orx.load_index(CORPUS)
        self.assertEqual(min(index.decay and
                             [orx._age_days(r, index.newest)
                              for r in index.records]), 0.0)
        self.assertAlmostEqual(max(index.decay), 1.0, places=6)

    def test_a_dateless_record_is_neither_trusted_nor_discarded(self):
        newest = datetime.date(2026, 8, 28)
        self.assertEqual(orx._age_days({}, newest), float(orx.HALF_LIFE_DAYS))

    def test_no_exemplar_is_older_than_the_cap(self):
        if not os.path.exists(CORPUS):
            self.skipTest("no corpus")
        index = orx.load_index(CORPUS)
        picked = sp.exemplars(company="Fitch Ratings", area="ABS", k=2)
        for mail in picked:
            self.assertTrue(mail.get("date"), "exemplars must carry their date")
            age = (index.newest
                   - datetime.date.fromisoformat(mail["date"])).days
            self.assertLessEqual(age, sp.EXEMPLAR_MAX_DAYS)


class TestGate(unittest.TestCase):
    """A draft in his older voice is caught; one in his current voice is not.

    The older-voice draft below is not invented. It is the first version of a
    real Fenley email, written from memory of his style rather than from
    a measurement of it, and every problem the gate reports on it is one that
    was actually in it.
    """

    # The sender and reader here are invented. What makes this the "older
    # voice" is the structure -- the self-introduction, the signature line,
    # the minutes ask -- not whose details are in it.
    OLDER_VOICE = """Dear Nora,

My name is Jane Doe. I recently graduated from Example University with a
degree in Economics and currently work as an Analyst Assistant at
Example Corp. I came across your name in a rating action, and I'm reaching
out because I recently applied to the Structured Finance Analytics role.

I've always been interested in structured finance because it sits at the
intersection of quantitative analysis and financial markets. Since you work
on the other side of that, I'd be grateful for your perspective on it.

If you have 15-20 minutes available for a brief call or an in-person chat in
the coming weeks, I would greatly appreciate the opportunity to learn more
about your career path and experiences. I've attached my resume for context.

Thank you for your time and consideration.

Best regards,
Jane Doe"""

    SUBJECT = "Recent Example Graduate Inquiring about ABS at Acme Capital"

    def setUp(self):
        if not os.path.exists(CORPUS):
            self.skipTest("no corpus")

    def test_the_older_voice_is_caught(self):
        # "his job title" is read from the gitignored profile, so point it at
        # the invented sender's title for the length of the assertion.
        from unittest import mock
        from jobagent.outreach import style_profile
        with mock.patch.dict(style_profile.PHRASES,
                             {"his job title": r"Analyst Assistant"}):
            problems = " | ".join(
                style_check.check(self.OLDER_VOICE, self.SUBJECT))
        self.assertIn("hope-well", problems)
        self.assertIn("job title", problems)
        self.assertIn("quantitative-analysis", problems)

    def test_the_length_gate_is_derived_not_frozen(self):
        low, high = style_check._length_range()
        self.assertNotEqual((low, high),
                            (style_check._FALLBACK_LOW,
                             style_check._FALLBACK_HIGH),
                            "the gate is still using the figures from the "
                            "whole corpus; it should read the recent window")
        self.assertLess(low, high)

    def test_the_current_drafts_pass(self):
        """The rewritten Fenley emails, read back out of the file they
        were delivered in, so this fails if they are edited off-voice."""
        path = os.path.join(ROOT, "prep", "morningstar-outreach.md")
        if not os.path.exists(path):
            self.skipTest("no drafts on this machine")
        text = open(path).read()
        drafts = re.findall(r"```\n(Dear .*?)\n```", text, re.S)
        subjects = re.findall(r"\*\*Subject:\*\* (.+)", text)
        self.assertTrue(drafts, "no drafts found in the file")
        for i, draft in enumerate(drafts):
            subject = subjects[i] if i < len(subjects) else ""
            with self.subTest(draft=i + 1):
                self.assertEqual(style_check.check(draft, subject), [])

    def test_follow_up_rules_are_separate(self):
        """A follow-up has no introduction, no resume and no minutes ask by
        design, and his follow-ups measurably did not drift. Checking one
        against the first-contact rules reports a dozen false problems."""
        nudge = ("Hello Nora,\n\nI hope this message finds you well. I "
                 "wanted to follow up on my note from a couple of weeks "
                 "ago in case it was helpful to revisit.\n\nBest regards,\n"
                 "Jane Doe")
        self.assertGreater(len(style_check.check(nudge)), 0)
        as_follow_up = style_check.check(nudge, follow_up=True)
        self.assertLess(len(as_follow_up), len(style_check.check(nudge)))

    def test_his_own_follow_ups_pass_the_follow_up_gate(self):
        """The gate used to judge a nudge by the first-contact length range.

        That is not a near miss. His follow-ups run 79 words median against
        196, so a 152-word floor rejected every real follow-up in the corpus
        and the only way to satisfy it was to pad a three-line nudge back
        into a second introduction -- the exact drift the gate exists to
        stop. Read off his own follow-ups instead of asserting a number.
        """
        corpus = _corpus()
        nudges = [e for e in corpus if e.get("is_follow_up") and e.get("body")]
        if len(nudges) < 20:
            self.skipTest("too few follow-ups in the corpus")
        nudges.sort(key=lambda e: e.get("date", ""), reverse=True)
        bodies = [b for mail in nudges[:60]
                  if style_check._SIGN_OFF_RE.search(
                      b := sp._clean(mail["body"]))]
        self.assertGreater(len(bodies), 10, "no signed-off follow-ups to check")

        length_flags = lambda **kw: sum(
            any("words" in p for p in style_check.check(b, **kw))
            for b in bodies)
        as_first = length_flags()
        as_nudge = length_flags(follow_up=True)

        # A 5th-95th percentile range must reject about a tenth of the
        # population it was measured on -- that is what the percentiles mean,
        # so demanding zero here would be demanding a gate that never fires.
        # The bug was categorical: judged as first contacts, nearly all of
        # his own follow-ups were "shorter than anything he sends now".
        self.assertGreater(as_first, 0.8 * len(bodies),
                           "expected the first-contact range to reject his "
                           "follow-ups; this test is no longer measuring the "
                           "bug it was written for")
        self.assertLessEqual(as_nudge, 0.2 * len(bodies),
                             f"{as_nudge}/{len(bodies)} of his own follow-ups "
                             f"flagged on length; the gate is still judging "
                             f"them by the first-contact range")

    def test_the_follow_up_length_range_is_its_own(self):
        first = style_check._length_range()
        later = style_check._length_range(follow_up=True)
        self.assertNotEqual(first, later)
        self.assertLess(later[0], first[0])
        self.assertLess(later[1], first[1])

    def test_a_follow_up_that_would_start_a_new_thread_is_caught(self):
        """A follow-up's subject is the thread's: "Re: <Firm> Phone Screen
        Invitation". None of the 220 in the corpus carry the first-contact
        template, and inventing one would open a second thread beside the
        one being followed up on."""
        nudge = ("Hello Max,\n\nI wanted to follow up on my note from last "
                 "week about the three Global Markets analyst roles in "
                 "Chicago, in case it is useful to revisit now that the "
                 "applications are in. I am happy to send the postings "
                 "across if that is easier than digging them out.\n\n"
                 "Best regards,\nJane Doe")
        subject_problems = lambda subject: [
            p for p in style_check.check(nudge, subject, follow_up=True)
            if "thread" in p or "template" in p]
        self.assertEqual(subject_problems("Re: Jane and Max catch-up"), [])
        self.assertTrue(subject_problems("Following up on my application"))


if __name__ == "__main__":
    unittest.main()
