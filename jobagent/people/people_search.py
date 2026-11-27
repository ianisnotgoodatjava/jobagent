"""Finds a named person on the right desk, ranked, addressed and de-duplicated.

Sourcing was three unconnected things: people_finder queried FINRA, edgar_finder
queried SEC filings, and the method that actually produced today's usable
contacts -- reading a company's own team page -- was done by hand in a browser.
The router in `sourcing` chose between the first two by asking which *could*
apply, not which returns anyone, so it reported full coverage of 222 companies
while EDGAR yielded people for none of them.

This runs the sources, keeps what they return, and is honest about the rest.

  FINRA        registered broker-dealers. Real, current, and free, but it
               carries no job title, so it says who works there and not what
               they do.
  Firm site    the company's own about/team/leadership pages (site_people).
               Name, title and often the address, from the firm itself --
               the verification the contact rules ask for. Also the only
               source that can say which domain the firm reads mail at when
               nobody has written there yet.
  Team page    the org chart sites publish name and title together, which is
               the piece FINRA is missing. Crowd-sourced, so a title can be
               stale -- treated as a lead, never as a fact.

Ranking is by desk match first and seniority second. Seniority matters in a
specific direction: the target is a VP or Director, because a Managing Director
or Head rarely answers an unsolicited note and an Analyst cannot speak for the
desk. That preference came from being told it directly, twice.

Anyone written to in the last year is dropped before ranking, not after --
see recent_contacts. Addresses come from company_formats, which declines to
build one when the evidence conflicts, so a candidate can be real and still
arrive with no address. That is reported rather than papered over with a guess.
"""

import re
import time

from jobagent.core import affinity
from jobagent.core import bio_facts
from jobagent.core import desk_match
from jobagent.people import company_formats
from jobagent.people import recent_contacts

# Title bands, most wanted first. The regexes read the title as published.
# "president" must not swallow "Vice President". A VP is the target band;
# matching the substring put Person H (VP, FIG) and Person I (SVP,
# Institutional Sales Trading) in the same bucket as a global co-head, which
# is the exact mistake this ranking exists to prevent.
_SENIOR = re.compile(
    r"(?i)(\b(managing director|chief|head of|global head|"
    # The C-suite by acronym. This was c[ete]o, which only ever matched CEO
    # and CTO, so "SVP and CFO" at Con Edison was banded a VP target. CDO is
    # left out on purpose: on a credit desk it is the product, not a person.
    r"partner|vice chairman|founder|ceo|cfo|coo|cio|cto|cro|cmo|cco)\b"
    r"|(?<!vice )(?<!senior vice )(?<!executive vice )\bpresident\b"
    r"|\bmd\b)")
_TARGET = re.compile(
    r"(?i)\b(vice president|svp|evp|avp|vp|director|principal|senior manager|"
    r"portfolio manager|senior|lead)\b")
# Mid-level: the people a step below VP who sit on the desk and still answer
# a cold note. A plain "Associate" was read as junior and dropped from every
# result, and "Manager, Structured Finance" fell to unknown — so the search
# never asked for them. They rank after VP/Director and ahead of a title it
# cannot read. A plain Analyst stays junior: told twice that an Analyst
# cannot speak for the desk.
_MID = re.compile(r"(?i)\b(associate|manager|team lead|supervisor)\b")
_JUNIOR = re.compile(r"(?i)\b(analyst|associate|intern|assistant|coordinator)\b")
_SUPPORT = re.compile(
    r"(?i)\b(intern|assistant|(client|customer|retail|store|administrative|"
    r"sales support|service) associate)\b")

# A seat on the board is not a job at the firm, and an org chart publishes it
# as "Director" — which _TARGET reads as the target band. That is how a search
# for someone to ask about a Ratings Associate opening at Moody's came back
# with three of that firm's non-executive directors at the top, all three of
# whom sit on the board and none of whom works there; another exchange
# returned two of its own directors the same way, and a third a "Public
# Director" and a "Board Member".
#
# Governance and administrative officers are here for the same reason rather
# than a different one: "EVP, General Counsel & Corporate Secretary" is banded
# a target by the "evp" in it, and is nobody to ask what a desk does.
#
# A bare "Director" with no department counts, because a real department head
# is published as "Director of X" or "Director, X" — the unqualified form is
# what board pages carry. "Director Of Operations" and "Director of Finance"
# are unaffected.
_NOT_A_DESK = re.compile(
    r"(?i)(\b(independent|non-?exec(utive)?|outside|public|lead|presiding)"
    r"\s+director\b"
    r"|\bboard\s+(member|director|chair(man)?)\b"
    r"|\bcorporate\s+board\b|\btrustee\b"
    r"|\bgeneral counsel\b|\bcorporate secretary\b|\bchro\b"
    r"|\bchief (human resources|people|legal|compliance|diversity) officer\b"
    r"|^\s*(independent\s+)?director\s*$)")

