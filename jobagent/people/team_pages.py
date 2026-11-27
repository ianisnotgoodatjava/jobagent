"""Reads a company's published org chart, for the firms no registry covers.

FINRA registers broker-dealers and nobody else, which leaves out most of what
this search actually applies to: a bank's corporate arm, a utility, a rating
agency, a private company. Running auto_outreach over the ten most recent
applications produced zero contacts for exactly that reason -- BankUnited,
BNY, Parchem, Onyx Staffing all invisible to it.

Org-chart sites publish name and job title together, which is the piece FINRA
is missing entirely and the piece that decides whether a person is on the desk
applied to. They emit schema.org Person entries, so this reads the structured
data rather than scraping layout.

The data is crowd-sourced and can be stale. It is treated as a lead: a name
and a claimed title worth checking, not a fact. Nothing here builds an
address -- company_formats does that, and only where the convention has
evidence behind it.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
_ORG_URL = "https://theorg.com/org/{}"

_PERSON_RE = re.compile(
    r'"@type"\s*:\s*"Person"\s*,\s*"name"\s*:\s*"([^"]{3,60})"\s*,\s*'
    r'"jobTitle"\s*:\s*"([^"]{2,90})"')

# Corporate noise that is never part of a URL slug.
_DROP = {"inc", "llc", "lp", "llp", "ltd", "limited", "corp", "corporation",
         "company", "co", "group", "the", "holdings", "plc", "sa", "nv"}


def slugs(company: str) -> list[str]:
    """URL slugs worth trying, most likely first.

    "BNY - 79943 - Internal Investment..." is a real sheet value, so the
    requisition tail is cut before anything else.
    """
    name = re.split(r"\s+-\s+\d", company or "")[0]
    words = [w for w in re.findall(r"[A-Za-z0-9&]+", name.lower())
             if w not in _DROP]
    if not words:
        return []
    out = ["-".join(words)]
    if len(words) > 1:
        out.append("".join(words))
        out.append(words[0])
    # dedupe, keep order
    seen, ordered = set(), []
    for s in out:
        if len(s) >= 3 and s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered


def _fetch(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


# Legal forms that mark a company as registered outside the US. A slug is a
# name, and names repeat across countries: theorg.com/org/stonex is Stonex
# S.r.l., an Italian surveying-instrument maker, not StoneX the US broker.
_FOREIGN_FORMS = {"srl", "spa", "gmbh", "ag", "bv", "nv", "sas", "sarl", "ltda",
                  "kk", "oy", "ab", "as", "aps", "sp-z-o-o", "pty", "pvt", "bhd"}


def foreign(html: str, country: str) -> str:
    """Why this org page is not the `country` company, or "" when it may be.

    Offices on the page (schema.org addressCountry) that never include the
    country, or a LinkedIn company slug ending in a foreign legal form.
    """
    if not country:
        return ""
    countries = set(re.findall(r'"addressCountry"\s*:\s*"([^"]+)"', html or ""))
    if countries and country not in countries:
        return f"its offices are in {', '.join(sorted(countries))}"
    for slug in re.findall(r"linkedin\.com/company/([a-z0-9\-]+)", html or ""):
        form = slug.rsplit("-", 1)[-1]
        if form in _FOREIGN_FORMS:
            return f"it is registered as {slug}"
    return ""


def people(company: str, limit: int = 60, country: str = "",
           notes: list | None = None) -> list[dict]:
    """[{name, title, source}] from the first slug that resolves.

    `country` (e.g. "US"), when given, rejects a page for a same-named
    company elsewhere; the reason goes to `notes`.
    """
    for slug in slugs(company):
        try:
            html = _fetch(_ORG_URL.format(urllib.parse.quote(slug)))
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            continue
        why = foreign(html, country)
        if why:
            if notes is not None:
                notes.append(f"org chart {slug} is another company: {why}")
            continue
        found, seen = [], set()
        for name, title in _PERSON_RE.findall(html):
            name = json.loads(f'"{name}"') if "\\u" in name else name
            key = name.lower().strip()
            if key in seen or len(key.split()) < 2:
                continue
            seen.add(key)
            found.append({"name": name.strip(), "title": title.strip(),
                          "source": f"org chart ({slug})"})
            if len(found) >= limit:
                break
        if found:
            return found
    return []


def run(company: str) -> None:
    from jobagent.people import people_search

    found = people(company)
    if not found:
        print(f"No published org chart found for {company}.")
        return
    print(f"{len(found)} published name(s) for {company}:\n")
    for person in people_search.rank(found):
        print(f"  {person['band']:11s} {person['name'][:26]:26s} "
              f"{person['title'][:52]}")
