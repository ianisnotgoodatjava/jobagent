"""Who shares something with you at a firm, and who the sweep checks.

The failure worth guarding against is not missing someone. It is reporting
someone whose "connection" is a guess -- from a name, from a market they
cover -- and then writing "as a fellow X" to a stranger who is not. So most
of these tests are refusals, and each refusal names its reason.

The traits here are placeholders on purpose. What a user shares with people
lives in their own gitignored profile, never in the repository.

No model, sheet or network: the research call is the only paid part, and
everything that decides something sits on either side of it.
"""

import datetime
from urllib.parse import unquote_plus

import pytest

from jobagent.core import shared_traits as st
from jobagent.people import common_ground as cg

TRAITS = [
    st.make("sport", "rowing", ["rowing", "rower", "crew"]),
    st.make("hobby", "chess"),
    st.make("language", "Portuguese"),
    st.make("school", "Example University", ["Example University", "EU Business School"]),
    st.make("heritage", "Portugal", ["Portugal", "Portuguese-American", "Lisbon", "Porto"]),
    st.make("group", "Toastmasters"),
    st.make("employer", "Example Corp"),
]


def _person(evidence, **extra):
    return {"name": "Alex Marlow", "title": "Analyst", "evidence": evidence,
            "source": "https://example.com/profile", **extra}


class TestWhatCounts:
    @pytest.mark.parametrize("evidence, label, kind", [
        ("B.A. Economics, Example University", "Example University", "school"),
        ("Captain of the varsity rowing team", "rowing", "sport"),
        ("Former collegiate rower", "rowing", "sport"),
        ("Outside work: chess and hiking", "chess", "hobby"),
        ("Languages: English, Portuguese (native)", "Portuguese", "language"),
        ("Grew up in Porto", "Portugal", "heritage"),
        ("Member, Toastmasters International", "Toastmasters", "group"),
        ("Previously an associate at Example Corp", "Example Corp", "employer"),
    ])
    def test_stated_traits_pass(self, evidence, label, kind):
        assert st.check(_person(evidence), TRAITS) == ""
        hit = st.match(evidence, TRAITS)
        assert (hit["label"], hit["kind"]) == (label, kind)

    def test_whole_words_only(self):
        """"crew" is a trait; "screwdriver" and "crewe" are not it."""
        assert st.match("Built a screwdriver company in Crewe", TRAITS) is None

    def test_strongest_kind_wins_when_two_match(self):
        hit = st.match("Portuguese-American; rows crew on weekends", TRAITS)
        assert hit["kind"] == "heritage"

    def test_coverage_next_to_a_real_background_keeps_it(self):
        evidence = "Covers Portugal equities; grew up in Lisbon"
        assert st.check(_person(evidence), TRAITS) == ""


class TestWhatIsRefused:
    @pytest.mark.parametrize("evidence, reason", [
        ("Surname is common in Portugal", "inferred"),
        ("Likely Portuguese given the name", "inferred"),
        ("Name suggests a Portugal background", "inferred"),
        ("Probably a rower, judging by the photo", "inferred"),
        ("Covers Portugal and Spain banks", "market they cover"),
        ("Analyst on Lisbon real estate", "market they cover"),
        ("Plays tennis", "names none"),
    ])
    def test_guesses_are_refused_with_a_reason(self, evidence, reason):
        assert reason in st.check(_person(evidence), TRAITS)

    def test_no_source_no_report(self):
        assert "source" in st.check(_person("Rowing, Example University",
                                            source=""), TRAITS)

    def test_one_word_name_is_not_a_person(self):
        assert st.check({**_person("chess"), "name": "Alex"}, TRAITS) == \
            "no full name"

    def test_no_traits_means_nothing_can_match(self):
        assert st.check(_person("chess"), []) == "no traits to look for"


