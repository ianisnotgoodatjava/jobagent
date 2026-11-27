"""What you have in common with a stranger, and whether they really said it.

A cold email lands differently when it opens on something shared: the same
school, a language spoken at home, the sport both of you played, a club, a
city you grew up in. This module holds the traits you are looking for and
decides whether a person's published words actually state one of them.

A trait is {kind, label, terms}:

  kind    school, language, heritage, group, sport, hobby, employer, place
  label   how it reads in a report ("rowing", "Example University")
  terms   the phrases that state it in a bio; the label when omitted

They come from three places, merged: your resume (schools, languages,
employers), a `common_ground` list in profile.json -- gitignored, because
what you share with people says who you are -- and `--trait kind:term` on
the command line for a one-off search.

The rule that matters is that a connection is something a person published
about *themselves*, with a page that says so. Writing "as a fellow X" to
someone who is not is worse than writing nothing, so `check()` refuses:

  - a guess from a name or a photo, about anyone's background, nationality,
    ethnicity or religion -- names are shared across countries, and inferring
    who someone is from one is the profiling nobody wants done to them;
  - a place or a background that is a market they cover, not where they are
    from ("covers Brazilian equities" is not "grew up in Brazil");
  - evidence that names none of your traits, or comes with no source.

Each refusal carries its reason. Pure logic: no network, no files.
"""

import re
from urllib.parse import quote_plus

from jobagent.core import linkedin_helper

HERITAGE, GROUP, SCHOOL, LANGUAGE, SPORT, HOBBY, EMPLOYER, PLACE = (
    "heritage", "group", "school", "language", "sport", "hobby", "employer",
    "place")

# Strongest first, used only to order people equally near the role. Saying
# who you are, or joining a club for it, is chosen; a city can be where
# someone was posted for a year.
KINDS = (HERITAGE, GROUP, SCHOOL, LANGUAGE, SPORT, HOBBY, EMPLOYER, PLACE)

# Kinds that also name a market someone can cover professionally.
_MARKET_KINDS = {HERITAGE, PLACE}


def make(kind: str, label: str, terms=None, search=None) -> dict:
    """One trait, normalised. Unknown kinds are kept as "group"."""
    kind = (kind or "").strip().lower()
    kind = kind if kind in KINDS else GROUP
    label = (label or "").strip()
    terms = [t.strip() for t in (terms or [label]) if t and t.strip()]
    return {"kind": kind, "label": label or (terms[0] if terms else ""),
            "terms": terms,
            "search": [s.strip() for s in (search or terms[:2]) if s.strip()]}


def parse_flag(value: str) -> dict:
    """"sport:rowing" or "language:Portuguese|Brazilian Portuguese"."""
    kind, _, rest = (value or "").partition(":")
    if not rest:
        kind, rest = GROUP, kind
    terms = [t for t in rest.split("|") if t.strip()]
    return make(kind, terms[0] if terms else "", terms)


def from_profile(profile: dict, resume_me: dict | None = None,
                 extra: list[str] | None = None, only_extra: bool = False
                 ) -> list[dict]:
    """Every trait to look for, de-duplicated by (kind, label).

    `resume_me` is affinity.me_from_resume's {schools, employers, languages}.
    `extra` is the --trait values. With `only_extra`, the profile and resume
    are ignored, for a search on one thing.
    """
    traits = [parse_flag(v) for v in extra or []]
    if not only_extra:
        for t in (profile or {}).get("common_ground") or []:
            if isinstance(t, dict):
                traits.append(make(t.get("kind"), t.get("label"),
                                   t.get("terms"), t.get("search")))
        me = resume_me or {}
        traits += [make(SCHOOL, s) for s in me.get("schools") or []]
        traits += [make(LANGUAGE, l) for l in me.get("languages") or []]
        traits += [make(EMPLOYER, e) for e in me.get("employers") or []]
    out, seen = [], set()
    for t in traits:
        key = (t["kind"], t["label"].lower())
        if t["label"] and t["terms"] and key not in seen:
            seen.add(key)
            out.append(t)
    return out