BAND_TARGET, BAND_SENIOR, BAND_JUNIOR, BAND_UNKNOWN, BAND_NOT_A_DESK = (
    "vp/director", "too senior", "too junior", "unknown", "not a desk")
BAND_MID = "mid-level"


def band(title: str) -> str:
    """Which seniority band a published title falls in."""
    title = title or ""
    # Order matters: "Director of Public Equity Research, Portfolio Manager"
    # is a target, while "Managing Director, Head of X" is not -- so the
    # senior test runs first and wins outright.
    # A title carrying both ("Managing Director, Head of X" vs "Senior Vice
    # President of Y") is decided by the senior test, which is written so that
    # the Vice President forms never reach it.
    # First: a board seat or a governance office is not a desk at all, so it
    # is not a question of being too senior for an unsolicited note.
    if _NOT_A_DESK.search(title):
        return BAND_NOT_A_DESK
    if _SENIOR.search(title):
        return BAND_SENIOR
    if _TARGET.search(title):
        return BAND_TARGET
    # Interns and assistants are not mid-level for carrying "associate" too,
    # and nor is a service seat: a Client Associate at a private bank books
    # meetings for the advisors, it is not a seat on a desk.
    if _MID.search(title) and not _SUPPORT.search(title):
        return BAND_MID
    if _JUNIOR.search(title):
        return BAND_JUNIOR
    return BAND_UNKNOWN


_BAND_RANK = {BAND_TARGET: 0, BAND_MID: 1, BAND_UNKNOWN: 2, BAND_SENIOR: 3,
              BAND_JUNIOR: 4, BAND_NOT_A_DESK: 5}


def _desk_score(title: str, role: str) -> float:
    """How near a published title is to the job applied to.

    Counting shared words missed most real matches — "VP, Securitization"
    shares none with "CMBS Analyst" — so core/desk_match also credits the
    same desk under a different name, at half weight.
    """
    return desk_match.score(title, role)[0]


# A desk read from licences rather than a title counts for less: a Series 7
# is held across a whole floor, and even a 79 says banking, not which group.
EXAM_DESK_WEIGHT = 0.6

_ME: dict | None = None


def _me() -> dict:
    """The sender's schools, employers and languages, for affinity."""
    global _ME
    if _ME is None:
        try:
            import json
            with open("resume_bank.json", encoding="utf-8") as f:
                _ME = affinity.me_from_resume(json.load(f))
        except (OSError, ValueError):
            _ME = {}
    return _ME


# Years since entering the industry that read as associate to VP when there
# is no title. Narrower than people_finder.MID_LEVEL_YEARS, which decides who
# is worth reading at all: past twelve years someone is as likely a director.
MID_REGISTERED = (2, 12)


def _registered_years(c: dict) -> int | None:
    if c.get("years") is not None:
        return c["years"]
    start = c.get("industry_start") or c.get("started") or ""
    try:
        import datetime
        return (datetime.date.today()
                - datetime.date.fromisoformat(start[:10])).days // 365
    except ValueError:
        return None


def _exam_codes(c: dict) -> list[str]:
    if c.get("exams"):
        return list(c["exams"])
    return [bio_facts.exam_code(e.get("code", ""))
            for e in (c.get("finra") or {}).get("exams") or []]