class TestWhereTraitsComeFrom:
    def test_profile_resume_and_flags_merge_without_duplicates(self):
        profile = {"common_ground": [
            {"kind": "sport", "label": "rowing", "terms": ["rowing", "crew"]},
            {"kind": "school", "label": "Example University"}]}
        resume = {"schools": ["Example University"], "languages": ["Portuguese"],
                  "employers": ["Example Corp"]}
        traits = st.from_profile(profile, resume, extra=["hobby:chess"])
        assert [(t["kind"], t["label"]) for t in traits] == [
            ("hobby", "chess"), ("sport", "rowing"),
            ("school", "Example University"), ("language", "Portuguese"),
            ("employer", "Example Corp")]

    def test_only_uses_just_the_flags(self):
        profile = {"common_ground": [{"kind": "sport", "label": "rowing"}]}
        traits = st.from_profile(profile, {"schools": ["X"]},
                                 extra=["language:Portuguese|Brazilian Portuguese"],
                                 only_extra=True)
        assert len(traits) == 1
        assert traits[0]["terms"] == ["Portuguese", "Brazilian Portuguese"]

    def test_flag_without_a_kind_is_a_group(self):
        assert st.parse_flag("Toastmasters")["kind"] == "group"

    def test_unknown_kind_is_kept_as_a_group(self):
        assert st.make("astrology", "Leo")["kind"] == "group"

    def test_signature_ignores_order_and_case(self):
        a = [st.make("sport", "Rowing"), st.make("hobby", "chess")]
        b = [st.make("hobby", "Chess"), st.make("sport", "rowing")]
        assert st.signature(a) == st.signature(b)


RESPONSE = """Preamble the model wrote.

=== PERSON ===
NAME: Sam Rourke
TITLE: Associate, Investor Relations
TEAM: Investor Relations
PROFILE_URL: UNKNOWN
TRAIT: chess
EVIDENCE: Plays competitive chess
SOURCE: https://example.com/a
RELEVANCE: elsewhere at the firm

=== PERSON ===
NAME: Jordan Marlow
TITLE: Vice President, Structured Finance Credit
TEAM: Structured Finance
PROFILE_URL: https://example.com/jordan
TRAIT: rowing
EVIDENCE: Rowed crew at Example University
SOURCE: https://example.com/jordan
RELEVANCE: same team
WHY_RELEVANT: rates the deals this role analyzes

=== PERSON ===
NAME: Casey Guess
TITLE: Analyst, Structured Finance
TEAM: Structured Finance
EVIDENCE: surname suggests a Portuguese background
SOURCE: https://example.com/g
RELEVANCE: same team
"""


class TestReadingTheResearch:
    def test_blocks_parse_and_unknown_is_empty(self):
        people = st.parse(RESPONSE)
        assert [p["name"] for p in people] == [
            "Sam Rourke", "Jordan Marlow", "Casey Guess"]
        assert people[0]["profile_url"] == ""
        assert people[1]["relevance"] == st.SAME_TEAM

    def test_screen_ranks_nearest_the_role_and_refuses_the_guess(self):
        kept, refused = cg.screen(st.parse(RESPONSE),
                                  "Structured Finance Credit Analyst", TRAITS)
        assert [p["name"] for p in kept] == ["Jordan Marlow", "Sam Rourke"]
        # Rowing and a school both match; the school is the stronger kind.
        assert (kept[0]["trait"], kept[0]["kind"]) == (
            "Example University", "school")
        assert [(p["name"], p["refused"]) for p in refused] == [
            ("Casey Guess", "connection inferred, not stated")]

    def test_the_same_person_twice_is_reported_once(self):
        kept, refused = cg.screen(st.parse(RESPONSE)[:2] * 2, "", TRAITS)
        assert len(kept) == 2
        assert {p["refused"] for p in refused} == {"listed twice"}

    def test_prompt_lists_every_trait_and_no_one_in_particular(self):
        text = cg.SYSTEM.replace("{TRAITS}", cg.describe(TRAITS))
        for t in TRAITS:
            assert t["label"] in text
        assert "name or photo" in text


