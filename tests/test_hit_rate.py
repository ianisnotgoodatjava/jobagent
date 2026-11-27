"""That the hit rate can only be moved by an outcome.

The failure these guard against is a number that looks like a measurement
and behaves like a countdown. Nine emails drafted on Saturday, scored on
Sunday, are nine failures to any rate computed over everything sent -- and
the rate then climbs for a fortnight without a single new fact arriving.
Every test here is about keeping an unanswered-but-young email out of the
denominator, and about the one thing being decided per draft: what the
address was built on.

No Gmail. The records are synthetic so these run offline and in a tenth of
a second; the collection path is exercised by running the command.
"""

import datetime
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jobagent.core import sender_profile
from jobagent.outreach import hit_rate

# Read, not written down: ME_RE is built from his own address, so a
# hardcoded copy put his personal address into a tracked file for no
# gain. profile.json is gitignored, which is where it belongs.
ME = f"{sender_profile.load()['name']} "\
     f"<{sender_profile.load()['email']}>"

TODAY = datetime.date(2026, 11, 23)


def record(sent, replied="", bounced=False, days=None, **extra):
    return {"thread": extra.pop("thread", sent + (replied or "x")),
            "to": extra.pop("to", "someone@firm.com"),
            "domain": "firm.com", "subject": "Recent Example Graduate Inquiring",
            "band": "individual contributor", "provenance": "derived",
            "sent": sent, "replied_on": replied, "days_to_reply": days,
            "bounced": bounced, "interview": False, "outcome": "", **extra}


def ago(days):
    return (TODAY - datetime.timedelta(days=days)).isoformat()


class Censoring(unittest.TestCase):

    def test_a_young_silent_email_is_pending_not_a_miss(self):
        self.assertEqual(
            hit_rate.classify(record(ago(2)), cutoff=21, today=TODAY),
            "pending")

    def test_an_old_silent_email_is_a_miss(self):
        self.assertEqual(
            hit_rate.classify(record(ago(60)), cutoff=21, today=TODAY),
            "miss")

    def test_the_cutoff_is_the_boundary(self):
        for age, expected in ((20, "pending"), (21, "miss"), (22, "miss")):
            with self.subTest(age=age):
                self.assertEqual(
                    hit_rate.classify(record(ago(age)), 21, TODAY), expected)

    def test_drafting_today_does_not_move_the_rate(self):
        """The whole point. Ten old emails, two replies; then nine drafted
        this weekend. The rate has to stay where it was, because nothing
        was learned -- and the nine have to still be visible as pending."""
        settled = ([record(ago(90), replied=ago(85), days=5, thread=f"r{i}")
                    for i in range(2)]
                   + [record(ago(90), thread=f"s{i}") for i in range(8)])
        before = hit_rate.tally(hit_rate.score(settled, 21, TODAY))

        weekend = [record(ago(2), thread=f"w{i}") for i in range(9)]
        after = hit_rate.tally(hit_rate.score(settled + weekend, 21, TODAY))

        self.assertEqual(before["rate"], 0.2)
        self.assertEqual(after["rate"], before["rate"])
        self.assertEqual(after["decided"], before["decided"])
        self.assertEqual(after["pending"], 9)
        self.assertEqual(after["sent"], 19)

    def test_pending_is_reported_not_hidden(self):
        """Held out of the denominator, not out of sight: an email nobody
        answered yet is the one worth following up."""
        scored = hit_rate.score([record(ago(1)), record(ago(99))], 21, TODAY)
        outcomes = {r["outcome"] for r in scored}
        self.assertEqual(outcomes, {"pending", "miss"})

    def test_a_record_with_no_send_date_is_never_counted_against_him(self):
        self.assertEqual(hit_rate.classify(record(""), 21, TODAY), "pending")


class Outcomes(unittest.TestCase):

    def test_a_bounce_outranks_a_reply(self):
        """An auto-reply from a colleague on a thread that also bounced is
        not the person answering."""
        both = record(ago(40), replied=ago(39), days=1, bounced=True)
        self.assertEqual(hit_rate.classify(both, 21, TODAY), "bounced")

    def test_a_bounce_is_not_counted_as_a_silent_miss(self):
        scored = hit_rate.score(
            [record(ago(40), bounced=True, thread="b"),
             record(ago(40), replied=ago(38), days=2, thread="h"),
             record(ago(40), thread="m")], 21, TODAY)
        got = hit_rate.tally(scored)
        self.assertEqual((got["bounced"], got["hits"], got["misses"]),
                         (1, 1, 1))
        # The reply rate is over the two that were delivered and answered,
        # not over all three: a bounce says nothing about the email.
        self.assertEqual(got["rate"], 0.5)
        self.assertAlmostEqual(got["bounce_rate"], 1 / 3)

    def test_an_empty_set_reports_no_rate_rather_than_zero(self):
        """0% and "nothing to go on" are different claims."""
        got = hit_rate.tally(hit_rate.score([record(ago(1))], 21, TODAY))
        self.assertIsNone(got["rate"])
        self.assertEqual(hit_rate.tally([])["rate"], None)


