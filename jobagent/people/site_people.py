"""Reads the people a company publishes on its own website.

The contact rule is that a person must be verified against the firm's own
page, its press, or FINRA — and of the three, only the firm's own page says
name, title and, often, address in one place. Every other source here gets
part of that: FINRA carries no title, an org-chart site is crowd-sourced and
can be a year stale, and a convention looked up on a directory is a guess.
Until this module that page was read by hand in a browser, which is why most
applications ended the night with no contact at all.

What a firm's site gives, and what each piece is worth:

  name + title   the person and the desk, from the firm itself. Verified in
                 the sense the rule means.
  mailto         an exact published address. Nothing to infer.
  hidden mail    Cloudflare rewrites addresses into a hex blob so scrapers
                 skip them. It is a fixed XOR, so it decodes; Valford's about
                 page hid contact@valfordfg.com that way — and that is how the
                 search learned Valford reads mail at valfordfg.com, not at
                 griupeak.com where its website lives.
  name+address   one published person with an address shows the convention
                 for everyone else on the page ("first.last", "flast"...),
                 which is better evidence than any directory's adoption rate.

Reading is deliberately small: a handful of likely pages (about, team,
leadership, people), the links the homepage itself labels that way, at most
MAX_PAGES fetches per company, cached for CACHE_DAYS. A page that names
nobody costs one request.

The website has to be found before it can be read. A hint (a domain already
known, a URL from the confirmation email) is tried first, then the obvious
spellings of the company name. A guessed site only counts if its own title
names the company — valford.com is a music service, and its title says so.
"""

import datetime
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from jobagent.people import company_formats
from jobagent.people import site_discovery
from jobagent.core import storage

CACHE = storage.location("site_people")
CACHE_DAYS = 14
MAX_PAGES = 12
# Bio pages the sitemap names, read on top of MAX_PAGES. One person each, so
# the budget is people rather than pages.
MAX_BIOS = 30

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# Paths that hold people on most small-firm and mid-size sites. The homepage's
# own nav links are read too; these catch the sites whose nav is a JS menu.
_PATHS = ("/about", "/about-us", "/team", "/our-team", "/people",
          "/our-people", "/leadership", "/about/team", "/about/leadership",
          "/about-us/team", "/who-we-are", "/professionals", "/our-firm",
          "/management", "/company")

_PEOPLE_LINK = re.compile(
    r"(?i)(team|people|leadership|about|professionals|management|our-firm|"
    r"who-we-are|bios?\b|staff|partners|principals)")

# Corporate words that are never the distinctive part of a name.
_NOISE = {"the", "inc", "llc", "lp", "llp", "ltd", "limited", "corp",
          "corporation", "company", "co", "group", "holdings", "plc", "sa",
          "nv", "and", "of", "&"}

_TITLE_WORDS = re.compile(
    r"(?i)\b(chief|officer|president|vice president|vp|svp|evp|avp|director|"
    r"managing|partner|principal|associate|analyst|manager|head|founder|"
    r"co-founder|chair(man|woman|person)?|counsel|trader|trading|strategist|"
    r"economist|portfolio|advisor|adviser|specialist|controller|treasurer|"
    r"secretary|engineer|consultant|ceo|cfo|coo|cio|cto|cco|cro)\b")

# Words that look like names when capitalised in a nav bar or a heading.
_NOT_NAME = {
    "our", "team", "about", "contact", "learn", "more", "read", "bio", "view",
    "profile", "leadership", "management", "board", "directors", "investment",
    "investments", "capital", "group", "partners", "services", "news",
    "insights", "careers", "home", "privacy", "policy", "terms", "us", "the",
    "and", "of", "for", "meet", "people", "firm", "company", "who", "we",
    "are", "client", "clients", "login", "menu", "search", "back", "next",
    "previous", "all", "close", "open", "office", "offices", "new", "york",
    "san", "francisco", "solutions", "strategy", "research", "markets",
    "fund", "funds", "asset", "assets", "wealth", "financial", "advisors",
    "global", "media", "press", "events", "resources", "subscribe",
    # Desk and department names, which team pages set as headings in the
    # same type as a person's name: Quenmore's "Equity Syndicate" over
    # "Institutional Sales & Trading" came back as a person.
    "equity", "equities", "syndicate", "institutional", "sales", "banking",
    "fixed", "income", "credit", "rates", "compliance", "legal", "finance",
    "accounting", "technology", "marketing", "private", "public",
    "corporate", "advisory", "retail", "product", "products", "analytics",
    "data", "energy", "power", "utilities", "real", "estate", "healthcare",
    "operations", "capital", "markets", "department", "desk", "division",
}

