"""That the search is aimed by the posting, not by the job title alone.

The fixtures are the three U.S. Bank postings from 2026-11-23, where searching
on the title picked a Treasury executive for a liquidity risk analyst opening.
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jobagent.core import posting_target as pt
from jobagent.core import sender_profile

COUNTERPARTY = (
    "The Counterparty Risk Group provides independent review of counterparty "
    "risk across the business, including analysis of the derivative "
    "portfolio. Reviews proposed trades against credit limits and collateral "
    "terms. Concepts such as PFE, CVA and netting; PFE and CVA reporting. "
    "Locations: Minneapolis, New York, Chicago, Charlotte. This role reports "
    "to the Director of Counterparty Risk.")
LIQUIDITY = (
    "Supports Internal Liquidity Stress Testing (ILST) and liquidity risk "
    "strategy within the Treasury Liquidity Risk team. ILST methodology and "
    "documentation. Charlotte, NC, three or more days in the office.")
IMPACT = (
    "Join the Impact Finance team underwriting LIHTC equity investments and "
    "LIHTC syndications. Denver, Saint Louis or Minneapolis.")


class Read(unittest.TestCase):

    def test_team_reporting_line_and_offices(self):
        t = pt.read(COUNTERPARTY, "Derivative Counterparty Risk Analyst", "U.S. Bank")
        self.assertEqual(t["teams"][0], "Counterparty Risk Group")
        self.assertEqual(t["reports_to"], ["Director of Counterparty Risk"])
        self.assertEqual(t["cities"][:2], ["Minneapolis", "New York"])
        self.assertEqual(t["states"][:2], ["MN", "NY"])

    def test_desk_vocabulary_is_the_repeated_acronyms(self):
        t = pt.read(COUNTERPARTY, "Derivative Counterparty Risk Analyst")
        self.assertIn("PFE", t["desk_words"])
        self.assertIn("CVA", t["desk_words"])
        self.assertNotIn("analyst", t["desk_words"])

    def test_lowercase_team_and_title_prefix(self):
        impact = pt.read(IMPACT, "Impact Finance - Affordable Housing Real "
                         "Estate Financial Analyst", "U.S. Bank")
        self.assertEqual(impact["teams"][0], "Impact Finance")
        self.assertEqual(impact["cities"][1], "St. Louis")
        self.assertEqual(impact["states"][1], "MO")
        self.assertIn("LIHTC", impact["desk_words"])
        liq = pt.read(LIQUIDITY, "Liquidity Risk Management Analyst", "U.S. Bank")
        self.assertIn("Treasury Liquidity Risk", liq["teams"])
        self.assertEqual(liq["cities"], ["Charlotte"])

    def test_queries_aim_at_team_and_city(self):
        t = pt.read(COUNTERPARTY, "Derivative Counterparty Risk Analyst", "U.S. Bank")
        self.assertEqual(t["queries"][0],
                         "U.S. Bank Counterparty Risk Group Minneapolis")
        # The alumnus query ends with whatever school the profile names, not
        # with the first owner's.
        self.assertTrue(
            t["queries"][-1].endswith(sender_profile.get("school_short")))
        self.assertIn("(Associate OR AVP OR Manager)", t["queries"][-2])
        self.assertTrue(all(u.startswith("https://www.linkedin.com/search/")
                            for u in pt.linkedin_urls(t)))

    def test_analyst_role_targets_vp_level(self):
        self.assertIn("Vice President", pt.target_titles("Credit Analyst"))
        self.assertIn("Director", pt.target_titles("Vice President, Rates"))


class SearchIsAimed(unittest.TestCase):

    def test_team_words_decide_desk_match_and_state(self):
        from jobagent.people import people_search
        site = {"people": [
            {"name": "Ann Lee", "title": "Vice President, Capital and Liquidity",
             "email": "", "verified": True, "source": "firm site"},
            {"name": "Bo Chen", "title": "Vice President, Counterparty Risk",
             "email": "", "verified": True, "source": "firm site"}],
            "mail_domain": "", "pattern": "", "pattern_evidence": "", "notes": []}
        posting = pt.read(COUNTERPARTY, "Derivative Counterparty Risk Analyst",
                          "U.S. Bank")
        with mock.patch("jobagent.people.people_finder.find_firm", return_value=[]) as finra, \
                mock.patch("jobagent.people.team_pages.people", return_value=[]), \
                mock.patch("jobagent.people.site_people.find", return_value=site), \
                mock.patch.object(people_search.recent_contacts, "filter_contacts",
                                  side_effect=lambda c: (c, [])), \
                mock.patch.object(people_search.company_formats, "pattern_for",
                                  return_value=("first.last", "delivered")):
            out = people_search.find("U.S. Bank", role="Analyst",
                                     domain="usbank.com", posting=posting)
        self.assertEqual(out["candidates"][0]["name"], "Bo Chen")
        self.assertEqual(out["target"]["teams"][0], "Counterparty Risk Group")
        self.assertTrue(any(n.startswith("posting:") for n in out["notes"]))


if __name__ == "__main__":
    unittest.main()