class Latency(unittest.TestCase):

    def test_the_cutoff_is_measured_from_his_own_replies(self):
        records = [record(ago(100), replied=ago(99), days=d)
                   for d in [1, 1, 2, 2, 3, 3, 4, 5, 8, 30]]
        timing = hit_rate.reply_latency(records)
        self.assertTrue(timing["measured"])
        self.assertEqual(timing["n"], 10)
        self.assertEqual(timing["median"], 3)
        self.assertEqual(timing["p90"], 30)
        self.assertEqual(timing["cutoff"], timing["p90"])

    def test_no_replies_falls_back_rather_than_dividing_by_zero(self):
        timing = hit_rate.reply_latency([record(ago(5)), record(ago(6))])
        self.assertFalse(timing["measured"])
        self.assertEqual(timing["cutoff"], hit_rate.FALLBACK_CUTOFF_DAYS)
        self.assertIsNone(timing["median"])

    def test_a_long_tail_widens_the_window_it_does_not_break_it(self):
        """One reply after four months should not make every email since
        then pending. p90 keeps it bounded; the max would not."""
        days = [1] * 19 + [120]
        timing = hit_rate.reply_latency(
            [record(ago(200), replied=ago(199), days=d) for d in days])
        self.assertLess(timing["cutoff"], 120)


class Provenance(unittest.TestCase):
    """The split he actually took risk on when he accepted derived
    addresses: not the shape of the local part, but what stood behind it."""

    FORMATS = {
        "morningstar.com": {"pattern": "first.last",
                            "source": "published analyst contacts on "
                                      "dbrs.morningstar.com"},
        "dakgroup.com": {"pattern": "flast",
                         "source": "LeadIQ: FLast@dakgroup.com, ~80%"},
    }
    def check(self, address):
        return hit_rate._provenance(address, self.FORMATS)

    def test_the_delivery_log_is_not_a_category(self):
        """A "proven" tier read off logs/address_patterns.json classified 81
        of 92 threads proven and then reported their bounce rate -- delivery
        measured against delivery. The file has no dates, so membership is a
        fact about the outcome, not about the choice being made. The tell was
        a 19.8% bounce rate inside the category defined by having arrived."""
        self.assertNotIn("proven", hit_rate.PROVENANCE)
        # An address that has demonstrably delivered is still only as good
        # as the convention recorded for its domain.
        self.assertEqual(self.check("jane.roe@bmo.com"), "guessed")

    def test_the_firms_own_pages_are_published(self):
        self.assertEqual(self.check("ann.example@morningstar.com"), "published")

    def test_an_aggregator_is_derived_not_published(self):
        """LeadIQ at 80% is a guess with a number on it. Counting it as
        published would hide the bounce rate he is paying for."""
        self.assertEqual(self.check("bexample@dakgroup.com"), "derived")

    def test_no_recorded_convention_at_all_is_guessed(self):
        self.assertEqual(self.check("someone@firmnobodyknows.com"), "guessed")

    def test_a_malformed_address_does_not_raise(self):
        for bad in ("", None, "not-an-address"):
            with self.subTest(value=bad):
                self.assertEqual(self.check(bad), "guessed")

    def test_the_breakdown_keeps_evidence_order_not_rate_order(self):
        """proven..guessed reads as a ladder. Sorting it by reply rate makes
        the table say nothing about whether verification is worth it."""
        scored = hit_rate.score(
            [record(ago(40), provenance="guessed", replied=ago(39), days=1,
                    thread="g"),
             record(ago(40), provenance="derived", thread="p"),
             record(ago(40), provenance="published", thread="q")], 21, TODAY)
        self.assertEqual([name for name, _ in hit_rate.by(scored,
                                                          "provenance")],
                         ["published", "derived", "guessed"])


