"""Reads a job posting for who to write to, before anyone is searched for.

The contact search used to start from the job title alone. At a firm the size
of U.S. Bank that finds whoever speaks in public under a similar word, which is
mostly senior people on the team next door: asked for someone near a Liquidity
Risk Management Analyst opening, it picked the Head of Capital and Liquidity
off a summit speaker list, a Treasury executive several levels above the desk.

The posting already says where the job sits. "The Counterparty Risk Group
provides independent review..." names the team; "reports to the Director of
Liquidity Risk" names the level above; "Charlotte, NC ... three or more days in
the office" names the building. This pulls those out so the search can rank
on them and, where nothing public names the right person, hand back LinkedIn
searches aimed at that team in that city instead of the firm at large.

Pure text handling: no network, no sheet.
"""

import re
from urllib.parse import quote_plus

from jobagent.core import sender_profile

# Offices worth recognising, with the state FINRA's branch filter wants.
CITIES = {
    "new york": "NY", "jersey city": "NJ", "stamford": "CT", "boston": "MA",
    "charlotte": "NC", "minneapolis": "MN", "chicago": "IL", "denver": "CO",
    "st. louis": "MO", "saint louis": "MO", "houston": "TX", "dallas": "TX",
    "atlanta": "GA", "pittsburgh": "PA", "philadelphia": "PA",
    "los angeles": "CA", "san francisco": "CA", "seattle": "WA",
    "phoenix": "AZ", "scottsdale": "AZ", "milwaukee": "WI",
    "cincinnati": "OH", "cleveland": "OH", "portland": "OR",
    "salt lake city": "UT", "washington": "DC", "miami": "FL",
    "indianapolis": "IN", "omaha": "NE", "nashville": "TN",
}

# Words in a title that say level or contract, not what the desk does.
_TITLE_NOISE = {
    "analyst", "associate", "senior", "sr", "junior", "jr", "intern",
    "manager", "specialist", "i", "ii", "iii", "entry", "level", "program",
    "the", "and", "of", "for", "a", "an", "with", "in", "to", "on", "at",
    "role", "position", "hybrid", "remote", "full", "time",
}

# Acronyms that show up in every posting and say nothing about the desk.
_COMMON_ACRONYMS = {
    "USA", "US", "EEO", "EOE", "AA", "HR", "PTO", "FAQ", "LLC", "INC", "CEO",
    "CFO", "NA", "ADA", "MS", "BA", "BS", "MBA", "CFA", "GPA", "NYU", "ID",
    "OR", "AND", "TBD", "WFH", "IT", "API",
}

_TEAM_RE = re.compile(
    r"\b(?:[Tt]he|[Oo]ur)\s+((?:[A-Z][\w&'’\-]*\s+){1,5}?)"
    r"(?:([Gg]roup|[Dd]esk|[Dd]epartment|[Dd]ivision|[Pp]ractice)|[Tt]eam|[Ff]unction)\b")
_SECTION_RE = re.compile(
    r"\b(?:join|part of|within|in|on|supporting|supports)\s+(?:the\s+|our\s+)?"
    r"((?:[A-Z][\w&'’\-]*\s+){1,5}?(?:Group|Team|Desk|Treasury|Risk|Finance))\b")
_REPORTS_RE = re.compile(
    r"(?i)\breport(?:s|ing)?\s+(?:directly\s+)?(?:to|into)\s+(?:the\s+|a\s+|an\s+)?"
    r"([A-Za-z][\w,&/'’\- ]{3,70}?)(?=[.;:)\n]|,\s|\s+(?:and|who|within|in|on)\s)")
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,6}\b")


