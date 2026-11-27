"""The tailored skills line: what may be on it, in what order, and what not.

Every person, firm and bullet here is invented. The rule under test is the
one resume_pdf already keeps for bullets: tailoring is selection and order
over what you wrote, never a word you could not defend in the interview.
"""

import copy
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jobagent.core import job_posting  # noqa: E402
from jobagent.core import skills  # noqa: E402

LISTED = ["Microsoft Office Suite", "Java", "Python", "R", "Tableau", "Excel",
          "Applied Math"]


def _bank():
    bullets = []
    tags = [["financial modeling", "valuation", "excel"],
            ["statistics", "time series", "python"],
            ["machine learning", "data analysis", "python"],
            ["credit", "underwriting", "risk"],
            ["research", "writing"], ["leadership"], ["presentation"],
            ["backtesting", "quantitative"], ["derivatives"], ["markets"],
            ["operations"], ["communication"]]
    for i, t in enumerate(tags):
        bullets.append({
            "id": f"b{i}", "company": "Example Capital" if i < 6 else "Example Bank",
            "role": "Analyst", "dates": "2025",
            "tags": t,
            "text": ("Built and maintained a three-statement model and wrote "
                     "weekly notes for the desk covering forty issuers, "
                     f"cutting turnaround by a third ({i})."),
        })
    return {
        "profile": {
            "name": "Jane Roe", "address": "New York, NY", "location": "New York, NY",
            "phone": "555-0100", "email": "jane.roe@example.com",
            "skills": list(LISTED),
            "languages": ["English (native)", "Spanish (fluent)"],
            "certifications": ["Bloomberg Terminal Training"],
            "citizenship": ["United States"],
            "education": {"school": "Example University", "degree": "B.A., Economics",
                          "dates": "2021 — 2025", "gpa": "3.8",
                          "coursework": ["Econometrics", "Corporate Finance"]},
        },
        "experience": [
            {"company": "Example Capital", "role": "Analyst", "location": "New York, NY",
             "dates": "2025 — Present", "section": "experience"},
            {"company": "Example Bank", "role": "Summer Analyst", "location": "New York, NY",
             "dates": "Summer 2024", "section": "experience"},
        ],
        "bullets": bullets,
        "projects": [],
    }


CREDIT = """Credit Analyst
Responsibilities
- Build financial models and perform credit analysis on middle-market borrowers
- Present credit memos to the investment committee
Qualifications
- Strong proficiency in Microsoft Excel; VBA a plus
- Coursework in statistics or econometrics; Python or R preferred
- Familiarity with Bloomberg and Capital IQ
- Understanding of accounting and financial statements
You will excel at communicating with our R&D partners."""


def _tailor(text, **kw):
    return skills.tailor(_bank(), job_posting.parse_text(text), **kw)


class TestNothingInvented:
    def test_what_you_cannot_show_is_a_gap_not_a_line(self):
        t = _tailor(CREDIT)
        for missing in ("VBA", "Capital IQ", "Accounting"):
            assert missing in t.gaps
            assert missing not in t.skills

    def test_every_added_skill_names_its_proof(self):
        t = _tailor(CREDIT)
        added = dict(t.added)
        assert added["Financial Modeling"].where == "tag"
        assert added["Statistics"].detail == "b1"
        assert added["Bloomberg"].where == "certification"
        assert all(label in t.skills for label in added)

    def test_a_listed_skill_is_never_dropped(self):
        t = _tailor(CREDIT, fits=lambda line: len(line) <= 3)
        assert all(s in t.skills for s in LISTED)
        assert not t.added            # the room came out of additions only


class TestOrder:
    def test_what_the_posting_asks_for_leads(self):
        t = _tailor(CREDIT)
        assert t.skills.index("Excel") < t.skills.index("Java")
        assert t.skills.index("Python") < t.skills.index("Microsoft Office Suite")
        assert {"Excel", "Python", "R"} <= set(t.moved_up)

    def test_your_spelling_is_kept(self):
        t = _tailor("Requirements\n- Proficient in MS Office and spreadsheets\n" * 30)
        assert "Microsoft Office Suite" in t.skills
        assert "Microsoft Office" not in t.skills

    def test_no_posting_means_no_change(self):
        t = _tailor("We are a great place to work. " * 30)
        assert t.skills == LISTED and not t.changed


class TestReadingPostings:
    @pytest.mark.parametrize("text,key", [
        ("You will excel at client service", "excel"),
        ("Partner with R&D on new products", "r"),
        ("Experience with JavaScript front ends", "java"),
        ("We offer flexible options for benefits", "derivatives"),
    ])
    def test_words_that_only_look_like_skills(self, text, key):
        assert not skills.mentions(text, skills.BY_KEY[key])

    @pytest.mark.parametrize("text,key", [
        ("Advanced Microsoft Excel", "excel"),
        ("Python, R, SQL", "r"),
        ("Python or R.", "r"),
        ("build three-statement models", "financial_modeling"),
        ("options pricing and swaps", "derivatives"),
    ])
    def test_and_ones_that_are(self, text, key):
        assert skills.mentions(text, skills.BY_KEY[key])

    def test_requirements_outweigh_prose(self):
        """Once under Qualifications beats twice in the company blurb."""
        posting = ("About us\nOur platform is written in Java, and Java runs "
                   "our trading stack.\nQualifications\n- Experience with Tableau "
                   "dashboards for reporting\n")
        ranked = [k for k, _ in skills.wanted(job_posting.parse_text(posting))]
        assert ranked.index("tableau") < ranked.index("java")


