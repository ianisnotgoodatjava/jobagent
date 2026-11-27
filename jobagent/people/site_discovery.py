"""Finding the pages on a firm's site that name its people, and the site itself.

site_people read a dozen likely paths (/team, /about, /leadership) and the
links the homepage labels that way. Measured over the 74 firms it had tried,
43 came back with nobody, for three reasons this module addresses:

  no website found          17 firms. The name did not spell a .com that
                            answered with the company in its title — Radvale,
                            Belmoor, Ashhaven. Wikidata records each
                            company's official website (property P856),
                            free and structured, which a guess cannot beat.
  nothing readable          7 firms. The team grid is drawn by JavaScript,
                            so a plain fetch sees an empty shell. The
                            sitemap usually still lists one URL per person
                            — /team/jane-doe — and a bio page is ordinary
                            HTML even when the grid is not.
  nobody on the usual paths the rest. Their people live at
                            /professionals/<name> or /our-people/<name>,
                            which only the sitemap names.

Politeness is part of the method, not an afterthought: robots.txt is read and
obeyed, and the caller caps how many pages it fetches.

Everything takes `fetch` (url -> text) so the tests touch no network.
"""

import json
import re
import urllib.parse
import urllib.request
import urllib.robotparser

# The identity Wikimedia asks automated clients to send. Generic on purpose:
# it names the tool, never the person running it.
WIKIDATA_UA = "job-tracker-people-lookup/1.0 (personal research script)"
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Path segments that sit directly above one person's page.
_BIO_PARENTS = {
    "team", "our-team", "people", "our-people", "professionals", "leadership",
    "bio", "bios", "staff", "partners", "person", "profile", "profiles",
    "about-us/team", "who-we-are", "management", "experts", "advisors",
    "directors", "principals", "our-professionals", "meet-the-team",
}

# Slugs under those parents that are sections, not people.
_NOT_A_PERSON_SLUG = {
    "leadership", "board", "management", "team", "people", "careers",
    "our-team", "all", "page", "category", "tag", "search", "contact",
    "board-of-directors", "executive-team", "senior-leadership", "advisors",
    "investment-team", "operations-team", "about", "overview",
}

_PARTICLES = {"de", "da", "van", "von", "der", "del", "la", "le", "di", "du"}


def _default_fetch(url: str, timeout: int = 15, ua: str = "") -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": ua or ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/125.0 Safari/537.36")})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(5_000_000).decode("utf-8", "ignore")


# ---------------------------------------------------------------------------
# robots.txt

def robots(base: str, fetch=None):
    """A parser for the site's robots.txt, or None when there is none.

    No robots.txt means no stated restriction, which is the convention.
    """
    fetch = fetch or _default_fetch
    try:
        text = fetch(base.rstrip("/") + "/robots.txt")
    except Exception:
        return None
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(text.splitlines())
    return parser


def allowed(parser, url: str) -> bool:
    return parser is None or parser.can_fetch("*", url)


def sitemaps_in(robots_text: str) -> list[str]:
    return re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots_text or "")


# ---------------------------------------------------------------------------
# sitemaps

def sitemap_urls(xml: str) -> tuple[list[str], list[str]]:
    """(page urls, child sitemap urls) from one sitemap or sitemap index."""
    locs = [urllib.parse.unquote(u.strip())
            for u in re.findall(r"(?is)<loc>\s*(.*?)\s*</loc>", xml or "")]
    if re.search(r"(?i)<sitemapindex", xml or ""):
        return [], locs
    return locs, []