def score(c: dict, role: str, me: dict | None = None) -> None:
    """Fill band, desk_score/desk_reasons and affinity/affinity_reasons."""
    c["band"] = band(c.get("title", ""))
    years = _registered_years(c)
    if (c["band"] == BAND_UNKNOWN and years is not None
            and MID_REGISTERED[0] <= years <= MID_REGISTERED[1]):
        # No title, but FINRA says how long they have been licensed, which
        # is the seniority signal the registry does carry.
        c["band"] = BAND_MID
    desk, reasons = desk_match.score(c.get("title", ""), role)
    if not desk and not c.get("title"):
        # No title: FINRA exams say which desk, at a discount.
        hint = bio_facts.desk_from_exams(_exam_codes(c))
        exam_desk, _ = desk_match.score(hint, role) if hint else (0.0, [])
        if exam_desk:
            desk = exam_desk * EXAM_DESK_WEIGHT
            reasons = [f"FINRA licences point to {hint.split()[0]}"]
    if not desk:
        # A past job on the same desk, from the bio or the registry.
        for job in c.get("employment") or []:
            past, _ = desk_match.score(job.get("title", ""), role)
            if past:
                desk = past * 0.5
                reasons = [f"was {job['title']} at {job['firm']}"]
                break
    c["desk_score"], c["desk_reasons"] = desk, reasons
    c["affinity"], c["affinity_reasons"] = affinity.score(
        c, _me() if me is None else me)


def rank(candidates: list[dict], role: str = "",
         drop_out_of_band: bool = False, me: dict | None = None) -> list[dict]:
    """Best person first: seniority band, then desk match, then title present.

    Band is checked before desk match, not after. Sorting on desk match first
    put "MD, Chief Investment Officer" at the top of a list for an Investment
    Consultant role -- a good topic match and precisely the seniority that
    does not answer an unsolicited note. A Managing Director on the right desk
    is still the wrong person to write to.
    """
    for c in candidates:
        score(c, role, me)
    if drop_out_of_band:
        candidates = [c for c in candidates
                      if c["band"] in (BAND_TARGET, BAND_MID, BAND_UNKNOWN)]
    # Within a band, desk fit and what they share with the sender (a school,
    # an employer: core/affinity) are added, since either makes a reply more
    # likely. Then someone more sources name, in the job's office, published
    # by the firm itself beats a name from a crowd-sourced chart.
    return sorted(
        candidates,
        key=lambda c: (_BAND_RANK.get(c["band"], 9),
                       -(c["desk_score"] + c["affinity"]),
                       -c.get("corroboration", 1), not c.get("same_office"),
                       not c.get("verified"), not c.get("title")))


def with_addresses(candidates: list[dict], domain: str,
                   published: tuple[str, str] = ("", "")) -> list[dict]:
    """Attach an address where the domain's convention is actually known.

    An address the firm printed next to the person's name is kept as it is --
    nothing built from a convention beats it. Otherwise delivered history
    decides the convention, then the registry, and last the convention the
    firm's own published addresses show (`published`, pattern and evidence).
    """
    pattern, why = company_formats.pattern_for(domain)
    if not pattern and published[0]:
        pattern, why = published[0], f"{published[1]} (inferred)"
    for c in candidates:
        if c.get("verified") and c.get("email"):
            c["address_basis"] = "published on the firm's site"
            continue
        c["email"] = (company_formats.address_for(c["name"], domain, pattern)
                      if pattern else "")
        c["address_basis"] = why
    return candidates


