"""That a firm's own website yields its people, and only its people.

Every fixture here is a page shape met for real while building this: Tidal's
about page hiding its mail domain behind Cloudflare, Kaizen's team grid with
a first@ address in each card and portfolio CEOs quoted on the homepage,
Osaic's marketing heading that reads like a name, F.N.B.'s "Jr." titles, and
StepStone Group's site answering with a bot check while stepstone.com — a
different company with the same legal name — answers with a title.

No test touches the network: `find` and `find_website` take the fetch
function as an argument.
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jobagent.people import site_people as sp


def cf(address: str, key: int = 0x5a) -> str:
    """Encode an address the way Cloudflare's email protection does."""
    return f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in address)


TIDAL_ABOUT = f"""<html><head><title>About | Tidal Financial Group</title></head>
<body>
<nav><a href="/about">About</a><a href="/etfs">ETFs</a></nav>
<h2>Our Team</h2>
<div class="card"><h3>Colin Marsh</h3><p>Chief Executive Officer</p></div>
<div class="card"><h3>Dev Okafor</h3><p>Chief Investment Officer</p></div>
<div class="card"><h3>Tess Halloran</h3><p>Head of Human Resources</p></div>
<footer>Contact: <a href="/cdn-cgi/l/email-protection#{cf('contact@tidalfg.com')}">
[email&#160;protected]</a>
<span class="__cf_email__" data-cfemail="{cf('media@tidalfg.com')}">[email protected]</span>
</footer></body></html>"""

KAIZEN_HOME = """<html><head><title>Kaizen Equity Partners</title></head><body>
<a href="/team">Our Team</a>
<blockquote>Rob Kessler<br>Former VP of Sales, CartWheel</blockquote>
<blockquote>Sam Whitfield<br>CEO &amp; Founder, LedgerLoop</blockquote>
</body></html>"""

KAIZEN_TEAM = """<html><head><title>Team | Kaizen Equity Partners</title></head>
<body><div class="grid">
<div class="member"><h4>Rhys Calder</h4><p>Vice President</p>
  <a href="mailto:rhys@kaizen-equity.com">Email</a></div>
<div class="member"><h4>Tomas Lindqvist</h4><p>Director</p>
  <a href="mailto:tomas@kaizen-equity.com">Email</a></div>
<div class="member"><h4>Devin Hartley</h4><p>Managing Partner</p>
  <a href="mailto:devin@kaizen-equity.com">Email</a></div>
<div class="member"><h4>Mara Ellis</h4><p>Associate</p>
  <a href="mailto:mara@kaizen-equity.com">Email</a></div>
</div></body></html>"""


def fake_fetch(pages: dict):
    """A fetch that serves a dict of url -> html and 404s everything else."""
    def fetch(url, timeout=15):
        url = url.rstrip("/")
        if url in pages:
            return url, pages[url]
        raise OSError(f"404 {url}")
    return fetch