def signature(traits: list[dict]) -> str:
    """A stable name for a trait set, so a search under other traits is redone."""
    return ";".join(sorted(f"{t['kind']}:{t['label'].lower()}" for t in traits))


def _terms_re(terms: list[str]) -> re.Pattern:
    alts = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(r"(?<!\w)(?:" + alts + r")(?!\w)", re.I)


_COVERAGE = (r"\b(cover(s|ed|ing|age)?( of)?|exposure to|invest(s|ed|ing)? in|"
             r"focus(es|ed|ing)? on|research(es|ed|ing)? on|specializ\w+ in|"
             r"analyst (on|for))\b[^.;\n]{0,40}?")

# Evidence that is a guess about a person rather than their own words.
_NAME_GUESS_RE = re.compile(
    r"\b(surname|last name|family name|first name|given name|"
    r"name (is|sounds|suggests|indicates|implies)|sounds like a|"
    r"photo|appearance|looks|ethnic\w*)\b", re.I)
_HEDGE = (r"\b(likely|probably|possibly|presumably|appears to be|seems to be|"
          r"may be|might be|could be|assumed?)\b[^.\n]{0,30}")


def _strip_coverage(evidence: str, traits: list[dict]) -> str:
    text = evidence
    for t in traits:
        if t["kind"] in _MARKET_KINDS:
            alts = "|".join(re.escape(x) for x in t["terms"])
            text = re.sub(_COVERAGE + rf"(?:{alts})\w*", " ", text, flags=re.I)
    return text


def match(evidence: str, traits: list[dict]) -> dict | None:
    """The strongest of your traits the evidence states, or None."""
    text = _strip_coverage(evidence or "", traits)
    hits = [t for t in traits if _terms_re(t["terms"]).search(text)]
    if not hits:
        return None
    return min(hits, key=lambda t: KINDS.index(t["kind"]))


def check(person: dict, traits: list[dict]) -> str:
    """Why this person cannot be reported, or "" if they can."""
    name = (person.get("name") or "").strip()
    if not name or len(name.split()) < 2:
        return "no full name"
    evidence = (person.get("evidence") or "").strip()
    if not evidence:
        return "no stated connection"
    if _NAME_GUESS_RE.search(evidence):
        return "connection inferred, not stated"
    for t in traits:
        alts = "|".join(re.escape(x) for x in t["terms"])
        if re.search(_HEDGE + rf"(?:{alts})", evidence, re.I):
            return "connection inferred, not stated"
    if not traits:
        return "no traits to look for"
    if not match(evidence, traits):
        if any(_terms_re(t["terms"]).search(evidence) for t in traits):
            return "a market they cover, not their background"
        return "evidence names none of the traits searched for"
    if not (person.get("source") or "").strip().lower().startswith("http"):
        return "no source URL for the connection"
    return ""


# --- searching by hand -------------------------------------------------------

_ROLE_STOP = {
    "analyst", "associate", "senior", "junior", "the", "and", "for", "with",
    "vice", "president", "director", "manager", "global", "group", "lead",
    "head", "officer", "chief", "specialist", "consultant", "professional",
    "executive", "business", "corporate", "general", "regional", "americas",
    "intern", "internship", "summer", "full", "time", "level", "entry",
    "york", "remote", "hybrid", "program", "role", "team",
}


def role_terms(role: str, limit: int = 3) -> list[str]:
    """The words in a job title that say which desk it is."""
    words = []
    for word in re.findall(r"[a-z]+", (role or "").lower()):
        if len(word) > 3 and word not in _ROLE_STOP and word not in words:
            words.append(word)
    return words[:limit]