_TOKEN = r"(?:[A-Z][a-zA-Z'’\-]+|[A-Z]\.)"
_PARTICLE = r"(?:de|da|van|von|der|del|la|le|di|du|st\.)"
_NAME_RE = re.compile(
    rf"^{_TOKEN}(?:\s+(?:{_TOKEN}|{_PARTICLE})){{0,2}}\s+[A-Z][a-zA-Z'’\-]+$")
# "Jane Doe, CFA" — credentials are not part of the name.
_CREDENTIALS = re.compile(
    r"(?:,\s*|\s+)(CFA|CPA|CAIA|FRM|CFP|PhD|Ph\.D\.|MBA|JD|J\.D\.|Esq\.?|"
    r"Jr\.?|Sr\.?|II|III|IV)\b[\s,®A-Za-z.]*$")

# Addresses that belong to a function, not a person. They still show which
# domain the firm reads mail at; they say nothing about a naming convention.
_ROLE_LOCALS = {
    "info", "contact", "contactus", "media", "press", "pr", "careers", "jobs",
    "hr", "recruiting", "talent", "admin", "office", "hello", "team",
    "investors", "ir", "investorrelations", "compliance", "support",
    "marketing", "sales", "general", "inquiries", "enquiries", "privacy",
    "legal", "webmaster", "help", "service", "operations", "ops", "trading",
    "research", "news", "events", "noreply", "no-reply",
}

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_CF_ATTR = re.compile(r'data-cfemail="([0-9a-fA-F]{6,})"')
_CF_HREF = re.compile(r"/cdn-cgi/l/email-protection#([0-9a-fA-F]{6,})")
_FILE_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "pdf"}


# ---------------------------------------------------------------------------
# Small pure pieces — everything below `find` is testable without a network.

def distinctive_words(company: str) -> list[str]:
    """"Valford Financial Group" -> ["tidal", "financial"]."""
    name = re.split(r"\s+-\s+\d|\(", company or "")[0]
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower())
             if w not in _NOISE]
    return words


def site_guesses(company: str) -> list[str]:
    """Homepages worth trying for a company with no known website."""
    words = distinctive_words(company)
    if not words:
        return []
    # The full name first, "group" and all: Valford's site is
    # griupeak.com, and valfordfinancial.com is somebody else's.
    full = _full_words(company)
    stems = ["".join(full)] if full != words else []
    stems.append("".join(words))
    if len(words) > 2:
        stems.append("".join(words[:2]))
    if len(words) > 1 and len(words[0]) >= 4:
        stems.append(words[0])
    if len(words) == 1:
        stems += [words[0] + "group", words[0] + "capital"]
    out = []
    for stem in stems:
        for host in (f"https://www.{stem}.com", f"https://{stem}.com"):
            if host not in out:
                out.append(host)
    return out


def _full_words(company: str) -> list[str]:
    """The name minus legal suffixes only — "group" and "capital" stay."""
    legal = {"the", "inc", "llc", "lp", "llp", "ltd", "limited", "corp",
             "corporation", "plc", "sa", "nv", "and", "of", "&"}
    name = re.split(r"\s+-\s+\d|\(", company or "")[0]
    return [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in legal]


def names_company(html: str, company: str, strict: bool = False) -> bool:
    """Whether a page's own title or site name names this company.

    Up to two distinctive words must all appear. One word is not enough:
    valford.com is a music service and its title says "VALFORD" too. `strict`,
    for a site the search guessed, wants the full name with "group" and
    "capital" in it — merworth.com is a German jobs board whose title says
    Merworth, and it came back for Merworth Group when the real site
    answered with a bot check and no title at all.
    """
    words = (_full_words(company)[:3] if strict
             else distinctive_words(company)[:2])
    if not words:
        return False
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    site_name = re.search(
        r"(?is)property=[\"']og:site_name[\"'][^>]*content=[\"']([^\"']+)",
        html or "")
    head = " ".join(m.group(1) for m in (title, site_name) if m)
    head = re.sub(r"[^a-z0-9]+", " ", head.lower())
    squashed = head.replace(" ", "")
    return all(re.search(rf"\b{re.escape(w)}", head) or w in squashed
               for w in words)


def decode_cloudflare(hexstr: str) -> str:
    """Cloudflare's email obfuscation: first byte is the key, XOR the rest."""
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key)
                       for i in range(2, len(hexstr) - 1, 2))
    except ValueError:
        return ""


def emails_in(html: str) -> list[str]:
    """Every address a page publishes: mailto, plain text, Cloudflare-hidden."""
    found = []
    for hexstr in _CF_ATTR.findall(html) + _CF_HREF.findall(html):
        decoded = decode_cloudflare(hexstr)
        if _EMAIL_RE.fullmatch(decoded or ""):
            found.append(decoded)
    for raw in re.findall(r"mailto:([^\"'?>\s]+)", html, flags=re.I):
        found.append(urllib.parse.unquote(raw))
    found += _EMAIL_RE.findall(re.sub(r"<[^>]+>", " ", html))
    out, seen = [], set()
    for address in found:
        address = address.strip().strip(".").lower()
        tld = address.rsplit(".", 1)[-1]
        if (not _EMAIL_RE.fullmatch(address) or tld in _FILE_TLDS
                or address in seen):
            continue
        seen.add(address)
        out.append(address)
    return out