class Pieces(unittest.TestCase):

    def test_cloudflare_addresses_decode(self):
        self.assertEqual(sp.decode_cloudflare(cf("contact@tidalfg.com")),
                         "contact@tidalfg.com")
        found = sp.emails_in(TIDAL_ABOUT)
        self.assertIn("contact@tidalfg.com", found)
        self.assertIn("media@tidalfg.com", found)

    def test_mail_domain_is_where_addresses_point_not_the_website(self):
        emails = sp.emails_in(TIDAL_ABOUT)
        self.assertEqual(sp.mail_domain(emails, "tidalfinancialgroup.com"),
                         "tidalfg.com")

    def test_other_peoples_domains_are_not_the_firms(self):
        # DRW's pages carried a riversideid.com address and Lenovo's a form
        # placeholder; neither is where the firm reads mail.
        self.assertEqual(sp.mail_domain(["help@riversideid.com"], "drw.com",
                                        "DRW"), "")
        self.assertEqual(sp.mail_domain(["you@example.com"], "lenovo.com",
                                        "Lenovo"), "")
        self.assertTrue(sp.owned_by("tidalfg.com", "Tidal Financial Group"))
        self.assertTrue(sp.owned_by("gs.com", "Goldman Sachs"))
        self.assertTrue(sp.owned_by("fnb-corp.com", "F.N.B. Corporation"))
        self.assertTrue(sp.owned_by("drw.com", "DRW", "drw.com"))

    def test_image_names_are_not_addresses(self):
        self.assertEqual(sp.emails_in('<img src="logo@2x.png"> hi@firm.com'),
                         ["hi@firm.com"])

    def test_card_shape(self):
        people = {p["name"]: p["title"] for p in sp.people_in(TIDAL_ABOUT)}
        self.assertEqual(people["Dev Okafor"], "Chief Investment Officer")
        self.assertNotIn("Our Team", people)

    def test_single_line_shape_and_suffixes(self):
        html = ("<p>Nora J. Vance, Jr. - Chairman, President and CEO</p>"
                "<p>Rhys L. Calder, Chief Credit Officer</p>"
                "<p>Jane Doe, CFA, Managing Director</p>")
        people = {p["name"]: p["title"] for p in sp.people_in(html)}
        self.assertEqual(people["Rhys L. Calder"], "Chief Credit Officer")
        self.assertEqual(people["Jane Doe"], "Managing Director")
        self.assertFalse(any(t.startswith(("Jr", "-")) for t in people.values()))

    def test_json_ld_person(self):
        html = ('<script type="application/ld+json">{"@type":"Person",'
                '"name":"Ana Ruiz","jobTitle":"Vice President",'
                '"email":"mailto:ana.ruiz@firm.com"}</script>')
        [person] = sp.people_in(html)
        self.assertEqual((person["name"], person["title"], person["email"]),
                         ("Ana Ruiz", "Vice President", "ana.ruiz@firm.com"))

    def test_a_lone_heading_over_copy_is_not_a_person(self):
        # Osaic's homepage: one "name" and one "title" is copy, not a team.
        html = "<h2>Supported Autonomy</h2><p>Independent advisor</p>"
        self.assertEqual(sp.people_in(html), [])

    def test_titles_and_names_are_told_apart(self):
        self.assertFalse(sp.is_name("Managing Director"))
        self.assertFalse(sp.is_name("New York"))
        self.assertTrue(sp.is_name("Mary-Kate O'Neil"))
        self.assertTrue(sp.is_title("Senior Vice President, Rates"))

    def test_quoted_outsiders_are_not_staff(self):
        words = sp.distinctive_words("Tidal Financial Group")
        self.assertTrue(sp.works_elsewhere(
            "Head of Exchange Traded Products at the New York Stock Exchange",
            words))
        self.assertTrue(sp.works_elsewhere("Former VP of Sales, CartWheel", words))
        self.assertFalse(sp.works_elsewhere(
            "Partner at Tidal Financial Group", words))
        self.assertFalse(sp.works_elsewhere("Chief Investment Officer", words))


class Addresses(unittest.TestCase):

    def test_card_position_assigns_the_address(self):
        people = sp.people_in(KAIZEN_TEAM)
        near = sp.addresses_near_names(KAIZEN_TEAM, [p["name"] for p in people])
        self.assertEqual(near["rhys@kaizen-equity.com"], "Rhys Calder")
        self.assertEqual(near["mara@kaizen-equity.com"], "Mara Ellis")

    def test_convention_is_voted_from_published_pairs(self):
        people = sp.people_in(KAIZEN_TEAM)
        emails = sp.emails_in(KAIZEN_TEAM)
        near = sp.addresses_near_names(KAIZEN_TEAM, [p["name"] for p in people])
        result = sp.match_addresses(people, emails, near)
        self.assertEqual((result["pattern"], result["domain"]),
                         ("first", "kaizen-equity.com"))

    def test_first_name_address_is_not_claimed_without_position(self):
        # alex@ rebuilds from every Alex; with no card to say whose it is,
        # the testimonial's Alex must not get it.
        people = [{"name": "Rob Kessler", "title": "VP", "email": ""}]
        sp.match_addresses(people, ["mara@kaizen-equity.com"], near={})
        self.assertEqual(people[0]["email"], "")

    def test_specific_convention_is_claimed_by_rebuild(self):
        people = [{"name": "Jane Doe", "title": "VP", "email": ""}]
        result = sp.match_addresses(people, ["jane.doe@firm.com"])
        self.assertEqual(people[0]["email"], "jane.doe@firm.com")
        self.assertEqual(result["pattern"], "first.last")

    def test_role_addresses_set_no_convention(self):
        people = [{"name": "Dev Okafor", "title": "CIO", "email": ""}]
        result = sp.match_addresses(people, ["contact@tidalfg.com"])
        self.assertEqual(result["pattern"], "")


