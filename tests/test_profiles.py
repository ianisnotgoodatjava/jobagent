"""Reading a person's background (degrees, past firms, licences) and using it.

Every name, firm and address here is invented; nothing touches the network.
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jobagent.core import affinity, bio_facts
from jobagent.people import people_db, people_finder, people_search, site_people

BIO = (
    "Jane Roe is a Vice President at Acme Advisors. Prior to joining Acme, "
    "Jane was an Associate at Moody's Investors Service, where she covered "
    "CMBS. She holds a B.S. in Finance from New York University and an MBA "
    "from the Wharton School of the University of Pennsylvania. Jane has over "
    "12 years of experience in structured finance, is a CFA charterholder "
    "and is fluent in Mandarin and Spanish.")


class BioFacts(unittest.TestCase):

    def test_degrees_from_a_sentence(self):
        edu = bio_facts.education(BIO)
        self.assertEqual([(e["degree"], e["school"]) for e in edu], [
            ("BS", "New York University"),
            ("MBA", "Wharton School of the University of Pennsylvania")])
        self.assertEqual(edu[0]["field"], "Finance")

    def test_degrees_from_an_education_list(self):
        text = ("Education: MBA, Strategy & Innovation, Boston University\n"
                "Education: B.S., Finance, New York University, 2015")
        self.assertEqual(bio_facts.education(text), [
            {"school": "Boston University", "degree": "MBA",
             "field": "Strategy & Innovation", "year": ""},
            {"school": "New York University", "degree": "BS",
             "field": "Finance", "year": "2015"}])

    def test_a_plural_degree_is_not_read_as_a_field(self):
        """"Masters of X" is written as often as "Master's of X".

        [Mm]aster(?:'s)? matched only "Master", so the leftover plural was
        read as the start of the field and a real Ankura bio stored
        "Masters of Architecture" as the field "s of Architecture".
        """
        self.assertEqual(
            bio_facts.education("Education: Masters of Architecture, "
                                "Massachusetts Institute of Technology"),
            [{"school": "Massachusetts Institute of Technology",
              "degree": "Master's", "field": "Architecture", "year": ""}])

    def test_a_plural_spelled_out_degree_keeps_its_abbreviation(self):
        self.assertEqual(
            bio_facts.education("Education: Bachelors of Science in Finance, "
                                "New York University"),
            [{"school": "New York University", "degree": "BS",
              "field": "Finance", "year": ""}])
        self.assertEqual(
            bio_facts.education("Education: Masters of Business "
                                "Administration, Columbia University"),
            [{"school": "Columbia University", "degree": "MBA",
              "field": "", "year": ""}])

    def test_each_school_gets_its_own_year(self):
        edu = bio_facts.education(
            "He received an M.S. from Columbia University and a B.A. from "
            "Fordham University in 2012.")
        self.assertEqual([e["year"] for e in edu], ["", "2012"])

    def test_past_employers_and_the_title_held(self):
        jobs = bio_facts.employers(BIO, "Acme Advisors")
        self.assertEqual(jobs, [{"firm": "Moody's Investors Service",
                                 "title": "Associate"}])

    def test_a_list_of_past_firms(self):
        jobs = bio_facts.employers(
            "He began his career at Goldman Sachs and previously worked at "
            "Morgan Stanley, Citi and Barclays.", "")
        self.assertEqual({j["firm"] for j in jobs},
                         {"Goldman Sachs", "Morgan Stanley", "Citi", "Barclays"})

    def test_dotted_firm_names_survive(self):
        jobs = bio_facts.employers("She joined Acme from J.P. Morgan in 2019.", "Acme")
        self.assertEqual([j["firm"] for j in jobs], ["J.P. Morgan"])

    def test_current_firm_is_not_a_past_employer(self):
        self.assertEqual(bio_facts.employers(
            "Prior to joining Acme, she spent six years at Acme.", "Acme"), [])

    def test_credentials_languages_and_experience(self):
        facts = bio_facts.read(BIO, "Acme")
        self.assertEqual(facts["credentials"], ["CFA"])
        self.assertEqual(facts["languages"], ["Mandarin", "Spanish"])
        self.assertEqual(facts["years_experience"], 12)

    def test_a_bio_with_no_background_states_nothing(self):
        facts = bio_facts.read("Kim Doe is a Managing Director in New York. "
                               "She leads client coverage across the Americas.")
        self.assertEqual((facts["education"], facts["employment"],
                          facts["credentials"], facts["languages"]),
                         ([], [], [], []))

    def test_two_listed_certifications_that_are_not_designations(self):
        text = ("Certifications: Certified Fraud Examiner\n"
                "Certifications: Introduction to AI (Certificate), 2025\n"
                "Certifications: Licensed Professional Engineer")
        # The dated course is not a credential, however the page files it.
        self.assertEqual(bio_facts.credentials(text), ["CFE", "PE"])

    def test_school_names_that_used_to_come_out_wrong(self):
        cases = {
            "She earned a BA from the University of Michigan, Ann Arbor.":
                "University of Michigan, Ann Arbor",
            "He holds a BBA from the College of William and Mary.":
                "College of William and Mary",
            "Graduate of Middlebury College.": "Middlebury College",
            "He studied International Finance at City University.": "City University",
            "He holds a BS from Virginia Polytechnic Institute and State University.":
                "Virginia Polytechnic Institute and State University",
        }
        for text, school in cases.items():
            self.assertEqual([e["school"] for e in bio_facts.education(text)],
                             [school], text)
        two = bio_facts.education("He has a bachelor's degree from Cornell "
                                  "University and New York University.")
        self.assertEqual(len(two), 2)
        self.assertEqual(bio_facts.education(
            "She has a Master's in Biomedical Engineering from the University."), [])

    def test_a_name_with_for_in_it_is_not_an_employer(self):
        self.assertEqual(bio_facts.employers(
            "She also served as an observer for the Organization for Security "
            "and Cooperation in Europe.", ""), [])
        self.assertEqual([j["firm"] for j in bio_facts.employers(
            "Previously, he served as Vice President of Investor Relations "
            "for KeySpan Energy.", "")], ["KeySpan Energy"])

    def test_school_spellings_agree(self):
        self.assertEqual(bio_facts.school_key("NYU Stern School of Business"),
                         bio_facts.school_key("New York University"))
        self.assertNotEqual(bio_facts.school_key("Columbia University"),
                            bio_facts.school_key("New York University"))

    def test_exams_name_a_desk(self):
        self.assertEqual(bio_facts.exam_code("Series 57TO"), "57")
        self.assertIn("trading", bio_facts.desk_from_exams(["7", "57", "63"]))
        self.assertEqual(bio_facts.desk_from_exams(["7", "63"]), "")
        self.assertTrue(bio_facts.supervises(["7", "24"]))


class Affinity(unittest.TestCase):
    ME = {"schools": ["New York University, College of Arts & Science"],
          "employers": ["Bank Example"], "languages": ["Mandarin"]}

    def test_same_employer_ignores_legal_names(self):
        self.assertTrue(affinity.same_employer("GOLDMAN, SACHS & CO.", "Goldman Sachs"))
        self.assertFalse(affinity.same_employer("Bank of America", "Bank Example"))

    def test_shared_school_employer_and_language(self):
        person = {"education": [{"school": "NYU Stern"}],
                  "employment": [{"firm": "BANK EXAMPLE SECURITIES INC."}],
                  "languages": ["Mandarin"]}
        total, why = affinity.score(person, self.ME)
        self.assertEqual(total, affinity.SCHOOL_WEIGHT + affinity.EMPLOYER_WEIGHT
                         + affinity.LANGUAGE_WEIGHT)
        self.assertEqual(len(why), 3)

    def test_nothing_shared(self):
        self.assertEqual(affinity.score({"education": [{"school": "Yale University"}]},
                                        self.ME), (0.0, []))

    def test_profile_from_resume_shape(self):
        me = affinity.me_from_resume({
            "profile": {"education": {"school": "New York University"},
                        "languages": ["English (native)", "Mandarin (native)"]},
            "experience": [{"company": "Firm A"}, {"company": "Firm A"}]})
        self.assertEqual(me, {"schools": ["New York University"],
                              "employers": ["Firm A"], "languages": ["Mandarin"]})


class Ranking(unittest.TestCase):

    def test_licences_stand_in_for_a_missing_title(self):
        trader = {"name": "Ann Example", "title": "", "exams": ["7", "57"]}
        rep = {"name": "Bob Example", "title": "", "exams": ["7", "63"]}
        ranked = people_search.rank([rep, trader], "Trading Analyst", me={})
        self.assertEqual(ranked[0]["name"], "Ann Example")
        self.assertIn("FINRA licences", ranked[0]["desk_reasons"][0])

    def test_shared_school_breaks_a_tie_within_a_band(self):
        a = {"name": "Ann Example", "title": "Vice President"}
        b = {"name": "Bob Example", "title": "Vice President",
             "education": [{"school": "New York University"}]}
        me = {"schools": ["New York University"]}
        ranked = people_search.rank([a, b], "Credit Analyst", me=me)
        self.assertEqual(ranked[0]["name"], "Bob Example")

    def test_shared_school_never_lifts_a_senior_over_the_band(self):
        md = {"name": "Ann Example", "title": "Managing Director",
              "education": [{"school": "New York University"}]}
        vp = {"name": "Bob Example", "title": "Vice President"}
        ranked = people_search.rank([md, vp], "Credit Analyst",
                                    me={"schools": ["New York University"]})
        self.assertEqual(ranked[0]["name"], "Bob Example")


class TitlelessFinraPeople(unittest.TestCase):

    def test_licensed_a_few_years_reads_as_mid_level(self):
        c = {"name": "Ann Example", "title": "", "years": 5}
        people_search.score(c, "Credit Analyst", me={})
        self.assertEqual(c["band"], people_search.BAND_MID)

    def test_licensed_twenty_years_stays_unknown(self):
        c = {"name": "Ann Example", "title": "", "industry_start": "2001-01-01"}
        people_search.score(c, "Credit Analyst", me={})
        self.assertEqual(c["band"], people_search.BAND_UNKNOWN)


class OrgChartCompany(unittest.TestCase):

    def test_a_same_named_foreign_company_is_refused(self):
        from jobagent.people import team_pages
        page = '<a href="https://www.linkedin.com/company/acme-srl">x</a>'
        self.assertIn("acme-srl", team_pages.foreign(page, "US"))
        abroad = '"addressCountry":"IT","addressCountry":"DE"'
        self.assertIn("IT", team_pages.foreign(abroad, "US"))
        home = '"addressCountry":"US" linkedin.com/company/acme-group'
        self.assertEqual(team_pages.foreign(home, "US"), "")
        self.assertEqual(team_pages.foreign(page, ""), "")


DETAIL = {"hits": {"hits": [{"_source": {"content": json.dumps({
    "productExamCategory": [{"examCategory": "Series 79TO",
                             "examName": "Investment Banking Representative",
                             "examTakenDate": "3/1/2019"}],
    "stateExamCategory": [{"examCategory": "Series 63", "examName": "State Law",
                           "examTakenDate": "2/1/2018"}],
    "currentEmployments": [{"firmName": "ACME SECURITIES LLC",
                            "registrationBeginDate": "6/1/2021",
                            "branchOfficeLocations": [{"city": "NEW YORK",
                                                       "state": "NY"}]}],
    "previousEmployments": [{"firmName": "EXAMPLE CAPITAL MARKETS LLC",
                             "city": "NEW YORK", "state": "NY",
                             "registrationBeginDate": "2/1/2018",
                             "registrationEndDate": "5/30/2021"}],
})}}]}}


class Finra(unittest.TestCase):

    def test_details_reduce_the_record(self):
        d = people_finder.details("123", get=lambda url: DETAIL)
        self.assertEqual([e["code"] for e in d["exams"]], ["Series 79TO", "Series 63"])
        self.assertEqual(d["employment"][0], {"firm": "ACME SECURITIES LLC",
                                              "city": "New York", "state": "NY",
                                              "start": "2021-06-01", "end": ""})
        self.assertEqual(d["employment"][1]["end"], "2021-05-30")

    def test_no_record(self):
        self.assertEqual(people_finder.details("9", get=lambda url: {}), {})


class Storage(unittest.TestCase):

    def setUp(self):
        self.db = people_db.connect(":memory:")

    def test_a_bio_is_read_into_rows(self):
        people_db.record(self.db, "Acme Advisors", [
            {"name": "Jane Roe", "title": "Vice President", "bio": BIO,
             "url": "https://acme.example/team/jane-roe", "source": "firm site bio"}])
        p = people_db.at_company(self.db, "Acme Advisors")[0]
        self.assertEqual([e["school"] for e in p["education"]],
                         ["New York University",
                          "Wharton School of the University of Pennsylvania"])
        self.assertEqual([e["firm"] for e in p["employment"]],
                         ["Moody's Investors Service"])
        self.assertEqual((p["credentials"], p["languages"], p["years_experience"]),
                         (["CFA"], ["Mandarin", "Spanish"], 12))
        self.assertEqual(p["bio_url"], "https://acme.example/team/jane-roe")

    def test_finra_record_and_its_freshness(self):
        finra = people_finder.details("123", get=lambda url: DETAIL)
        people_db.record(self.db, "Acme", [
            {"name": "Ann Example", "title": "", "crd": "123",
             "started": "2018-02-01", "source": "FINRA (ACME SECURITIES LLC)",
             "finra": finra}])
        p = people_db.at_company(self.db, "Acme", "Investment Banking Analyst")[0]
        self.assertEqual(sorted(p["exams"]), ["63", "79"])
        self.assertEqual(len(p["employment"]), 2)
        self.assertEqual(p["crd"], "123")
        self.assertGreater(p["desk_score"], 0)
        self.assertTrue(people_db.finra_checked(self.db, "Acme", "Ann Example"))
        self.assertFalse(people_db.finra_checked(self.db, "Acme", "Bob Example"))

    def test_the_same_job_from_two_sources_is_one_row(self):
        people_db.record(self.db, "Acme", [
            {"name": "Ann Example", "source": "FINRA",
             "employment": [{"firm": "GOLDMAN, SACHS & CO.", "start": "2010-01-01"}]},
            {"name": "Ann Example", "source": "firm site bio",
             "bio": "Prior to joining Acme, she spent six years at Goldman Sachs."}])
        p = people_db.at_company(self.db, "Acme")[0]
        self.assertEqual(len(p["employment"]), 1)

    def test_firm_format_is_kept_and_never_erased(self):
        people_db.record_firm(self.db, "Acme", website="https://acme.example",
                              domain="acme.example", pattern="first.last",
                              basis="3 published addresses")
        people_db.record_firm(self.db, "Acme", pattern="")
        f = people_db.firm(self.db, "Acme")
        self.assertEqual((f["domain"], f["pattern"]), ("acme.example", "first.last"))

    def test_an_old_file_gains_the_new_columns(self):
        import sqlite3
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "people.db")
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, company_key TEXT,"
                    " company TEXT, name TEXT, name_key TEXT, title TEXT,"
                    " location TEXT, email TEXT, email_basis TEXT, verified INT,"
                    " first_seen TEXT, last_seen TEXT)")
        old.commit()
        old.close()
        db = people_db.connect(path)
        cols = {r["name"] for r in db.execute("PRAGMA table_info(people)")}
        self.assertTrue({"crd", "bio", "linkedin", "industry_start"} <= cols)


class BioPage(unittest.TestCase):

    PAGE = """<html><body><h1>Jane Roe</h1><p>Vice President</p>
    <p>Jane Roe advises lenders on structured credit transactions and has
    worked across the capital structure for a decade or more.</p>
    <h3>Education</h3><ul><li>MBA, Finance, Boston University</li>
    <li>BA, Economics, New York University</li></ul>
    <h3>Certifications</h3><ul><li>Certified Fraud Examiner</li></ul>
    <a href="https://www.linkedin.com/in/jane-roe-123">LinkedIn</a>
    <a href="https://www.linkedin.com/company/acme">Acme</a>
    </body></html>"""

    def test_prose_keeps_paragraphs_and_listed_sections(self):
        text = site_people.prose(self.PAGE)
        self.assertIn("advises lenders", text)
        self.assertIn("Education: MBA, Finance, Boston University", text)
        self.assertEqual([e["school"] for e in bio_facts.education(text)],
                         ["Boston University", "New York University"])
        self.assertEqual(bio_facts.credentials(text), ["CFE"])

    def test_linkedin_only_when_the_firm_links_their_own_profile(self):
        self.assertEqual(site_people.linkedin_link(self.PAGE, "Jane Roe"),
                         "https://www.linkedin.com/in/jane-roe-123")
        self.assertEqual(site_people.linkedin_link(self.PAGE, "Bob Example"), "")


if __name__ == "__main__":
    unittest.main()