def find(company: str, role: str = "", domain: str = "", state: str = "NY",
         limit: int = 12, website: str = "", posting: dict | None = None) -> dict:
    """{candidates, dropped, notes, domain} for one company.

    `dropped` is the people who exist but cannot be written to -- already
    contacted, or no address. Keeping them visible is the point: an empty
    result and a result full of unreachable people are different problems.

    `domain` comes back filled in when the caller had none and the firm's
    own published addresses showed it.
    """
    notes, found = [], []
    finra_firm = ""

    # The posting, when there is one, says where the job sits. Its team name
    # and the desk's own vocabulary (ILST, LIHTC, PFE) go into desk matching,
    # and its office decides which state FINRA is asked about -- a Charlotte
    # desk is not found by searching New York branches.
    if posting:
        role = " ".join([role, *posting.get("teams", []),
                         *posting.get("desk_words", [])]).strip()
        if posting.get("states") and posting["states"][0]:
            state = posting["states"][0]
        notes.append("posting: " + "; ".join(x for x in (
            ", ".join(posting.get("teams", [])),
            ("reports to " + posting["reports_to"][0]) if posting.get("reports_to") else "",
            ", ".join(posting.get("cities", []))) if x))

    try:
        from jobagent.people import people_finder
        firms = people_finder.find_firm(company)
        for firm_id, firm_name in firms[:3]:
            registered = people_finder.people_at_firm(firm_id, state=state,
                                                      limit=120)
            if not registered:
                continue
            notes.append(f"FINRA {firm_name}: {len(registered)} in {state}")
            finra_firm = firm_name
            band_people = people_finder.mid_level(registered)
            detailed = _finra_details(company, band_people)
            if detailed:
                notes.append(f"FINRA records read for {detailed} "
                             f"(licences and past firms)")
            for person in band_people:
                found.append({"name": person["name"].title(), "title": "",
                              "source": f"FINRA ({firm_name})",
                              "years": person.get("years"),
                              "crd": person.get("crd", ""),
                              "started": person.get("started", ""),
                              "city": person.get("city", ""),
                              "finra": person.get("finra") or {}})
            break
        if not firms:
            notes.append("not a registered broker-dealer — FINRA has nobody")
    except Exception as exc:
        notes.append(f"FINRA unavailable: {type(exc).__name__}")

    # The firm's own site: verified names and titles, sometimes exact
    # addresses, and the mail domain when nothing else knows it.
    site: dict = {}
    try:
        from jobagent.people import site_people
        site = site_people.find(company, domain=domain, website=website)
        notes += [f"firm site: {n}" for n in site.get("notes", [])[-1:]]
        published = site.get("people") or []
        if published:
            on_site = {p["name"].lower(): p for p in published}
            for person in found:
                match = on_site.get(person["name"].lower())
                if match:
                    person["title"] = person["title"] or match["title"]
                    person["email"] = person.get("email") or match.get("email", "")
                    person["verified"] = True
            found += published
        if not domain and site.get("mail_domain"):
            domain = site["mail_domain"]
            notes.append(f"mail domain {domain} from the firm's published "
                         f"addresses")
    except Exception as exc:
        notes.append(f"firm site unavailable: {type(exc).__name__}")

    # Org chart, for everything FINRA cannot see -- and for the job titles it
    # never carries even when it can. Run always, not as a fallback: a FINRA
    # name with no title cannot be matched to a desk.
    try:
        from jobagent.people import team_pages
        published = team_pages.people(company, country="US", notes=notes)
        if published:
            notes.append(f"org chart: {len(published)} published name(s)")
            titled = {p["name"].lower(): p["title"] for p in published}
            for person in found:
                if not person["title"]:
                    person["title"] = titled.get(person["name"].lower(), "")
            found += published
        elif not found:
            notes.append("no published org chart either")
    except Exception as exc:
        notes.append(f"org chart unavailable: {type(exc).__name__}")

    # First mention wins, but a later source can still fill what it lacks:
    # an org-chart entry for someone the firm's site also lists keeps the
    # site's address and its verification.
    kept, unique = {}, []
    for person in found:
        key = person["name"].lower()
        if key in kept:
            first = kept[key]
            for field in ("title", "email", "verified", "url"):
                if not first.get(field) and person.get(field):
                    first[field] = person[field]
            continue
        kept[key] = person
        unique.append(person)

    if domain:
        published = ((site.get("pattern", ""), site.get("pattern_evidence", ""))
                     if site.get("mail_domain") == domain else ("", ""))
        unique = with_addresses(unique, domain, published)

    # Everyone found goes into the people database, and comes back with how
    # many independent sources have named them across every search so far.
    db = _remember(company, unique, notes)
    if db is not None:
        try:
            from jobagent.people import people_db
            pattern, basis = company_formats.pattern_for(domain) if domain else ("", "")
            if not pattern and site.get("mail_domain") == domain:
                pattern, basis = site.get("pattern", ""), site.get("pattern_evidence", "")
            people_db.record_firm(db, company, website=site.get("website", ""),
                                  domain=domain, pattern=pattern, basis=basis,
                                  finra_firm=finra_firm)
        except Exception:
            pass

    fresh, already = recent_contacts.filter_contacts(unique)
    if already:
        notes.append(f"{len(already)} already written to in the last year")

    ranked = rank(fresh, role, drop_out_of_band=True)
    if db is not None:
        try:
            from jobagent.people import people_db
            people_db.link(db, company, role, ranked)
        except Exception:
            pass
    unreachable = [c for c in ranked if not c.get("email")]
    reachable = [c for c in ranked if c.get("email")]
    return {
        "candidates": reachable[:limit],
        "dropped": already + unreachable[:limit],
        "notes": notes,
        "domain": domain,
        "target": posting or {},
    }