class Website(unittest.TestCase):

    def test_full_name_is_guessed_first(self):
        self.assertEqual(sp.site_guesses("Tidal Financial Group")[0],
                         "https://www.tidalfinancialgroup.com")

    def test_one_shared_word_is_not_the_company(self):
        tidal_music = "<title>TIDAL | High Fidelity Music Streaming</title>"
        self.assertFalse(sp.names_company(tidal_music, "Tidal Financial Group"))

    def test_a_readable_namesake_is_not_read_past_the_real_site(self):
        pages = {
            "https://www.stepstonegroup.com": "<html><body>Checking your "
                                              "browser…</body></html>",
            "https://www.stepstone.com": "<title>The Stepstone Group | Jobs"
                                         "</title>",
        }
        home, _, why = sp.find_website("StepStone Group",
                                       fetch=fake_fetch(pages))
        self.assertEqual(home, "")
        self.assertIn("nothing readable", why)

    def test_a_known_domain_that_cannot_be_read_stops_the_search(self):
        pages = {"https://stepstonegroup.com": "<html></html>",
                 "https://www.stepstone.com": "<title>The Stepstone Group"
                                              "</title>"}
        home, _, _ = sp.find_website("StepStone Group", ("stepstonegroup.com",),
                                     fetch=fake_fetch(pages))
        self.assertEqual(home, "")


class EndToEnd(unittest.TestCase):

    def test_find_reads_team_page_not_homepage_quotes(self):
        pages = {"https://www.kaizenequitypartners.com": KAIZEN_HOME,
                 "https://www.kaizenequitypartners.com/team": KAIZEN_TEAM}
        result = sp.find("Kaizen Equity Partners", fetch=fake_fetch(pages))
        names = {p["name"] for p in result["people"]}
        self.assertIn("Rhys Calder", names)
        self.assertNotIn("Rob Kessler", names)
        self.assertNotIn("Sam Whitfield", names)
        self.assertEqual(result["mail_domain"], "kaizen-equity.com")
        self.assertEqual(result["pattern"], "first")
        rhys = next(p for p in result["people"] if p["name"] == "Rhys Calder")
        self.assertEqual(rhys["email"], "rhys@kaizen-equity.com")
        self.assertTrue(rhys["verified"])

    def test_injected_fetch_never_writes_the_registry_or_cache(self):
        pages = {"https://www.kaizenequitypartners.com": KAIZEN_HOME,
                 "https://www.kaizenequitypartners.com/team": KAIZEN_TEAM}
        with mock.patch.object(sp.company_formats, "save") as save, \
                mock.patch.object(sp, "_save_cache") as save_cache:
            sp.find("Kaizen Equity Partners", fetch=fake_fetch(pages))
        save.assert_not_called()
        save_cache.assert_not_called()


class SearchUsesTheSite(unittest.TestCase):
    """people_search: the site's people, address and mail domain flow through."""

    def _find(self, site_result, domain=""):
        from jobagent.people import people_search
        with mock.patch("jobagent.people.people_finder.find_firm", return_value=[]), \
                mock.patch("jobagent.people.team_pages.people", return_value=[]), \
                mock.patch("jobagent.people.site_people.find", return_value=site_result), \
                mock.patch.object(people_search.recent_contacts,
                                  "filter_contacts",
                                  side_effect=lambda c: (c, [])), \
                mock.patch.object(people_search.company_formats, "pattern_for",
                                  return_value=("", "unknown")):
            return people_search.find("Tidal Financial Group",
                                      role="Trade Analyst", domain=domain)

    def test_mail_domain_and_convention_come_from_the_site(self):
        site = {"people": [{"name": "Nora Vance", "title": "Vice President, Trading",
                            "email": "", "verified": True,
                            "source": "firm site (/about)"}],
                "mail_domain": "tidalfg.com", "pattern": "first.last",
                "pattern_evidence": "1 published address(es) use first.last",
                "notes": ["read 2 page(s)"]}
        out = self._find(site)
        self.assertEqual(out["domain"], "tidalfg.com")
        [jo] = out["candidates"]
        self.assertEqual(jo["email"], "nora.vance@tidalfg.com")
        self.assertIn("inferred", jo["address_basis"])

    def test_published_address_is_kept_as_published(self):
        site = {"people": [{"name": "Rhys Calder", "title": "Vice President",
                            "email": "rhys@kaizen-equity.com", "verified": True,
                            "source": "firm site (/team)"}],
                "mail_domain": "kaizen-equity.com", "pattern": "first",
                "pattern_evidence": "", "notes": []}
        out = self._find(site, domain="kaizen-equity.com")
        [rhys] = out["candidates"]
        self.assertEqual(rhys["email"], "rhys@kaizen-equity.com")
        self.assertEqual(rhys["address_basis"], "published on the firm's site")