# Words that put a slug outside the set of names. Measured on real sitemaps:
# Stoford's /industries/aerospace-and-defense and Calmere's
# /about/analyst-coverage both read as two-word names without this, which
# taught the directory test that /industries/ lists people.
_FUNCTION_WORDS = {
    "and", "of", "the", "for", "in", "to", "with", "on", "at", "by", "our",
    "your", "we", "us", "how", "why", "what", "is", "are", "a", "an", "vs",
    "from", "into", "new", "about", "all",
}
_SLUG_VOCAB = {
    "services", "service", "solutions", "solution", "industries", "industry",
    "financial", "finance", "capital", "markets", "market", "management",
    "analyst", "analysts", "coverage", "awards", "recognition", "partner",
    "partners", "program", "policy", "privacy", "statement", "terms",
    "notice", "cookie", "cookies", "legal", "disclosures", "careers",
    "investor", "investors", "relations", "news", "press", "release",
    "insights", "report", "reports", "risk", "advisory", "consulting",
    "strategy", "technology", "data", "digital", "energy", "health",
    "healthcare", "real", "estate", "private", "equity", "credit", "debt",
    "fund", "funds", "asset", "assets", "wealth", "trading", "operations",
    "compliance", "regulatory", "tax", "audit", "accounting", "litigation",
    "disputes", "restructuring", "transactions", "transaction", "deals",
    "office", "offices", "locations", "contact", "events", "webinar",
    "webinars", "team", "teams", "leadership", "board", "committee",
    "governance", "sustainability", "esg", "overview", "history", "mission",
    "values", "culture", "diversity", "inclusion", "community", "impact",
    "global", "americas", "europe", "asia", "north", "south", "east", "west",
    "construction", "infrastructure", "aerospace", "defense", "government",
    "public", "sector", "insurance", "banking", "bank", "lending", "life",
    "cycle", "trade", "processing", "platform", "products", "product",
    "client", "clients", "case", "study", "studies", "resources", "guide",
    "blog", "podcast", "video", "videos", "executive", "executives",
    "experts", "expert", "advisors", "professionals", "people", "staff",
    "middle", "back", "front", "home", "page", "site", "map", "search",
}


def name_from_slug(slug: str) -> str:
    """"jane-a-doe" -> "Jane A Doe"; "" when the slug is not a name."""
    slug = re.sub(r"\.(html?|php|aspx)$", "", slug.lower())
    parts = [p for p in re.split(r"[-_]+", slug) if p]
    if not 2 <= len(parts) <= 4:
        return ""
    if not all(re.fullmatch(r"[a-z]+", p) for p in parts):
        return ""
    if any(p in _FUNCTION_WORDS or p in _SLUG_VOCAB for p in parts):
        return ""
    if len(parts[0]) < 2 or len(parts[-1]) < 2:
        return ""
    return " ".join(p if p in _PARTICLES else p.capitalize() for p in parts)


def _split(url: str) -> tuple[str, str, str]:
    """(host, parent path, last segment) of a URL."""
    parsed = urllib.parse.urlparse(url)
    segments = [s for s in parsed.path.lower().split("/") if s]
    host = parsed.netloc.lower().removeprefix("www.")
    if len(segments) < 2:
        return host, "", ""
    return host, "/".join(segments[:-1]), segments[-1]


def _known_parent(parent: str) -> bool:
    tail = parent.split("/")
    return tail[-1] in _BIO_PARENTS or "/".join(tail[-2:]) in _BIO_PARENTS


def bio_url(url: str, host: str = "", parents: frozenset = frozenset()) -> str:
    """The name a URL is a bio page for, or "" when it is not one.

    /team/jane-doe qualifies by its folder's name; /experts-and-advisors/
    jane-doe qualifies when `parents` (learned by people_directories) says
    that folder is where this site keeps its people. /team/investment-team
    and /news/jane-doe-joins-firm do not.
    """
    url_host, parent, slug = _split(url)
    if not parent or (host and url_host != host):
        return ""
    if not (_known_parent(parent) or parent in parents):
        return ""
    if slug in _NOT_A_PERSON_SLUG:
        return ""
    return name_from_slug(slug)


# A folder is a people directory when it holds at least this many pages and
# this share of their slugs read as names. Stoford keeps 187 people under
# /experts-and-advisors/, a name no fixed list would carry; its /news/ and
# /services/ folders hold slugs that are sentences and phrases.
MIN_DIRECTORY = 5
NAME_SHARE = 0.6


def people_directories(urls: list[str]) -> frozenset:
    """The parent folders, learned from the URLs themselves, that list people."""
    groups: dict[str, list[str]] = {}
    for url in urls:
        _, parent, slug = _split(url)
        if parent:
            groups.setdefault(parent, []).append(slug)
    out = set()
    for parent, slugs in groups.items():
        if len(slugs) < MIN_DIRECTORY:
            continue
        named = sum(1 for s in slugs if name_from_slug(s))
        if named / len(slugs) >= NAME_SHARE:
            out.add(parent)
    return frozenset(out)