def _clean_name(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip(" ,|-–—:")
    text = _CREDENTIALS.sub("", text).strip(" ,")
    # Some sites set names in capitals with CSS off; "DEV OKAFOR".
    return text.title() if text.isupper() else text


# "Nora J. Vance, Jr., Chairman" splits at the first comma, which leaves
# the suffix at the front of the title.
_LEADING_CREDENTIALS = re.compile(
    r"^(?:(?:CFA|CPA|CAIA|FRM|CFP|PhD|MBA|JD|Esq\.?|Jr\.?|Sr\.?|II|III|IV)"
    r"(?:[,\s]+|\s*[-–—]\s*))+")


def _clean_title(text: str) -> str:
    text = _LEADING_CREDENTIALS.sub("", re.sub(r"\s+", " ", text or ""))
    return text.strip(" ,-–—")


def is_name(text: str) -> bool:
    text = _clean_name(text)
    if not text or len(text) > 40 or not _NAME_RE.match(text):
        return False
    if _TITLE_WORDS.search(text):
        return False
    words = [w.lower().strip(".") for w in text.split()]
    return not any(w in _NOT_NAME for w in words)


def is_title(text: str) -> bool:
    text = (text or "").strip()
    return (2 <= len(text) <= 90 and len(text.split()) <= 12
            and bool(_TITLE_WORDS.search(text)) and not _EMAIL_RE.search(text))


def _lines(html: str) -> list[str]:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = soup.get_text("\n")
    except Exception:
        text = re.sub(r"<[^>]+>", "\n", html)
    return [re.sub(r"\s+", " ", line).strip()
            for line in text.split("\n") if line.strip()]


def _json_ld_people(html: str) -> list[dict]:
    out = []
    for block in re.findall(
            r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack += item
            elif isinstance(item, dict):
                kind = item.get("@type")
                kinds = kind if isinstance(kind, list) else [kind]
                if "Person" in kinds and item.get("name"):
                    out.append({"name": _clean_name(str(item["name"])),
                                "title": str(item.get("jobTitle") or "").strip(),
                                "email": str(item.get("email") or "")
                                .replace("mailto:", "").lower()})
                stack += [v for v in item.values() if isinstance(v, (dict, list))]
    return out


def people_in(html: str) -> list[dict]:
    """[{name, title, email}] a page publishes, structured data first.

    Three shapes cover nearly every team page: schema.org Person entries; a
    card with the name on one line and the title on the next; and a single
    line "Name, Title" or "Name – Title".

    The two text shapes only count when a page shows at least two people
    that way. A team page lists a team; one lone match is far more often a
    heading over a line of copy — Fenridge's "Supported Autonomy" above
    "Independent advisor" — than a firm of one.
    """
    structured = _json_ld_people(html)
    lines = _lines(html)
    text_shaped = []
    for i, line in enumerate(lines):
        parts = re.split(r"\s*(?:,|\||–|—|\s-\s)\s*", line, maxsplit=1)
        if len(parts) == 2 and is_name(parts[0]) and is_title(parts[1]):
            text_shaped.append({"name": _clean_name(parts[0]),
                                "title": _clean_title(parts[1]), "email": ""})
            continue
        if is_name(line) and i + 1 < len(lines):
            nxt = lines[i + 1]
            if is_title(nxt) and not is_name(nxt):
                text_shaped.append({"name": _clean_name(line),
                                    "title": _clean_title(nxt), "email": ""})
    distinct = {p["name"].lower() for p in text_shaped}
    found = structured + (text_shaped if len(distinct) >= 2 else [])
    out, seen = [], {}
    for person in found:
        key = person["name"].lower()
        if not key or len(key.split()) < 2:
            continue
        if key in seen:
            kept = seen[key]
            kept["title"] = kept["title"] or person["title"]
            kept["email"] = kept["email"] or person["email"]
            continue
        seen[key] = person
        out.append(person)
    return out


_CITY_LINE = re.compile(r"^[A-Z][A-Za-z .'-]{2,30},\s*([A-Z]{2}|[A-Z][a-z]+)$")


def bio_person(html: str, slug_name: str) -> dict | None:
    """The one person a bio page is about, or None when it is not a bio.

    The URL's slug proposes the name; the page has to state it — as a
    structured Person, or as a line of its own — or it is a page about
    something else that happens to sit under /team/. Every line naming them
    is tried, because the first is usually the page's <title>
    ("Nora Vance - Stoford.com") and the card with the job title comes
    later. The title is the first title-shaped line within a few lines after
    the name, and an office city directly after the title is kept.

    A name with neither a title nor an address of their own is refused:
    that is the shape of WBG's /eligible-contract-participant and
    Calmere's /issuer-proxy, which state their slug as a heading and are
    not people.
    """
    want = slug_name.lower().split()
    if len(want) < 2:
        return None

    def same(name: str) -> bool:
        got = re.sub(r"[^a-z ]", "", _clean_name(name).lower()).split()
        return len(got) >= 2 and got[0] == want[0] and got[-1] == want[-1]

    for person in _json_ld_people(html):
        if same(person["name"]):
            return person
    lines = _lines(html)
    name, best = "", None
    for i, line in enumerate(lines):
        parts = re.split(r"\s*(?:,|\||–|—|\s-\s)\s*", line, maxsplit=1)
        if not (same(parts[0]) and is_name(parts[0])):
            continue
        name = name or _clean_name(parts[0])
        title, at = "", i
        if len(parts) == 2 and is_title(parts[1]):
            title = parts[1]
        else:
            for j in range(i + 1, min(i + 5, len(lines))):
                if is_title(lines[j]) and not is_name(lines[j]):
                    title, at = lines[j], j
                    break
        if title:
            location = next((l for l in lines[at + 1:at + 4]
                             if _CITY_LINE.match(l)), "")
            best = {"name": _clean_name(parts[0]),
                    "title": _clean_title(re.sub(r"\s*\|.*$", "", title)),
                    "email": "", "location": location}
            break
    if best:
        return best
    if not name:
        return None
    last = re.sub(r"[^a-z]", "", want[-1])
    own_address = any(last in e.split("@")[0] for e in emails_in(html))
    return {"name": name, "title": "", "email": ""} if own_address else None


# Lines on a bio page that are site furniture, not the person's story.
_FURNITURE = re.compile(
    r"(?i)cookie|privacy|javascript|browser|subscribe|newsletter|"
    r"all rights reserved|terms of use|sign up|copyright")


# Headings bio pages put over a list of short lines. What is under them is
# kept as "Heading: line", so bio_facts can read "MBA, Strategy &
# Innovation, Boston University" as a degree without needing a sentence.
_SECTION = re.compile(
    r"(?i)^(education|certifications?|credentials|professional credentials|"
    r"licenses|languages?|affiliations|bar admissions?|admissions|"
    r"professional experience|previous experience|prior experience|"
    r"experience)\s*:?$")


_BLOCK_TAGS = ["p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6",
               "tr", "td", "section", "article", "ul", "ol", "dd", "dt",
               "header", "footer", "blockquote"]


def _blocks(html: str) -> list[str]:
    """The page's text, one line per block element.

    _lines breaks at every tag and at every newline in the source, so a
    paragraph wrapped in the HTML, or with a link in it, came back as
    fragments too short to read as sentences.
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(re.sub(r"\s+", " ", html or ""), "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "nav"]):
            tag.decompose()
        for tag in soup.find_all(_BLOCK_TAGS):
            tag.insert_before("\n")
            tag.insert_after("\n")
        text = soup.get_text("")
    except Exception:
        return _lines(html)
    return [re.sub(r"\s+", " ", line).strip()
            for line in text.split("\n") if line.strip()]


def prose(html: str, limit: int = 8000) -> str:
    """The text of a bio page worth keeping: its paragraphs, then its lists.

    Paragraphs are lines long enough to be sentences. Lists are the short
    lines under a heading like "Education" or "Certifications", which is
    where most firms put degrees. Kept so the facts can be read now and read
    again when the reader improves, without fetching the page twice.
    """
    lines = _blocks(html)
    paragraphs = [l for l in lines
                  if len(l.split()) >= 12 and not _FURNITURE.search(l)]
    listed = []
    for i, line in enumerate(lines):
        head = _SECTION.match(line)
        if not head:
            continue
        label = head.group(1).capitalize()
        for item in lines[i + 1:i + 9]:
            if _SECTION.match(item) or len(item.split()) > 15:
                break
            listed.append(f"{label}: {item}")
    text = "\n".join(dict.fromkeys(paragraphs))
    sections = "\n".join(dict.fromkeys(listed))
    # Lists last but never cut: they are short and hold the degrees.
    return (text[: max(0, limit - len(sections) - 1)] + "\n" + sections).strip()


def linkedin_link(html: str, name: str) -> str:
    """A LinkedIn profile the firm's own page links for this person.

    Read from the firm's HTML only; LinkedIn itself is never fetched. Kept
    only when the profile slug carries the person's surname, so a footer
    link to the firm's company page is not taken for theirs.
    """
    last = re.sub(r"[^a-z]", "", (name or "").lower().split()[-1]) if name else ""
    for url in re.findall(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_%\-]+",
                          html or ""):
        if last and last in url.lower():
            return url
    return ""


def spread(items: list, budget: int) -> list:
    """All of `items`, reordered so the first `budget` are spread evenly.

    Sitemaps list a firm's people in the order the site does, which is
    usually leadership first: Stoford's first 30 bios were all Senior Managing
    Directors, so a budget of 30 never reached anyone mid-level. Reading
    every k-th first covers the whole firm; the rest follow, in case some
    of the first pass fail to read.
    """
    if len(items) <= budget or budget <= 0:
        return list(items)
    step = -(-len(items) // budget)
    return [x for offset in range(step) for x in items[offset::step]]


def _person_key(name: str) -> str:
    """First and last name only: "Mary K. Major" is "Mary Major"."""
    words = re.sub(r"[^a-z ]", " ", (name or "").lower()).split()
    words = [w for w in words if len(w) > 1]
    return f"{words[0]} {words[-1]}" if len(words) >= 2 else " ".join(words)


def works_elsewhere(title: str, company_words: list[str]) -> bool:
    """A quoted partner or client, not staff: "Head of ETPs at the NYSE".

    Homepages quote outsiders, and the name-then-title shape reads the same
    as a team card. A title that places the person "at" an organisation that
    is not this one is somebody else's employee.
    """
    # Someone who used to be somewhere is a reference, not a colleague.
    if re.match(r"(?i)\s*(former|retired|ex-)", title or ""):
        return True
    match = re.search(r"(?i)\s(?:at|@)\s+(.+)$", title or "")
    if not match:
        return False
    where = match.group(1).lower()
    return not any(w in where for w in company_words[:2])


def addresses_near_names(html: str, names: list[str]) -> dict:
    """{address: name} where the address sits inside that person's own card.

    Walks up from each mailto (or Cloudflare-hidden address) until the
    enclosing element names exactly one person. Two names means the element is
    the whole team grid, not a card, and the address belongs to neither.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {}
    soup = BeautifulSoup(html or "", "html.parser")
    out: dict = {}
    anchors = soup.select('a[href^="mailto:"], a[href*="email-protection#"], '
                          '[data-cfemail]')
    lowered = [(n, n.lower()) for n in names]
    for node in anchors:
        href = node.get("href") or ""
        if node.get("data-cfemail"):
            address = decode_cloudflare(node["data-cfemail"])
        elif "email-protection#" in href:
            address = decode_cloudflare(href.split("#", 1)[1])
        else:
            address = urllib.parse.unquote(href[7:].split("?")[0])
        address = address.strip().lower()
        if not _EMAIL_RE.fullmatch(address):
            continue
        parent = node
        for _ in range(5):
            parent = parent.parent
            if parent is None:
                break
            text = parent.get_text(" ").lower()
            present = [n for n, low in lowered if low in text]
            if len(present) == 1:
                out.setdefault(address, present[0])
                break
            if len(present) > 1:
                break
    return out


def match_addresses(people: list[dict], emails: list[str],
                    near: dict | None = None) -> dict:
    """Pairs published addresses with published names; votes the convention.

    Returns {"pattern", "evidence", "domain"}. An address counts as a vote
    only when it rebuilds exactly from the name it belongs to, one vote per
    person, and a tie between conventions is reported as no convention rather
    than a coin flip.

    Which name it belongs to is decided by position where the page shows it
    (`near`, from addresses_near_names). Without that, only a specific
    convention may claim an address: alex@ rebuilds from every Alex, and on
    Penstead's homepage the only Alex in sight was a portfolio company's former
    VP quoted in a testimonial.
    """
    near = near or {}
    by_name = {p["name"]: p for p in people}
    votes: dict = {}
    for address in emails:
        local, _, domain = address.partition("@")
        if local in _ROLE_LOCALS or domain in company_formats.PERSONAL_DOMAINS:
            continue
        owner = by_name.get(near.get(address, ""))
        candidates = [owner] if owner else people
        for person in candidates:
            hits = [p for p in company_formats.BUILDERS
                    if company_formats.address_for(person["name"], domain, p)
                    == address]
            if not owner:
                hits = [p for p in hits if p not in ("first", "last")]
            if owner and not person.get("email"):
                person["email"] = address
            if hits:
                person["email"] = person.get("email") or address
                # "first" and "last" rebuild from almost any name; prefer the
                # more specific shape when two agree on the same address.
                pattern = sorted(hits, key=lambda p: p in ("first", "last"))[0]
                votes.setdefault((domain, pattern), set()).add(person["name"])
                break
    if not votes:
        return {"pattern": "", "evidence": "", "domain": ""}
    ranked = sorted(votes.items(), key=lambda kv: -len(kv[1]))
    if len(ranked) > 1 and len(ranked[0][1]) == len(ranked[1][1]):
        return {"pattern": "", "domain": "",
                "evidence": "published addresses disagree on a convention"}
    (domain, pattern), names = ranked[0]
    return {"pattern": pattern, "domain": domain,
            "evidence": f"{len(names)} published address(es) on the firm's "
                        f"site use {pattern}@{domain}"}


_PLACEHOLDER_DOMAINS = {"example.com", "example.org", "domain.com",
                        "email.com", "yourcompany.com", "company.com",
                        "sentry.io", "wixpress.com", "w3.org", "schema.org"}


def owned_by(domain: str, company: str, site_host: str = "") -> bool:
    """Whether a domain plausibly belongs to this company.

    A site publishes other people's addresses too: WBG's pages carried a
    riversideid.com address, Ashridge's a form placeholder at example.com, and
    taking either as the firm's mail domain builds every address wrong. The
    domain counts when it is the website's own, or its name starts with the
    company's first word (tidalfg for Valford) or its initials (gs for Quenworth
    Selford, kinarock-corp for F.N.B.).
    """
    if not domain or domain in _PLACEHOLDER_DOMAINS:
        return False
    root = ".".join((site_host or "").split(".")[-2:])
    if root and domain == root:
        return True
    label = re.sub(r"[^a-z0-9]", "", domain.split(".")[0])
    words = distinctive_words(company)
    if not words or not label:
        return False
    if len(words[0]) >= 4 and label.startswith(words[0]):
        return True
    initials = "".join(w[0] for w in words)
    return len(initials) >= 2 and label.startswith(initials)


def mail_domain(emails: list[str], site_host: str = "",
                company: str = "") -> str:
    """The domain the firm's own published addresses use, most common first."""
    from jobagent.core.company_match import _is_vendor_domain
    counts: dict = {}
    for address in emails:
        domain = address.partition("@")[2]
        if (not domain or domain in company_formats.PERSONAL_DOMAINS
                or _is_vendor_domain(domain)):
            continue
        if company and not owned_by(domain, company, site_host):
            continue
        counts[domain] = counts.get(domain, 0) + 1
    if not counts:
        return ""
    # A tie goes to the domain that matches the website, the likelier one.
    root = ".".join(site_host.split(".")[-2:]) if site_host else ""
    return sorted(counts, key=lambda d: (-counts[d], d != root))[0]


def is_people_page(url: str) -> bool:
    """Whether a URL's path says it is about the firm's people."""
    return bool(_PEOPLE_LINK.search(urllib.parse.urlparse(url).path or ""))


def people_links(html: str, base: str) -> list[str]:
    """Links the page itself labels as about/team/people, same site only."""
    host = urllib.parse.urlparse(base).netloc.lower().removeprefix("www.")
    out = []
    for href, text in re.findall(
            r'(?is)<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', html):
        label = re.sub(r"<[^>]+>", " ", text)
        if not (_PEOPLE_LINK.search(href) or _PEOPLE_LINK.search(label)):
            continue
        url = urllib.parse.urljoin(base, href)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc.lower().removeprefix("www.") != host:
            continue
        if re.search(r"\.(pdf|jpg|png|zip|docx?)$", parsed.path, re.I):
            continue
        url = url.split("?")[0].rstrip("/")
        if url not in out:
            out.append(url)
    return out


# ---------------------------------------------------------------------------
# Network and cache.

def _fetch(url: str, timeout: int = 15) -> tuple[str, str]:
    """(final url, html). Raises on any failure."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                               "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if "html" not in (resp.headers.get("Content-Type") or "html"):
            raise ValueError("not html")
        return resp.geturl(), resp.read(2_000_000).decode("utf-8", "ignore")


def _cache_key(company: str) -> str:
    return company_formats._slugify(company) or (company or "").lower()


def _load_cache() -> dict:
    return storage.read(CACHE, {}) or {}


def _save_cache(cache: dict) -> None:
    storage.write(CACHE, cache, indent=1, sort_keys=True)


def find_website(company: str, hints: tuple = (), fetch=None,
                 lookup=None) -> tuple[str, str, str]:
    """(homepage url, html, how it was found), or ("", "", why not).

    `lookup` is company -> (official url, why), Wikidata by default when the
    real network is in use; tests pass their own or leave it out.
    """
    fetch = fetch or _fetch
    tried = []
    words = distinctive_words(company)
    for hint in hints:
        if not hint:
            continue
        url = hint if "://" in hint else f"https://{hint}"
        parsed = urllib.parse.urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        try:
            final, html = fetch(root)
        except Exception:
            tried.append(root)
            continue
        # A hint is evidence already, so it does not need the title test —
        # but a parked or unrelated domain still should not pass.
        if names_company(html, company) or (words and words[0] in html.lower()):
            return final, html, f"given ({root})"
        # The firm's domain is known and it answered; it just cannot be read
        # (a bot check, a JavaScript shell). Guessing past it is how a
        # lookalike gets read instead, so stop here and say so.
        return "", "", f"{root} answered but shows nothing readable"
    # The domain spelled exactly like the company is still asked first. If it
    # answers, it is the likelier owner — and if it answers with a bot check,
    # stopping is what kept Merworth Group from being read as the German
    # jobs board that shares its legal name (and Wikidata's best match).
    full = "".join(_full_words(company))
    exact = {f"https://www.{full}.com", f"https://{full}.com"}
    for url in [u for u in site_guesses(company) if u in exact]:
        try:
            final, html = fetch(url)
        except Exception:
            tried.append(url)
            continue
        tried.append(url)
        if names_company(html, company, strict=True):
            return final, html, f"guessed ({url}); its title names the company"
        return "", "", f"{url} answered but shows nothing readable"
    # Wikidata's official-website field, before the looser guesses: those
    # have to prove themselves with a page title, and this has been stated.
    if lookup is None and fetch is _fetch:
        lookup = site_discovery.wikidata_website
    if lookup:
        official, why = lookup(company)
        if official:
            parsed = urllib.parse.urlparse(official)
            root = f"{parsed.scheme}://{parsed.netloc}"
            try:
                final, html = fetch(root)
            except Exception:
                final, html = "", ""
            # The page must still name the company: a wrong entity's site
            # names something else.
            if html and names_company(html, company):
                return final, html, f"{why} ({root})"
            # The firm's own stated site refuses a plain fetch (Ashhaven,
            # Belmoor). Guessing past it only finds lookalikes, and getting
            # past a bot check is not something this does — so say so.
            return "", "", f"official site {root} ({why}) cannot be read by a script"
    for url in site_guesses(company):
        if url in tried:
            continue
        try:
            final, html = fetch(url)
        except Exception:
            continue
        if names_company(html, company, strict=True):
            return final, html, f"guessed ({url}); its title names the company"
        # The exact-name domain exists but its page is a bot check with no
        # title. That is Merworth Group's site; the next guess, merworth.com,
        # is a German jobs board whose legal name is also "The Merworth
        # Group". The domain that answered to the full name is the likelier
        # owner, so stop rather than read a namesake.
        if url in exact:
            return "", "", f"{url} answered but shows nothing readable"
    return "", "", "no website found that names the company"


def find(company: str, domain: str = "", website: str = "",
         refresh: bool = False, fetch=None) -> dict:
    """Everything the firm's own site publishes about its people.

    {website, pages, people, emails, mail_domain, pattern, pattern_evidence,
     notes}. Each person is {name, title, email, source, url, verified}.
    `fetch` is injectable so tests run without a network.
    """
    key = _cache_key(company)
    cache = {} if fetch else _load_cache()
    entry = cache.get(key)
    if entry and not refresh:
        try:
            age = (datetime.date.today()
                   - datetime.date.fromisoformat(entry["fetched"])).days
        except (KeyError, ValueError):
            age = CACHE_DAYS + 1
        if age <= CACHE_DAYS:
            return entry["result"]

    fetch = fetch or _fetch
    result = {"company": company, "website": "", "pages": [], "people": [],
              "emails": [],
              "mail_domain": "", "pattern": "", "pattern_evidence": "",
              "notes": []}
    home, html, how = find_website(company, (website, domain), fetch=fetch)
    if not home:
        result["notes"].append(how)
        return _remember(cache, key, result, fetch)
    result["website"] = home
    result["notes"].append(f"website {home} — {how}")

    base = home.rstrip("/")
    queue = [base] + people_links(html, base) + [base + p for p in _PATHS]
    pages, seen = {base: html}, {base}
    for url in queue:
        if len(pages) >= MAX_PAGES:
            break
        if url in seen:
            continue
        seen.add(url)
        try:
            final, page = fetch(url)
        except Exception:
            continue
        final = final.split("?")[0].rstrip("/")
        if final in pages:
            continue
        pages[final] = page
        time.sleep(0.2)

    # Names come only from pages that are about people. A homepage quotes
    # clients and partners in the same name-then-title shape as a team card,
    # and marketing copy ("Supported Autonomy / Independent advisor" on
    # Fenridge's) reads as a name too. Addresses are read from every page.
    people, emails, near = [], [], {}
    for url, page in pages.items():
        page_emails = emails_in(page)
        emails += [e for e in page_emails if e not in emails]
        if not is_people_page(url):
            continue
        found = people_in(page)
        path = urllib.parse.urlparse(url).path or "/"
        for person in found:
            person.update(source=f"firm site ({path})", url=url, verified=True)
            people.append(person)
        if page_emails and found:
            for address, name in addresses_near_names(
                    page, [p["name"] for p in found]).items():
                near.setdefault(address, name)

    merged, by_name = [], {}
    words = distinctive_words(company)
    for person in people:
        if works_elsewhere(person["title"], words):
            continue
        k = person["name"].lower()
        if k in by_name:
            kept = by_name[k]
            kept["title"] = kept["title"] or person["title"]
            kept["email"] = kept["email"] or person["email"]
            continue
        by_name[k] = person
        merged.append(person)

    # The sitemap's bio pages, for the people no team page named — the whole
    # firm, when the grid is drawn by JavaScript and read as an empty shell.
    text_fetch = ((lambda u: site_discovery._default_fetch(u))
                  if fetch is _fetch else (lambda u: fetch(u)[1]))
    try:
        parser = site_discovery.robots(base, fetch=text_fetch)
        bios = site_discovery.bio_pages(base, fetch=text_fetch, parser=parser)
    except Exception:
        bios = []
    named = {_person_key(k) for k in by_name}
    read_bios = 0
    for url, slug_name in spread(bios, MAX_BIOS):
        if read_bios >= MAX_BIOS:
            break
        if _person_key(slug_name) in named:
            continue
        try:
            final, page = fetch(url)
        except Exception:
            continue
        read_bios += 1
        pages[final.split("?")[0].rstrip("/")] = page
        emails += [e for e in emails_in(page) if e not in emails]
        person = bio_person(page, slug_name)
        if not person or works_elsewhere(person["title"], words):
            continue
        path = urllib.parse.urlparse(url).path or "/"
        for address, name in addresses_near_names(page, [person["name"]]).items():
            near.setdefault(address, name)
        person.update(source=f"firm site bio ({path})", url=url, verified=True,
                      bio=prose(page), linkedin=linkedin_link(page, person["name"]))
        if _person_key(person["name"]) in named:
            continue
        by_name[person["name"].lower()] = person
        named.add(_person_key(person["name"]))
        named.add(_person_key(slug_name))
        merged.append(person)
        if fetch is _fetch:
            time.sleep(0.2)
    if bios:
        result["notes"].append(
            f"sitemap lists {len(bios)} bio page(s); read {read_bios}")

    host = urllib.parse.urlparse(home).netloc.lower().removeprefix("www.")
    convention = match_addresses(
        merged, [e for e in emails
                 if owned_by(e.partition("@")[2], company, host)], near)
    result.update(
        pages=list(pages), people=merged, emails=emails,
        mail_domain=convention["domain"] or mail_domain(emails, host, company),
        pattern=convention["pattern"],
        pattern_evidence=convention["evidence"])
    result["notes"].append(
        f"read {len(pages)} page(s): {len(merged)} named, "
        f"{len(emails)} address(es) published"
        + (f", mail at {result['mail_domain']}" if result["mail_domain"] else ""))
    if result["pattern"]:
        _record_convention(result["mail_domain"], result["pattern"],
                           result["pattern_evidence"], home, dry=bool(fetch is not _fetch))
    return _remember(cache, key, result, fetch)


def _remember(cache: dict, key: str, result: dict, fetch) -> dict:
    if fetch is _fetch:
        cache[key] = {"fetched": datetime.date.today().isoformat(),
                      "result": result}
        try:
            _save_cache(cache)
        except OSError:
            pass
    return result


def _record_convention(domain: str, pattern: str, evidence: str, home: str,
                       dry: bool = False) -> None:
    """A convention the firm published is worth keeping for later drafts.

    Never overwrites an entry already in the registry: one there came from
    somebody's lookup or a delivered address, and this is one page's say-so.
    """
    if dry or not domain:
        return
    registry = company_formats.load()
    if domain in registry:
        return
    registry[domain] = {"pattern": pattern,
                        "source": f"published on {home}: {evidence}"}
    company_formats.save(registry)


def run(company: str, domain: str = "", website: str = "",
        refresh: bool = False) -> None:
    from jobagent.people import people_search

    result = find(company, domain=domain, website=website, refresh=refresh)
    for note in result["notes"]:
        print(f"  · {note}")
    if result["mail_domain"]:
        print(f"  · convention: {result['pattern'] or 'not shown'}"
              + (f" — {result['pattern_evidence']}" if result["pattern"] else ""))
    if not result["people"]:
        print("\nThe site names nobody with a title.")
        if result["emails"]:
            print("Published addresses: " + ", ".join(result["emails"][:8]))
        return
    print(f"\n{len(result['people'])} published name(s):\n")
    for person in people_search.rank(list(result["people"])):
        print(f"  {person['band']:11s} {person['name'][:26]:26s} "
              f"{person.get('email', '')[:32]:32s} {person['title'][:44]}")