# BrokerCheck records read per search. Each is one request to a free public
# service; ninety days of caching in people_db means a repeat search of the
# same firm reads only the people it has not seen.
FINRA_DETAILS = 25


def _finra_details(company: str, people: list[dict], get=None) -> int:
    """Attach `finra` (people_finder.details) to the first FINRA_DETAILS
    people not read in the last ninety days. Returns how many were read."""
    from jobagent.people import people_finder
    try:
        from jobagent.people import people_db
        db = people_db.connect()
    except Exception:
        db = None
    read = 0
    for person in people:
        if read >= FINRA_DETAILS:
            break
        if not person.get("crd"):
            continue
        if db is not None and people_db.finra_checked(db, company, person["name"]):
            continue
        try:
            person["finra"] = people_finder.details(person["crd"], get=get)
        except Exception:
            continue
        read += 1
        if get is None:
            time.sleep(0.3)  # a free public service; do not hammer it
    return read


def _remember(company: str, people: list[dict], notes: list[str]):
    """Record a search in people_db; attach each person's corroboration.

    Never lets the database stop a search: a locked or missing file costs
    the tie-break, not the result.
    """
    try:
        from jobagent.people import people_db
        db = people_db.connect()
        people_db.record(db, company, people)
        known = {people_db.name_key(p["name"]): p
                 for p in people_db.at_company(db, company)}
    except Exception as exc:
        notes.append(f"people database unavailable: {type(exc).__name__}")
        return None
    for person in people:
        row = known.get(people_db.name_key(person.get("name", "")))
        if row:
            person["corroboration"] = row["corroboration"]
            person["id"] = row["id"]
            # What earlier searches learned about them: degrees, past firms,
            # licences. Ranking reads these for desk and affinity.
            for field in ("education", "employment", "exams", "credentials",
                          "languages", "years_experience", "linkedin"):
                if row.get(field) and not person.get(field):
                    person[field] = row[field]
    multi = sum(1 for p in people if p.get("corroboration", 1) > 1)
    if multi:
        notes.append(f"{multi} named by more than one source")
    return db


def run(company: str, role: str = "", domain: str = "", state: str = "NY",
        posting: dict | None = None) -> None:
    result = find(company, role, domain, state, posting=posting)
    for note in result["notes"]:
        print(f"  · {note}")
    print()
    if not result["candidates"] and not result["dropped"]:
        print(f"Nobody found at {company}. FINRA only registers broker-dealers, "
              f"and the firm's\nown site names nobody readable — see the notes "
              f"above for which pages were tried.")
        _print_linkedin(result.get("target") or {})
        return
    if result["candidates"]:
        print(f"Write to these ({len(result['candidates'])}):")
        for c in result["candidates"]:
            years = f"{c['years']}y" if c.get("years") is not None else "  "
            print(f"  {years:>4}  {c['name'][:26]:26s} {c.get('email','')[:34]:34s}"
                  f" {c.get('title','') or c['band']}")
    if result["dropped"]:
        print(f"\nFound but not writable ({len(result['dropped'])}):")
        for c in result["dropped"][:8]:
            why = (f"emailed {c['last_emailed']}" if c.get("last_emailed")
                   else c.get("address_basis", "no address"))
            print(f"        {c['name'][:26]:26s} {why[:52]}")
    if not any(c.get("verified") for c in result["candidates"]):
        _print_linkedin(result.get("target") or {})


def _print_linkedin(target: dict) -> None:
    """LinkedIn searches aimed by the posting, for when nothing public names
    anyone on the desk. Picking the person stays with you; the search is
    only aimed at the right team in the right city."""
    if not target:
        return
    from jobagent.core import posting_target
    print(f"\nNobody on that desk is published. On LinkedIn, look for a "
          f"{' / '.join(target.get('target_titles', [])[:3])}:")
    for url in posting_target.linkedin_urls(target):
        print(f"  {url}")