class TestSearchLinks:
    def test_role_words_drop_seniority_and_place(self):
        assert st.role_terms(
            "Senior Credit Analyst - Structured Finance, New York") == [
            "credit", "structured", "finance"]

    def test_linkedin_search_quotes_company_traits_and_desk(self):
        (label, url), (wide_label, wide) = st.linkedin_urls(
            "Normere's", "Credit Analyst", TRAITS)
        assert label == "near the role"
        keywords = unquote_plus(url.split("keywords=")[1])
        assert keywords.startswith('"Normere\'s" AND (')
        assert '"rowing"' in keywords and '"Toastmasters"' in keywords
        assert keywords.endswith("AND (credit)")
        assert "credit" not in unquote_plus(wide.split("keywords=")[1])

    def test_no_traits_no_search(self):
        assert st.linkedin_urls("Normere's", "Analyst", []) == []

    def test_web_query_stays_under_googles_word_limit(self):
        many = TRAITS + [st.make("group", f"Club Number {i}") for i in range(20)]
        query = st.xray_query("Halvorsen Mutual Banking Corporation",
                              "Senior Credit Risk Analyst, Leveraged Finance",
                              many)
        assert query.startswith('site:linkedin.com/in "Halvorsen')
        assert len(query.split()) <= 32


TODAY = datetime.date(2026, 11, 20)


def _row(company, position="Analyst", applied="2026-11-18", status="applied"):
    return {"Company": company, "Position": position,
            "Applied Draes": applied, "Status": status}


class TestWhichApplicationsTheSweepChecks:
    def test_recent_open_applications_one_per_firm(self):
        rows = [_row("Brightwater Ratings", "Credit Analyst"),
                _row("Brightwater Ratings", "Associate Director"),
                _row("Wexmoor Securities", applied="2026-11-19"),
                _row("Old Co", applied="2026-09-01"),
                _row("Said No", status="rejected"),
                _row("Crestline Staffing")]
        picked = cg.pending(rows, TRAITS, days_back=14, store={}, today=TODAY)
        assert [r["Company"] for r in picked] == ["Wexmoor Securities", "Brightwater Ratings"]

    def test_checked_under_these_traits_is_not_paid_for_twice(self):
        from jobagent.core import app_identity
        store = {app_identity.key("Selhaven", "Analyst"): {
            "checked": "2026-01-01", "company_key": "unused",
            "traits": st.signature(TRAITS)}}
        assert cg.pending([_row("Selhaven")], TRAITS, store=store,
                          today=TODAY) == []

    def test_a_new_trait_is_a_reason_to_look_again(self):
        from jobagent.core import app_identity
        store = {app_identity.key("Selhaven", "Analyst"): {
            "checked": "2026-11-19", "company_key": "unused",
            "traits": st.signature(TRAITS[:2])}}
        assert len(cg.pending([_row("Selhaven")], TRAITS, store=store,
                              today=TODAY)) == 1

    def test_a_firm_checked_this_month_for_another_role_waits(self):
        from jobagent.core import company_id
        store = {"x": {"checked": "2026-11-10", "traits": st.signature(TRAITS),
                       "company_key": company_id.key("Selhaven")}}
        assert cg.pending([_row("Selhaven", "Other Role")], TRAITS,
                          store=store, today=TODAY) == []

    def test_but_not_forever(self):
        from jobagent.core import company_id
        store = {"x": {"checked": "2026-08-01", "traits": st.signature(TRAITS),
                       "company_key": company_id.key("Selhaven")}}
        assert len(cg.pending([_row("Selhaven", "Other Role")], TRAITS,
                              store=store, today=TODAY)) == 1

    def test_limit(self):
        rows = [_row(f"Firm {c}") for c in "ABCDEFG"]
        assert len(cg.pending(rows, TRAITS, limit=3, store={},
                              today=TODAY)) == 3
