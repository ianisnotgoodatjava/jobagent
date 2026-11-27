"""The people database, the desk matcher, and the sitemap/Wikidata discovery.

No test touches the network: discovery takes its fetch as an argument, and
the database runs in memory. Names here are invented.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jobagent.core import company_id
from jobagent.core import desk_match
from jobagent.people import people_db
from jobagent.people import site_discovery as sd
from jobagent.people import site_people as sp


class DeskMatch(unittest.TestCase):
    def test_same_desk_under_a_different_name(self):
        # Shared words: none. The old count scored this 0.
        score, why = desk_match.score("Vice President, Securitization",
                                      "Structured Finance Analyst, CMBS")
        self.assertGreater(score, 0)
        self.assertTrue(any("structured" in w for w in why))

    def test_exact_word_beats_family(self):
        exact, _ = desk_match.score("VP, CMBS Surveillance", "CMBS Analyst")
        family, _ = desk_match.score("VP, ABS Trading", "CMBS Analyst")
        self.assertGreater(exact, family)

    def test_stems_meet(self):
        score, _ = desk_match.score("Director, Underwriting", "Underwriter")
        self.assertEqual(score, 1.0)

    def test_generic_words_prove_nothing(self):
        score, _ = desk_match.score("Vice President, Global Markets",
                                    "Senior Analyst, Capital Markets")
        self.assertEqual(score, 0)

    def test_internal_audit_is_not_investment_consulting(self):
        score, _ = desk_match.score("VP, Internal Audit",
                                    "Internal Investment Consultant")
        self.assertEqual(score, 0)

    def test_office(self):
        self.assertTrue(desk_match.location_match("New York, NY", "New York"))
        self.assertFalse(desk_match.location_match("Charlotte", "New York"))


class Database(unittest.TestCase):
    def setUp(self):
        self.db = people_db.connect(":memory:")

    def test_one_person_two_sources_is_corroborated(self):
        people_db.record(self.db, "Acme Capital", [
            {"name": "Jane Q. Doe", "title": "Vice President, CMBS",
             "source": "firm site (/team)", "verified": True}])
        people_db.record(self.db, "Acme Capital LLC", [
            {"name": "Jane Doe", "title": "", "source": "FINRA (ACME)"}])
        [jane] = people_db.at_company(self.db, "Acme Capital", "CMBS Analyst")
        self.assertEqual(jane["corroboration"], 2)
        self.assertEqual(jane["title"], "Vice President, CMBS")

    def test_inferred_never_overwrites_published(self):
        people_db.record(self.db, "Acme", [
            {"name": "Jane Doe", "email": "jane@acme.com", "verified": True,
             "source": "firm site"}])
        people_db.record(self.db, "Acme", [
            {"name": "Jane Doe", "email": "jdoe@acme.com",
             "address_basis": "flast (inferred)", "source": "org chart"}])
        [jane] = people_db.at_company(self.db, "Acme")
        self.assertEqual(jane["email"], "jane@acme.com")

    def test_band_before_desk_then_corroboration(self):
        people_db.record(self.db, "Acme", [
            {"name": "Mary Major", "title": "Managing Director, CMBS",
             "source": "firm site"},
            {"name": "Vic Vee", "title": "Vice President, Rates",
             "source": "firm site"},
            {"name": "Sam Same", "title": "Vice President, Securitization",
             "source": "firm site"},
            {"name": "Cora Two", "title": "Vice President, Securitization",
             "source": "firm site"}])
        people_db.record(self.db, "Acme", [
            {"name": "Cora Two", "title": "", "source": "FINRA"}])
        order = [p["name"] for p in
                 people_db.at_company(self.db, "Acme", "CMBS Analyst")]
        self.assertEqual(order, ["Cora Two", "Sam Same", "Vic Vee", "Mary Major"])

    def test_applications_are_linked(self):
        people_db.record(self.db, "Acme", [
            {"name": "Jane Doe", "title": "VP, CMBS", "source": "firm site"}])
        people_db.for_application(self.db, "Acme", "CMBS Analyst")
        self.assertEqual(people_db.stats(self.db)["applications_linked"], 1)

    def test_single_names_are_not_people(self):
        self.assertEqual(people_db.record(self.db, "Acme", [
            {"name": "Jane", "source": "firm site"}]), 0)


class Rekeying(unittest.TestCase):
    """An alias added after a firm was stored has to reach the stored rows."""

    def setUp(self):
        self.db = people_db.connect(":memory:")
        self._aliases = dict(company_id.ALIASES)

    def tearDown(self):
        company_id.ALIASES.clear()
        company_id.ALIASES.update(self._aliases)

    def test_an_alias_added_later_moves_the_rows_it_now_covers(self):
        people_db.record(self.db, "The Orrin Corporation", [
            {"name": "Nora Vance", "title": "VP, Rates",
             "source": "firm site"}])
        self.assertEqual(
            [r["company_key"] for r in self.db.execute(
                "SELECT company_key FROM people")], ["theorrin"])
        company_id.ALIASES["theorrin"] = "orrin"
        moved = people_db.remap_company_keys(self.db)
        self.assertEqual([(m[0], m[1], m[3]) for m in moved],
                         [("theorrin", "orrin", "moved")])
        [person] = people_db.at_company(self.db, "Orrin")
        self.assertEqual(person["name"], "Nora Vance")

    def test_a_person_under_both_keys_is_merged_not_duplicated(self):
        people_db.record(self.db, "Orrin", [
            {"name": "Nora Vance", "title": "VP, Rates",
             "source": "firm site (/team)"}])
        people_db.record(self.db, "The Orrin Corporation", [
            {"name": "Nora Vance", "title": "", "source": "FINRA (ORRIN)"}])
        company_id.ALIASES["theorrin"] = "orrin"
        moved = people_db.remap_company_keys(self.db)
        self.assertEqual([m[3] for m in moved], ["merged"])
        people = people_db.at_company(self.db, "Orrin")
        self.assertEqual(len(people), 1)
        # The sighting came across, so the firm site and FINRA still count as
        # two sources for her -- merging must not cost corroboration.
        self.assertEqual(people[0]["corroboration"], 2)
        self.assertEqual(people[0]["title"], "VP, Rates")

    def test_nothing_moves_when_every_key_is_current(self):
        people_db.record(self.db, "Orrin", [
            {"name": "Nora Vance", "title": "VP", "source": "firm site"}])
        self.assertEqual(people_db.remap_company_keys(self.db), [])

    def test_the_firm_row_follows_its_people(self):
        people_db.record(self.db, "The Orrin Corporation", [
            {"name": "Nora Vance", "title": "VP", "source": "firm site"}])
        people_db.record_firm(self.db, "The Orrin Corporation",
                              website="https://orrin.example",
                              domain="orrin.example", pattern="first.last",
                              basis="published on the team page")
        company_id.ALIASES["theorrin"] = "orrin"
        people_db.remap_company_keys(self.db)
        firm = people_db.firm(self.db, "Orrin")
        self.assertEqual(firm["domain"], "orrin.example")
        self.assertEqual(firm["pattern"], "first.last")
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM firms").fetchone()[0], 1)


class Discovery(unittest.TestCase):
    def test_bio_urls(self):
        self.assertEqual(sd.bio_url("https://acme.com/team/jane-doe"), "Jane Doe")
        self.assertEqual(sd.bio_url("https://acme.com/our-people/jan-van-dyke/"),
                         "Jan van Dyke")
        self.assertEqual(sd.bio_url("https://acme.com/team/investment-team"), "")
        self.assertEqual(sd.bio_url("https://acme.com/news/jane-doe-joins"), "")
        self.assertEqual(sd.bio_url("https://other.com/team/jane-doe",
                                    host="acme.com"), "")

    def test_sitemap_index_followed_people_first(self):
        pages = {
            "https://acme.com/robots.txt":
                "User-agent: *\nDisallow: /team/secret-person\n"
                "Sitemap: https://acme.com/sitemap_index.xml",
            "https://acme.com/sitemap_index.xml":
                "<sitemapindex><sitemap><loc>https://acme.com/post-sitemap.xml"
                "</loc></sitemap><sitemap><loc>https://acme.com/team-sitemap.xml"
                "</loc></sitemap></sitemapindex>",
            "https://acme.com/team-sitemap.xml":
                "<urlset><url><loc>https://acme.com/team/jane-doe/</loc></url>"
                "<url><loc>https://acme.com/team/secret-person</loc></url>"
                "<url><loc>https://acme.com/team/</loc></url></urlset>",
        }
        fetch = lambda u: pages[u]
        parser = sd.robots("https://acme.com", fetch)
        found = sd.bio_pages("https://acme.com", fetch, parser)
        self.assertEqual(found, [("https://acme.com/team/jane-doe", "Jane Doe")])

    def test_wikidata_wants_the_company_not_a_namesake(self):
        def get_json(params):
            if params["action"] == "wbsearchentities":
                return {"search": [
                    {"id": "Q1", "label": "Moody's Mood for Love",
                     "description": "1952 song"},
                    {"id": "Q2", "label": "Moody's Corporation",
                     "description": "American business and financial "
                                    "services company"}]}
            return {"entities": {"Q2": {"claims": {"P856": [
                {"mainsnak": {"datavalue": {"value": "https://www.moodys.com"}}}]}}}}
        url, why = sd.wikidata_website("Moody's", get_json)
        self.assertEqual(url, "https://www.moodys.com")
        self.assertIn("Q2", why)

    def test_wikidata_short_names_ignore_aliases(self):
        # Both of these happened against the live API.
        hits = {"VRC": {"id": "Q148", "label": "People's Republic of China",
                        "aliases": ["VRC", "PRC"],
                        "description": "country in East Asia"},
                "DRW": {"id": "Q17609", "label": "Darwin International Airport",
                        "aliases": ["DRW"],
                        "description": "airport in Darwin, Australia"}}
        for name, hit in hits.items():
            url, _ = sd.wikidata_website(name, lambda p, h=hit: (
                {"search": [h]} if p["action"] == "wbsearchentities" else
                {"entities": {h["id"]: {"claims": {"P856": [{"mainsnak": {
                    "datavalue": {"value": "https://example.org"}}}]}}}}))
            self.assertEqual(url, "", name)

    def test_section_slugs_are_not_names(self):
        for slug in ("aerospace-and-defense", "analyst-coverage",
                     "financial-services", "our-partner-program",
                     "terms-of-use"):
            self.assertEqual(sd.name_from_slug(slug), "", slug)
        self.assertEqual(sd.name_from_slug("kim-park"), "Kim Park")

    def test_people_directory_is_learned(self):
        people = [f"https://ankura.com/experts-and-advisors/{s}" for s in (
            "nora-vance", "rhys-j-calder", "bob-example",
            "ann-example", "ana-lopez", "kim-park")]
        news = [f"https://ankura.com/news/{s}" for s in (
            "ankura-named-top-firm-2025", "experts-acknowledged-by-whos-who",
            "a-b", "outstanding-results", "ankura-expands-in-asia")]
        industries = [f"https://ankura.com/industries/{s}" for s in (
            "aerospace-and-defense", "construction-and-infrastructure",
            "financial-services", "energy", "healthcare-and-life-sciences")]
        news += industries
        dirs = sd.people_directories(people + news)
        self.assertEqual(dirs, frozenset({"experts-and-advisors"}))
        self.assertEqual(sd.bio_url(people[1], "ankura.com", dirs),
                         "Rhys J Calder")
        self.assertEqual(sd.bio_url(news[2], "ankura.com", dirs), "")

    def test_wikidata_nothing_by_that_name(self):
        url, _ = sd.wikidata_website(
            "Acme", lambda p: {"search": [{"id": "Q9", "label": "Acme Records",
                                    "description": "record company"}]})
        self.assertEqual(url, "")


class BioPages(unittest.TestCase):
    """A team grid drawn by JavaScript: the shell names nobody, the sitemap
    names everyone, and each bio is plain HTML."""

    SITE = {
        "https://www.acmecapital.com":
            "<html><head><title>Acme Capital</title></head>"
            "<body><div id='root'></div></body></html>",
        "https://www.acmecapital.com/robots.txt": "User-agent: *\nAllow: /",
        "https://www.acmecapital.com/sitemap.xml":
            "<urlset><url><loc>https://www.acmecapital.com/team/jane-doe</loc>"
            "</url><url><loc>https://www.acmecapital.com/team/john-roe</loc>"
            "</url><url><loc>https://www.acmecapital.com/team/not-here</loc>"
            "</url></urlset>",
        "https://www.acmecapital.com/team/jane-doe":
            "<html><body><h1>Jane Doe</h1><p>Vice President, Structured Credit"
            "</p><a href='mailto:jane.doe@acmecapital.com'>Email</a></body></html>",
        "https://www.acmecapital.com/team/john-roe":
            "<html><body><h1>John Roe, CFA</h1><h2>Director</h2></body></html>",
        "https://www.acmecapital.com/team/not-here":
            "<html><body><h1>Page not found</h1></body></html>",
    }

    def fetch(self, url):
        url = url.rstrip("/")
        if url not in self.SITE:
            raise OSError(url)
        return url, self.SITE[url]

    def test_people_from_bios(self):
        result = sp.find("Acme Capital", website="https://www.acmecapital.com",
                         fetch=self.fetch)
        names = {p["name"]: p for p in result["people"]}
        self.assertEqual(set(names), {"Jane Doe", "John Roe"})
        self.assertEqual(names["Jane Doe"]["title"],
                         "Vice President, Structured Credit")
        self.assertEqual(names["Jane Doe"]["email"], "jane.doe@acmecapital.com")
        self.assertTrue(names["John Roe"]["verified"])

    def test_a_heading_with_no_title_is_not_a_person(self):
        page = ("<html><head><title>Eligible Contract Participant - DRW"
                "</title></head><body><h1>Eligible Contract Participant</h1>"
                "<p>Representations for counterparties.</p></body></html>")
        self.assertIsNone(sp.bio_person(page, "Eligible Contract Participant"))

    def test_title_card_after_the_page_title(self):
        page = ("<html><head><title>Nora Vance - Ankura.com</title>"
                "</head><body><nav>Industries</nav><nav>Services</nav>"
                "<nav>Experts</nav><nav>Insights</nav><nav>Careers</nav>"
                "<h1>Nora Vance</h1><p>Senior Managing Director</p>"
                "<p>Fairfield, CT</p></body></html>")
        person = sp.bio_person(page, "Nora Vance")
        self.assertEqual(person["title"], "Senior Managing Director")
        self.assertEqual(person["location"], "Fairfield, CT")

    def test_bio_budget_spreads_past_the_leadership(self):
        items = list(range(100))
        order = sp.spread(items, 10)
        self.assertEqual(sorted(order), items)
        self.assertEqual(order[:10], [0, 10, 20, 30, 40, 50, 60, 70, 80, 90])
        self.assertEqual(sp.spread([1, 2], 10), [1, 2])

    def test_bio_must_state_the_slug_name(self):
        self.assertIsNone(sp.bio_person("<h1>Page not found</h1>", "Not Here"))


if __name__ == "__main__":
    unittest.main()