class TestOnePage:
    @pytest.fixture
    def bank(self, monkeypatch):
        from jobagent.apply import prep_package
        b = _bank()
        monkeypatch.setattr(prep_package, "load_bank", lambda path=None: copy.deepcopy(b))
        return b

    def test_the_tailored_resume_is_one_page(self, bank, tmp_path):
        from jobagent.apply import resume_pdf
        parsed = job_posting.parse_text(CREDIT)
        path = resume_pdf.build("risk_credit", parsed=parsed, company_name="Acme",
                                role_name="Credit Analyst", out_root=str(tmp_path),
                                quiet=True)
        assert resume_pdf.page_count(path) == 1

    def test_an_overflowing_page_sheds_added_skills_first(self, bank, tmp_path,
                                                          monkeypatch):
        from jobagent.apply import resume_pdf
        # Pretend every line with an addition spills onto a second page.
        monkeypatch.setattr(resume_pdf, "page_count", lambda path: 2)
        line = resume_pdf.tailored_skills(bank, job_posting.parse_text(CREDIT)).skills
        assert set(line) > set(LISTED)
        resume_pdf.build("risk_credit", parsed=job_posting.parse_text(CREDIT),
                         out_root=str(tmp_path), skills=line, quiet=True)
        assert line == [s for s in line if s in LISTED]      # trimmed in place
        assert set(line) == set(LISTED)                      # none of yours lost


class TestTheCommand:
    def test_a_copied_sentence_is_not_a_posting(self):
        from jobagent.apply import tailor
        assert not tailor.looks_like_posting("123 Main St, New York")
        assert tailor.looks_like_posting(CREDIT * 2)

    def test_tailor_writes_and_reports(self, tmp_path, monkeypatch):
        from jobagent.apply import prep_package, tailor
        b = _bank()
        monkeypatch.setattr(prep_package, "load_bank", lambda path=None: copy.deepcopy(b))
        out = tailor.tailor(CREDIT, company="Acme", role="Credit Analyst",
                            out_root=str(tmp_path))
        assert os.path.exists(out["path"])
        report = "\n".join(out["report"])
        assert "Financial Modeling" in report and "VBA" in report

    def test_the_agent_can_call_it_and_it_writes_no_mail(self):
        from jobagent.agent import toolbox
        assert "tailor_resume" in toolbox.TOOLS
        assert toolbox.TOOLS["tailor_resume"].writes is False


class TestLeadBulletsComeFromTheBank:
    """Which bullets lead a category is the resume owner's data, not code.

    The ids used to be written into prep_package.CATEGORIES, which put one
    person's resume structure in every copy of the code. They live in
    resume_bank.json now, under "category_leads".
    """

    @staticmethod
    def _bank():
        return {"bullets": [
            {"id": "alpha", "tags": ["valuation"], "text": "Built a valuation model."},
            {"id": "beta", "tags": ["python"], "text": "Wrote internal tooling."},
            {"id": "gamma", "tags": ["credit"], "text": "Reviewed loan files."},
        ]}

    PARSED = {"requirements": ["valuation experience"], "responsibilities": [],
              "text": ""}

    def test_the_banks_order_beats_the_postings_wording(self):
        from jobagent.apply import prep_package
        bank = self._bank()
        bank["category_leads"] = {"risk_credit": ["beta", "gamma"]}
        ranked = prep_package.rank_bullets(bank, self.PARSED, "risk_credit")
        assert [b["id"] for _, b in ranked][:2] == ["beta", "gamma"]

    def test_a_bank_without_the_key_still_ranks_every_category(self):
        from jobagent.apply import prep_package
        for category in prep_package.CATEGORIES:
            ranked = prep_package.rank_bullets(self._bank(), self.PARSED, category)
            assert len(ranked) == 3
            # with nothing leading, the posting's own wording decides
            assert ranked[0][1]["id"] == "alpha"

    def test_no_bullet_ids_are_written_into_the_code(self):
        from jobagent.apply import prep_package
        for name, spec in prep_package.CATEGORIES.items():
            assert not spec.get("lead"), f"{name} hard-codes lead bullets"

    def test_the_example_bank_documents_the_key(self):
        import json
        from jobagent.apply import prep_package
        with open(os.path.join(ROOT, "resume_bank.example.json"), encoding="utf-8") as f:
            bank = json.load(f)
        ids = ({b["id"] for b in bank.get("bullets", [])}
               | {p["id"] for p in bank.get("projects", [])})
        leads = bank.get("category_leads")
        assert leads, "the example should show what category_leads looks like"
        for category, lead in leads.items():
            assert category in prep_package.CATEGORIES
            assert set(lead) <= ids, f"{category} names ids the example lacks"