def bio_pages(base: str, fetch=None, parser=None, limit: int = 400,
              max_sitemaps: int = 6) -> list[tuple[str, str]]:
    """[(url, name)] for every person page the site's sitemaps list.

    Sitemaps are found in robots.txt first and at the two usual paths after.
    An index is followed one level, to at most `max_sitemaps` children, and
    the children whose names say people (team-sitemap.xml, people.xml) are
    read before the rest.
    """
    fetch = fetch or _default_fetch
    base = base.rstrip("/")
    host = urllib.parse.urlparse(base).netloc.lower().removeprefix("www.")
    queue = []
    try:
        queue += sitemaps_in(fetch(base + "/robots.txt"))
    except Exception:
        pass
    queue += [base + "/sitemap.xml", base + "/sitemap_index.xml"]

    urls, seen_maps = [], set()
    while queue and len(seen_maps) < max_sitemaps:
        sm = queue.pop(0)
        if sm in seen_maps or not allowed(parser, sm):
            continue
        seen_maps.add(sm)
        try:
            pages, children = sitemap_urls(fetch(sm))
        except Exception:
            continue
        children.sort(key=lambda u: not re.search(
            r"(?i)(team|people|professional|staff|bio|person|leader|expert)", u))
        queue = children + queue
        urls += pages

    parents = people_directories(urls)
    out, seen = [], set()
    for url in urls:
        url = url.rstrip("/")
        name = bio_url(url, host, parents)
        if name and url not in seen and allowed(parser, url):
            seen.add(url)
            out.append((url, name))
    return out[:limit]


# ---------------------------------------------------------------------------
# Wikidata: a company's official website

def _fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# What an employer's Wikidata description says it is. An airport, a country,
# a song or a person never says any of these.
_BUSINESS = re.compile(
    r"(?i)\b(company|companies|corporation|firm|bank|banking|business|"
    r"conglomerate|enterprise|insurer|insurance|manager|management|"
    r"investment|financial|exchange|brokerage|consultancy|consulting|"
    r"utility|provider|agency|holding|group|fund|developer|operator|"
    r"manufacturer|retailer|publisher|organi[sz]ation|subsidiary|lender)\b")


def wikidata_website(company: str, get_json=None) -> tuple[str, str]:
    """(official website, how it was found), or ("", why not).

    Only an entity whose own label or alias matches the company is accepted
    — a search for "Normere's" also returns the 1920s jazz musician.
    """
    get_json = get_json or _wikidata_get
    try:
        found = get_json({"action": "wbsearchentities", "search": company,
                          "language": "en", "type": "item", "limit": "7",
                          "format": "json"})
    except Exception as exc:
        return "", f"Wikidata unavailable: {type(exc).__name__}"
    want = _fold(re.split(r"\(", company)[0])
    legal = re.compile(r"(inc|llc|lp|llp|ltd|corp|corporation|group|plc|"
                       r"holdings|company|co)$")
    want_core = legal.sub("", want)
    ids = []
    for hit in found.get("search", []):
        # An alias is how "QLC" found the People's Republic of China (its
        # German abbreviation) and "WBG" found Darwin Airport (its code).
        # Short names must match the entity's own label.
        labels = [hit.get("label", "")]
        if len(want_core) >= 6:
            labels += list(hit.get("aliases") or [])
            labels.append(hit.get("match", {}).get("text", ""))
        if not _BUSINESS.search(hit.get("description") or ""):
            continue
        for label in labels:
            got = _fold(label)
            if got and (got == want or legal.sub("", got) == want_core):
                ids.append(hit["id"])
                break
    if not ids:
        return "", "Wikidata has no entity by that name"
    try:
        data = get_json({"action": "wbgetentities", "ids": "|".join(ids[:5]),
                         "props": "claims|labels", "format": "json"})
    except Exception as exc:
        return "", f"Wikidata unavailable: {type(exc).__name__}"
    for qid in ids:
        claims = data.get("entities", {}).get(qid, {}).get("claims", {})
        for claim in claims.get("P856", []):
            value = (claim.get("mainsnak", {}).get("datavalue", {})
                     .get("value"))
            if isinstance(value, str) and value.startswith("http"):
                return value, f"Wikidata {qid} official website"
    return "", "Wikidata lists no official website"


def _wikidata_get(params: dict, retries: int = 2) -> dict:
    """One API call, paced, honouring Retry-After when Wikimedia asks."""
    import time
    import urllib.error
    url = _WIKIDATA_API + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries + 1):
        time.sleep(0.5)
        try:
            return json.loads(_default_fetch(url, timeout=15, ua=WIKIDATA_UA))
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == retries:
                raise
            try:
                wait = float(exc.headers.get("Retry-After") or 5)
            except ValueError:
                wait = 5
            time.sleep(min(wait, 30))
    return {}