def _teams(text: str, title: str) -> list[str]:
    found = []
    for rx in (_TEAM_RE, _SECTION_RE):
        for m in rx.finditer(text or ""):
            # "Counterparty Risk Group" keeps its noun; "the Impact Finance
            # team" drops it, since "team" is not part of the group's name.
            team = (m.group(1).strip() + (" " + m.group(2).title()
                    if rx is _TEAM_RE and m.lastindex and m.lastindex >= 2
                    and m.group(2) else "")).strip()
            if len(team.split()) >= 1 and team.lower() not in (t.lower() for t in found):
                found.append(team)
    # "Impact Finance - Affordable Housing Real Estate Financial Analyst":
    # the part before the dash is the business line.
    head = re.split(r"\s+[-–—]\s+", title or "", maxsplit=1)
    if len(head) == 2 and len(head[0].split()) <= 4:
        if head[0].lower() not in (t.lower() for t in found):
            found.insert(0, head[0].strip())
    return found[:4]


def _cities(text: str) -> list[str]:
    low = (text or "").lower()
    hits = sorted(((low.find(c), c) for c in CITIES if c in low))
    out = []
    for _, city in hits:
        name = "St. Louis" if city in ("st. louis", "saint louis") else city.title()
        if name not in out:
            out.append(name)
    return out[:4]


def _desk_words(title: str, teams: list[str], text: str) -> list[str]:
    words = []
    for source in [title, *teams]:
        for w in re.findall(r"[A-Za-z][A-Za-z&\-]+", source or ""):
            lw = w.lower()
            if lw not in _TITLE_NOISE and len(lw) > 2 and lw not in words:
                words.append(lw)
    counts: dict = {}
    for a in _ACRONYM_RE.findall(text or ""):
        if a not in _COMMON_ACRONYMS:
            counts[a] = counts.get(a, 0) + 1
    # An acronym the posting repeats is the desk's own vocabulary: ILST,
    # LIHTC, PFE, CVA. Said once, it is usually boilerplate.
    words += [a for a, n in sorted(counts.items(), key=lambda kv: -kv[1])
              if n >= 2][:5]
    return words[:12]


def target_titles(title: str) -> list[str]:
    """The level that can speak for the desk and still answers a cold note."""
    t = (title or "").lower()
    if re.search(r"\b(vice president|vp|director)\b", t):
        return ["Director", "Managing Director", "Executive Director"]
    if re.search(r"\b(manager|senior|sr\.?)\b", t):
        return ["Vice President", "Director", "Senior Manager"]
    # Mid-level first-hand knowledge of the desk: an Associate or AVP sat in
    # this seat two or three years ago and still answers a cold note.
    return ["Vice President", "Assistant Vice President", "Associate",
            "Senior Analyst", "Manager"]


def read(text: str, title: str = "", company: str = "") -> dict:
    """{teams, reports_to, cities, states, desk_words, target_titles, queries}."""
    body = text or ""
    teams = _teams(body, title)
    reports = [m.group(1).strip(" ,") for m in _REPORTS_RE.finditer(body)]
    cities = _cities(f"{title}\n{body}")
    words = _desk_words(title, teams, body)
    focus = teams[0] if teams else " ".join(words[:3])
    queries = []
    for city in (cities[:2] or [""]):
        q = " ".join(x for x in (company, focus, city) if x)
        queries.append(q)
    if company:
        # One search for the mid-level on the desk: the firm's own site
        # publishes its leadership and almost never its associates.
        queries.append(f'{company} {focus} (Associate OR AVP OR Manager)'
                       .replace("  ", " ").strip())
        # The sender's own school, for the shared-alumnus search. It comes
        # from the gitignored profile: it is their identity, not a constant.
        school = sender_profile.get("school_short")
        if school and not school.isupper():
            queries.append(f"{company} {focus} {school}".strip())
    return {
        "teams": teams,
        "reports_to": reports[:2],
        "cities": cities,
        "states": [CITIES.get(c.lower(), CITIES["st. louis"] if c == "St. Louis" else "")
                   for c in cities],
        "desk_words": words,
        "target_titles": target_titles(title),
        "queries": queries,
    }


def linkedin_urls(target: dict) -> list[str]:
    return [f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(q)}"
            for q in target.get("queries", [])]