def search_terms(traits: list[dict], max_words: int = 18) -> list[str]:
    """Quoted phrases for a hand-run search, strongest kinds first.

    Bounded by word count: Google stops reading a query at 32 words, and the
    company and desk need room too. Each OR between phrases is a word.
    """
    out, words = [], 0
    for t in sorted(traits, key=lambda t: KINDS.index(t["kind"])):
        for phrase in t["search"]:
            q = f'"{phrase}"'
            if q in out:
                continue
            n = len(phrase.split()) + (1 if out else 0)
            if words + n > max_words:
                return out
            out.append(q)
            words += n
    return out


def linkedin_urls(company: str, role: str, traits: list[dict]
                  ) -> list[tuple[str, str]]:
    """[(label, url)] LinkedIn people searches to open by hand, never fetched."""
    shared = " OR ".join(search_terms(traits))
    if not shared:
        return []
    terms = role_terms(role)
    out = []
    if terms:
        out.append(("near the role", linkedin_helper.people_search_url(
            f'"{company}"', f"AND ({shared}) AND ({' OR '.join(terms)})")))
    out.append(("anywhere at the firm", linkedin_helper.people_search_url(
        f'"{company}"', f"AND ({shared})")))
    return out


def xray_query(company: str, role: str, traits: list[dict]) -> str:
    """A web-search query for public profiles stating one of your traits."""
    terms = role_terms(role)
    desk = f" ({' OR '.join(terms)})" if terms else ""
    # 32 words in all: "site:", the company, and the desk words with their ORs.
    budget = 31 - len(company.split()) - (2 * len(terms) - 1 if terms else 0)
    shared = " OR ".join(search_terms(traits, max_words=max(budget, 3)))
    return f'site:linkedin.com/in "{company}" ({shared}){desk}'


def google_url(query: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(query)}"


# --- reading the research back ---------------------------------------------

_BLOCK_RE = re.compile(r"^\s*===\s*PERSON\s*===\s*$", re.M)
_LINE_RE = re.compile(r"^\s*([A-Z_]+)\s*:\s*(.*?)\s*$")

_FIELDS = {
    "NAME": "name", "TITLE": "title", "TEAM": "team", "LOCATION": "location",
    "PROFILE_URL": "profile_url", "EVIDENCE": "evidence", "SOURCE": "source",
    "TRAIT": "trait_claimed", "RELEVANCE": "relevance",
    "WHY_RELEVANT": "why_relevant",
}

SAME_TEAM, SAME_FUNCTION, ELSEWHERE = (
    "same team", "same function", "elsewhere at the firm")
RELEVANCE_ORDER = (SAME_TEAM, SAME_FUNCTION, ELSEWHERE)


def _relevance(text: str) -> str:
    text = (text or "").lower()
    if "elsewhere" in text:
        return ELSEWHERE
    if "team" in text or "desk" in text:
        return SAME_TEAM
    if "function" in text or "adjacent" in text:
        return SAME_FUNCTION
    return ELSEWHERE


def parse(text: str) -> list[dict]:
    """People blocks out of the delimited response.

    Delimited text rather than a schema: this runs unattended, and a format
    that degrades to readable text beats one that fails to parse silently.
    """
    people = []
    for block in _BLOCK_RE.split(text or "")[1:]:
        person = {}
        for line in block.splitlines():
            m = _LINE_RE.match(line)
            if m and m.group(1) in _FIELDS:
                value = m.group(2)
                person[_FIELDS[m.group(1)]] = (
                    "" if value.upper() in ("UNKNOWN", "N/A", "NONE") else value)
        if person:
            person["relevance"] = _relevance(person.get("relevance", ""))
            people.append(person)
    return people


def sort_key(person: dict, desk_score: int = 0) -> tuple:
    """Nearest the role first, then the strongest kind of connection.

    Seniority is deliberately not a filter: a peer who shares something with
    you is the likeliest reply of all.
    """
    rel = RELEVANCE_ORDER.index(person.get("relevance", ELSEWHERE))
    k = person.get("kind")
    strength = KINDS.index(k) if k in KINDS else len(KINDS)
    return (rel, -desk_score, strength)