class OnePerFirm(unittest.TestCase):

    def test_three_roles_at_one_firm_share_contacts(self):
        from jobagent.outreach import auto_outreach
        rows = [{"Company": "BMO", "Position": "Corporate Banking"},
                {"Company": "BMO", "Position": "FX Sales"},
                {"Company": "Tidal Financial Group", "Position": "Trade Analyst"},
                {"Company": "BMO", "Position": "FX Solutions"}]
        keep, covered = auto_outreach.one_per_firm(rows)
        self.assertEqual([r["Position"] for r in keep],
                         ["Corporate Banking", "Trade Analyst"])
        self.assertEqual(len(covered), 2)

    def test_default_is_three_per_team(self):
        from jobagent.outreach import auto_outreach
        self.assertEqual(auto_outreach.CONTACTS_PER_APPLICATION, 3)


class WhatItActuallyDrafts(unittest.TestCase):
    """The nightly run writes straight into Gmail, so what it writes is not a
    style preference -- it is what the sender finds in Drafts in the morning."""

    def setUp(self):
        import os
        from jobagent.outreach import style_profile
        if not os.path.exists(os.path.join(ROOT, style_profile.CORPUS)):
            self.skipTest("no corpus on this machine")
        from jobagent.outreach import auto_outreach, draft_writer
        self.auto_outreach = auto_outreach
        self.contact = {"name": "Nora Vance", "title": "Associate",
                        "email": "nora@unorthodoxventures.com"}
        self.body = auto_outreach._body(
            "Unorthodox Ventures", "Investment Analyst", self.contact, [])
        from jobagent.core import sender_profile
        self.subject = (sender_profile.subject_prefix() + " "
                        + draft_writer.subject_topic("Investment Analyst")
                        + " at Unorthodox Ventures")

    def test_the_generated_body_passes_the_gate(self):
        """It failed on nine counts while writing to Gmail unchecked --
        the adjective-form ask, "If you'd be open to", "conversation", a
        hyphenated 15-20, no "for your reference", and his contact block."""
        from jobagent.outreach import style_check
        self.assertEqual(style_check.check(self.body, self.subject), [])

    def test_the_sign_off_carries_no_contact_details(self):
        """`signature_contact` under the sign-off appears in 0 of the 345
        emails he has sent, and the bank redacts the phone, so Gmail
        rendered a literal "REDACTED" beneath his name."""
        self.assertNotIn("@", self.body.split("Best regards,")[-1])
        self.assertNotIn("REDACTED", self.body.upper())
        self.assertEqual(self.auto_outreach._signoff().count("\n"), 0)

    def test_the_introduction_is_not_said_twice(self):
        """intro_opening() is already the whole introduction. Appending a
        second degree clause produced "...currently work at <employer>.
        graduated from <school> with a degree in <degree>." in every draft
        this function ever wrote."""
        from jobagent.core import sender_profile
        self.assertEqual(
            self.body.count(f"degree in {sender_profile.get('degree')}"), 1)
        self.assertNotIn(". graduated", self.body)

    def test_three_per_firm_means_three_in_total(self):
        """It meant three more every night. Kaizen Equity collected rhys@ on
        the 21st and nora@ and tomas@ on the 23rd, and the pass after that
        would have added three strangers at CAE in the same week as a reply
        to the person who had interviewed him."""
        from unittest import mock
        from jobagent.people import recent_contacts
        with mock.patch.object(recent_contacts, "emailed_since",
                               return_value={"rcalder@caeonline.com": "x"}):
            held = self.auto_outreach._covered_at(
                "caeonline.com", {"nvance@caeonline.com"})
        self.assertEqual(held, {"rcalder@caeonline.com",
                                "nvance@caeonline.com"})
        # Another firm's addresses are not this firm's coverage.
        with mock.patch.object(recent_contacts, "emailed_since",
                               return_value={"a@stonex.com": "x"}):
            self.assertEqual(
                self.auto_outreach._covered_at("caeonline.com", set()), set())

    def test_one_person_is_not_drafted_under_two_conventions(self):
        from jobagent.people import recent_contacts
        held = {"nora@unorthodoxventures.com"}
        self.assertEqual(
            recent_contacts.already_addressed(
                "Nora Vance", "unorthodoxventures.com", held, {}),
            "nora@unorthodoxventures.com")
        # A different person at the same firm is still reachable, and the
        # same name at another firm is a different person.
        self.assertFalse(recent_contacts.already_addressed(
            "Bob Example", "unorthodoxventures.com", held, {}))
        self.assertFalse(recent_contacts.already_addressed(
            "Nora Vance", "kalshi.com", held, {}))


if __name__ == "__main__":
    unittest.main()