class WhatCountsAsAnswering(unittest.TestCase):
    """Only a message dated at or after the email can be a reply to it.

    Too obvious to state, and the first thing this got wrong. Taking the
    earliest human message in the thread gave nora.vance@kroll.com a reply
    three months before the email it answered and rcalder@valuationresearch.com
    one seventeen months before -- earlier conversations with the same
    people -- and max(0, gap) turned each into a tidy nought. Eleven of
    most replies read as same-day and the median latency came out at zero.
    """

    def me(self, when):
        return {"from": ME, "subject": "x",
                "date": when}

    def them(self, when):
        return {"from": "Her <her@firm.com>", "subject": "RE: x",
                "date": when}

    def test_a_message_predating_the_email_is_not_a_reply_to_it(self):
        opener = self.me("Tue, 17 Nov 2026 10:00:00 +0000")
        got = hit_rate._resolve(
            [self.them("Mon, 04 Aug 2026 10:00:00 +0000"), opener], opener)
        self.assertEqual(got["replied_on"], "")
        self.assertIsNone(got["days_to_reply"])

    def test_a_thread_already_live_is_not_scored_as_a_first_touch(self):
        """Whether they answer says nothing about whether this email landed,
        so it is held out of the denominator rather than credited."""
        opener = self.me("Tue, 17 Nov 2026 10:00:00 +0000")
        got = hit_rate._resolve(
            [self.them("Mon, 04 Aug 2026 10:00:00 +0000"), opener], opener)
        self.assertFalse(got["first_touch"])
        self.assertEqual(hit_rate.classify(got, 21, TODAY), "ongoing")

    def test_an_ongoing_thread_is_outside_the_rate(self):
        scored = hit_rate.score(
            [record(ago(40), replied=ago(38), days=2, thread="h"),
             record(ago(40), thread="m"),
             record(ago(40), replied=ago(39), days=1, thread="o",
                    first_touch=False)], 21, TODAY)
        got = hit_rate.tally(scored)
        self.assertEqual(got["ongoing"], 1)
        self.assertEqual(got["decided"], 2)
        self.assertEqual(got["rate"], 0.5)

    def test_a_later_reply_is_still_counted_and_dated_honestly(self):
        opener = self.me("Thu, 03 Sep 2026 10:00:00 +0000")
        got = hit_rate._resolve(
            [opener, self.them("Thu, 17 Sep 2026 10:00:00 +0000")], opener)
        self.assertTrue(got["first_touch"])
        self.assertEqual(got["days_to_reply"], 14)

    def test_an_undated_message_does_not_invent_a_same_day_reply(self):
        """It used to fall back to the send date, which is a zero-day gap
        manufactured out of a missing header."""
        opener = self.me("Thu, 03 Sep 2026 10:00:00 +0000")
        got = hit_rate._resolve([opener, self.them("")], opener)
        self.assertEqual(got["replied_on"], "")

    def test_latency_ignores_ongoing_threads_and_negative_gaps(self):
        records = [record(ago(40), replied=ago(38), days=2, thread="a"),
                   record(ago(40), replied=ago(41), days=-1, thread="b"),
                   record(ago(40), replied=ago(30), days=10, thread="c",
                          first_touch=False)]
        timing = hit_rate.reply_latency(records)
        self.assertEqual(timing["n"], 1)
        self.assertEqual(timing["median"], 2)


class Interviews(unittest.TestCase):
    """The number that actually matters, and the one most easily faked."""

    def test_an_invitation_needs_a_body_to_be_seen(self):
        """invite_evidence reads the body. Asked of a metadata-only thread it
        answers False every time, so a report built on one would have printed
        "interviews 0" whatever the truth."""
        metadata_only = [{"from": "Her <her@firm.com>",
                          "subject": "RE: Recent Example Graduate Inquiring"}]
        self.assertFalse(hit_rate._interview_in(metadata_only))
        with_body = [{"from": "Her <her@firm.com>", "subject": "RE: x",
                      "body": "Are you free Tuesday? We would like to "
                              "schedule an interview."}]
        self.assertTrue(hit_rate._interview_in(with_body))

    def test_his_own_messages_are_not_an_invitation(self):
        """He writes "I would really appreciate the opportunity" in every
        email he sends. Counting his own words would make the interview rate
        equal to the reply rate."""
        mine = [{"from": ME, "subject": "x",
                 "body": "Are you free Tuesday? Could we schedule an "
                         "interview."}]
        self.assertFalse(hit_rate._interview_in(mine))

    def test_interviews_are_counted_over_replies_not_over_sends(self):
        scored = hit_rate.score(
            [record(ago(40), replied=ago(39), days=1, thread="a",
                    interview=True),
             record(ago(40), replied=ago(38), days=2, thread="b"),
             record(ago(40), thread="c")], 21, TODAY)
        got = hit_rate.tally(scored)
        self.assertEqual((got["interviews"], got["hits"]), (1, 2))


class Storage(unittest.TestCase):

    def test_records_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "logs", "outcomes.json")
            rows = hit_rate.score([record(ago(40), thread="a")], 21, TODAY)
            hit_rate.save(rows, path)
            self.assertEqual(hit_rate.load(path), rows)

    def test_a_missing_or_corrupt_file_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "nope.json")
            self.assertEqual(hit_rate.load(missing), [])
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w") as f:
                f.write("{not json")
            self.assertEqual(hit_rate.load(bad), [])

    def test_a_settled_outcome_is_reused_and_an_open_one_is_not(self):
        """What makes a rerun cheap. A reply or a bounce cannot change, so
        only the still-open threads are worth another Gmail round trip."""
        settled = hit_rate.score(
            [record(ago(40), replied=ago(39), days=1, thread="hit"),
             record(ago(40), bounced=True, thread="bounce"),
             record(ago(40), thread="miss"),
             record(ago(1), thread="young")], 21, TODAY)
        reusable = {r["thread"] for r in settled
                    if r["outcome"] in hit_rate.TERMINAL}
        self.assertEqual(reusable, {"hit", "bounce"})

    def test_the_saved_file_is_readable_json_with_a_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "o.json")
            hit_rate.save([record(ago(3))], path)
            with open(path) as f:
                data = json.load(f)
            self.assertIn("written", data)
            self.assertEqual(len(data["threads"]), 1)


if __name__ == "__main__":
    unittest.main()
