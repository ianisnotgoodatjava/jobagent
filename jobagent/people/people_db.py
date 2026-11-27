"""One place that remembers every person found at every firm applied to.

Each source kept its own file — site_people.json, contacts_found.json, the
common-ground store — and none of them said which application a person was found
for, or that the same person had turned up in two places. So a search for
a firm already researched started from nothing, and a name the firm's site
and FINRA both confirmed ranked the same as a name one org chart claimed.

This is a SQLite file in logs/, gitignored with the rest of logs/, because
it holds real people's names. Like everything in logs/ it is derived: it can
be deleted and rebuilt with `backfill()` plus fresh searches. Gmail stays
the source of truth for who was written to; this records who exists.

    people      one row per (firm, person). Title, best address and how
                that address is known, first and last seen.
    sightings   one row per source that named them: firm site, bio page,
                FINRA, org chart, a past outreach. Corroboration is the
                number of distinct sources here.
    matches     one row per (person, application): how near their desk is
                to that role, and why, as last computed.
    education   one row per degree a bio or list states: school, degree,
                field, year.
    employment  one row per job: the bio's "prior to joining X" and every
                FINRA registration, with dates where the registry has them.
    facts       everything else that is one value: a credential (CFA), a
                FINRA exam (Series 79), a language, years of experience.
    firms       one row per firm: website, mail domain, and the address
                convention with how it is known.

Read-only towards everything else: no email, no sheet writes.
"""

import datetime
import json
import os
import re
import sqlite3

from jobagent.core import affinity
from jobagent.core import app_identity
from jobagent.core import bio_facts
from jobagent.core import company_id
from jobagent.core import desk_match
from jobagent.core import storage

DB_PATH = storage.database("people")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id           INTEGER PRIMARY KEY,
    company_key  TEXT NOT NULL,
    company      TEXT NOT NULL,
    name         TEXT NOT NULL,
    name_key     TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    location     TEXT NOT NULL DEFAULT '',
    email        TEXT NOT NULL DEFAULT '',
    email_basis  TEXT NOT NULL DEFAULT '',
    verified     INTEGER NOT NULL DEFAULT 0,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    UNIQUE (company_key, name_key)
);
CREATE TABLE IF NOT EXISTS sightings (
    person_id  INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    source     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    url        TEXT NOT NULL DEFAULT '',
    title      TEXT NOT NULL DEFAULT '',
    seen       TEXT NOT NULL,
    UNIQUE (person_id, kind, url)
);
CREATE TABLE IF NOT EXISTS matches (
    person_id  INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    app_key    TEXT NOT NULL,
    company    TEXT NOT NULL,
    role       TEXT NOT NULL,
    band       TEXT NOT NULL,
    desk       REAL NOT NULL,
    why        TEXT NOT NULL,
    computed   TEXT NOT NULL,
    PRIMARY KEY (person_id, app_key)
);
CREATE TABLE IF NOT EXISTS education (
    person_id  INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    school     TEXT NOT NULL,
    school_key TEXT NOT NULL,
    degree     TEXT NOT NULL DEFAULT '',
    field      TEXT NOT NULL DEFAULT '',
    year       TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT '',
    UNIQUE (person_id, school_key, degree)
);
CREATE TABLE IF NOT EXISTS employment (
    person_id  INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    firm       TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    city       TEXT NOT NULL DEFAULT '',
    state      TEXT NOT NULL DEFAULT '',
    start      TEXT NOT NULL DEFAULT '',
    "end"      TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT '',
    UNIQUE (person_id, firm, start)
);
CREATE TABLE IF NOT EXISTS facts (
    person_id  INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    value      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT '',
    seen       TEXT NOT NULL,
    UNIQUE (person_id, kind, value)
);
CREATE TABLE IF NOT EXISTS firms (
    company_key  TEXT PRIMARY KEY,
    company      TEXT NOT NULL,
    website      TEXT NOT NULL DEFAULT '',
    domain       TEXT NOT NULL DEFAULT '',
    pattern      TEXT NOT NULL DEFAULT '',
    pattern_basis TEXT NOT NULL DEFAULT '',
    finra_firm   TEXT NOT NULL DEFAULT '',
    updated      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS people_company ON people(company_key);
CREATE INDEX IF NOT EXISTS matches_app ON matches(app_key);
"""

# Which family of evidence a source string belongs to. Two bio pages on the
# same site are one source; the firm's site and FINRA are two.
_KINDS = (
    ("finra", re.compile(r"(?i)finra|brokercheck")),
    ("firm_site", re.compile(r"(?i)firm site|team page|our-team|public team|staff page")),
    ("org_chart", re.compile(r"(?i)org chart|theorg")),
    ("sec", re.compile(r"(?i)edgar|form d|sec\b|prospectus")),
    ("mailbox", re.compile(r"(?i)mailbox|delivered|replied|outreach")),
    ("research", re.compile(r"(?i)muse|search|common ground")),
)


def kind_of(source: str) -> str:
    for kind, pattern in _KINDS:
        if pattern.search(source or ""):
            return kind
    return "other"


def name_key(name: str) -> str:
    """"Nora J. Vance, Jr." and "Nora Vance" are the same person."""
    name = re.sub(r",.*$", "", name or "")
    words = [w for w in re.sub(r"[^a-z\s'-]", " ", name.lower()).split()
             if len(w.strip(".")) > 1 and w not in {"jr", "sr", "ii", "iii"}]
    if len(words) < 2:
        return " ".join(words)
    return f"{words[0]} {words[-1]}"


# Columns added to `people` after the first version, so an existing file
# gains them instead of having to be rebuilt.
_PEOPLE_COLUMNS = {
    "crd": "TEXT NOT NULL DEFAULT ''",            # FINRA's id for the person
    "industry_start": "TEXT NOT NULL DEFAULT ''",  # FINRA: entered the industry
    "bio": "TEXT NOT NULL DEFAULT ''",             # the firm's bio text
    "bio_url": "TEXT NOT NULL DEFAULT ''",
    "linkedin": "TEXT NOT NULL DEFAULT ''",        # as linked by the firm's page
}


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    # WAL and a busy timeout (storage.connect_db): the scheduled run and the
    # MCP server can now write at the same moment.
    db = storage.connect_db(path)
    db.executescript(_SCHEMA)
    have = {r["name"] for r in db.execute("PRAGMA table_info(people)")}
    for column, decl in _PEOPLE_COLUMNS.items():
        if column not in have:
            db.execute(f"ALTER TABLE people ADD COLUMN {column} {decl}")
    db.commit()
    return db


def _today() -> str:
    return datetime.date.today().isoformat()


def record(db: sqlite3.Connection, company: str, people: list[dict],
           seen: str = "") -> int:
    """Store what one search returned. Returns how many people were new.

    A later sighting fills what an earlier one lacked — a title, an address
    — but never replaces a published address with an inferred one.
    """
    seen = seen or _today()
    ckey = company_id.key(company)
    if not ckey:
        return 0
    new = 0
    for p in people:
        name = (p.get("name") or "").strip()
        nkey = name_key(name)
        if len(nkey.split()) < 2:
            continue
        email = (p.get("email") or "").strip().lower()
        basis = p.get("address_basis") or p.get("confidence") or (
            "published on the firm's site" if p.get("verified") and email else "")
        row = db.execute("SELECT * FROM people WHERE company_key=? AND name_key=?",
                         (ckey, nkey)).fetchone()
        if row is None:
            cur = db.execute(
                "INSERT INTO people (company_key, company, name, name_key, title,"
                " location, email, email_basis, verified, first_seen, last_seen)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ckey, company, name, nkey, p.get("title") or "",
                 p.get("city") or p.get("location") or "", email, basis,
                 int(bool(p.get("verified"))), seen, seen))
            pid = cur.lastrowid
            new += 1
        else:
            pid = row["id"]
            published = "published" in (row["email_basis"] or "")
            keep_email = row["email"] and (published or not email)
            db.execute(
                "UPDATE people SET title=?, location=?, email=?, email_basis=?,"
                " verified=?, last_seen=max(last_seen, ?) WHERE id=?",
                (row["title"] or p.get("title") or "",
                 row["location"] or p.get("city") or p.get("location") or "",
                 row["email"] if keep_email else email,
                 row["email_basis"] if keep_email else basis,
                 int(row["verified"] or bool(p.get("verified"))), seen, pid))
        source = p.get("source") or "unknown"
        db.execute(
            "INSERT OR IGNORE INTO sightings (person_id, source, kind, url, title,"
            " seen) VALUES (?,?,?,?,?,?)",
            (pid, source, kind_of(source), p.get("url") or "",
             p.get("title") or "", seen))
        _record_profile(db, pid, company, p, source, seen)
    db.commit()
    return new


def _record_profile(db: sqlite3.Connection, pid: int, company: str, p: dict,
                    source: str, seen: str) -> None:
    """Everything beyond name and title one sighting carries.

    A bio is read by bio_facts here, so every caller that has a bio gets its
    education and past firms stored without having to parse it. FINRA's
    record (`p["finra"]`, from people_finder.details) adds exams and every
    registration. Nothing already stored is overwritten; a second source
    only adds rows the first did not have.
    """
    sets = {}
    if p.get("crd"):
        sets["crd"] = str(p["crd"])
    if p.get("started"):
        sets["industry_start"] = p["started"]
    if p.get("bio"):
        sets["bio"] = p["bio"]
        sets["bio_url"] = p.get("url") or ""
    if p.get("linkedin"):
        sets["linkedin"] = p["linkedin"]
    for column, value in sets.items():
        db.execute(f"UPDATE people SET {column}=? WHERE id=? AND {column}=''",
                   (value, pid))

    facts = bio_facts.read(p["bio"], company) if p.get("bio") else {}
    for e in list(p.get("education") or []) + facts.get("education", []):
        db.execute(
            "INSERT OR IGNORE INTO education (person_id, school, school_key,"
            " degree, field, year, source) VALUES (?,?,?,?,?,?,?)",
            (pid, e["school"], bio_facts.school_key(e["school"]),
             e.get("degree", ""), e.get("field", ""), e.get("year", ""), source))
    for e in list(p.get("employment") or []) + facts.get("employment", []):
        _add_job(db, pid, e, source)

    def fact(kind, value, detail=""):
        if value in ("", None):
            return
        db.execute(
            "INSERT OR IGNORE INTO facts (person_id, kind, value, detail,"
            " source, seen) VALUES (?,?,?,?,?,?)",
            (pid, kind, str(value), detail, source, seen))

    for c in facts.get("credentials", []):
        fact("credential", c)
    for lang in facts.get("languages", []):
        fact("language", lang)
    fact("years_experience", facts.get("years_experience"))

    finra = p.get("finra") or {}
    if finra:
        for exam in finra.get("exams") or []:
            fact("exam", bio_facts.exam_code(exam["code"]),
                 f"{exam['code']}: {exam.get('name', '')} ({exam.get('taken', '')})")
        for job in finra.get("employment") or []:
            _add_job(db, pid, job, "FINRA BrokerCheck")
        fact("finra_checked", "yes", "", )
        db.execute("UPDATE facts SET seen=? WHERE person_id=? AND "
                   "kind='finra_checked'", (seen, pid))


def _add_job(db, pid: int, job: dict, source: str) -> None:
    firm = (job.get("firm") or "").strip()
    if not firm:
        return
    # The bio's "Normere's" and FINRA's "MOODY'S INVESTORS SERVICE" are one
    # job; keep the first, which is the one with dates when FINRA came first.
    for row in db.execute("SELECT firm FROM employment WHERE person_id=?", (pid,)):
        if affinity.same_employer(row["firm"], firm):
            return
    db.execute(
        'INSERT OR IGNORE INTO employment (person_id, firm, title, city, state,'
        ' start, "end", source) VALUES (?,?,?,?,?,?,?,?)',
        (pid, firm, job.get("title", ""), job.get("city", ""),
         job.get("state", ""), job.get("start", ""), job.get("end", ""), source))


def finra_checked(db: sqlite3.Connection, company: str, name: str,
                  max_age: int = 90) -> bool:
    """Whether this person's BrokerCheck record was read in the last
    `max_age` days, so a repeat search does not ask FINRA again."""
    row = db.execute(
        "SELECT f.seen FROM facts f JOIN people p ON p.id=f.person_id WHERE "
        "p.company_key=? AND p.name_key=? AND f.kind='finra_checked'",
        (company_id.key(company), name_key(name))).fetchone()
    if not row:
        return False
    try:
        age = (datetime.date.today() - datetime.date.fromisoformat(row["seen"])).days
    except ValueError:
        return False
    return age <= max_age


def record_firm(db: sqlite3.Connection, company: str, website: str = "",
                domain: str = "", pattern: str = "", basis: str = "",
                finra_firm: str = "") -> None:
    """What is known about the firm itself. Empty values never erase."""
    ckey = company_id.key(company)
    if not ckey:
        return
    db.execute("INSERT OR IGNORE INTO firms (company_key, company, updated)"
               " VALUES (?,?,?)", (ckey, company, _today()))
    for column, value in (("website", website), ("domain", domain),
                          ("pattern", pattern), ("pattern_basis", basis),
                          ("finra_firm", finra_firm)):
        if value:
            db.execute(f"UPDATE firms SET {column}=?, updated=? WHERE company_key=?",
                       (value, _today(), ckey))
    db.commit()


def firm(db: sqlite3.Connection, company: str) -> dict:
    row = db.execute("SELECT * FROM firms WHERE company_key=?",
                     (company_id.key(company),)).fetchone()
    return dict(row) if row else {}


def profile(db: sqlite3.Connection, person_id: int) -> dict:
    """{education, employment, credentials, exams, languages,
    years_experience} for one person, as stored."""
    q = lambda sql: [dict(r) for r in db.execute(sql, (person_id,))]
    facts = q("SELECT kind, value, detail FROM facts WHERE person_id=?")
    by = lambda kind: [f["value"] for f in facts if f["kind"] == kind]
    years = by("years_experience")
    return {
        "education": q("SELECT school, degree, field, year, source FROM "
                       "education WHERE person_id=?"),
        "employment": q('SELECT firm, title, city, state, start, "end", source'
                        ' FROM employment WHERE person_id=? ORDER BY start DESC'),
        "credentials": by("credential"),
        "exams": by("exam"),
        "languages": by("language"),
        "years_experience": int(years[0]) if years else None,
    }


def at_company(db: sqlite3.Connection, company: str, role: str = "",
               location: str = "") -> list[dict]:
    """Everyone known at a firm, ranked for `role`, best first.

    Ranking is people_search's — seniority band before desk — with the desk
    read by desk_match, and two tie-breaks that only a database can supply:
    how many independent sources name the person, and whether they sit in
    the job's city.
    """
    ckey = company_id.key(company)
    rows = db.execute("SELECT * FROM people WHERE company_key=?", (ckey,)).fetchall()
    out = []
    for row in rows:
        sights = db.execute(
            "SELECT source, kind, url, seen FROM sightings WHERE person_id=?",
            (row["id"],)).fetchall()
        person = dict(row)
        person["sources"] = sorted({s["source"] for s in sights})
        person["corroboration"] = len({s["kind"] for s in sights})
        person["url"] = next((s["url"] for s in sights if s["url"]), "")
        person["same_office"] = desk_match.location_match(
            row["location"], location)
        person.update(profile(db, row["id"]))
        out.append(person)
    return rank(out, role)


def rank(people: list[dict], role: str = "") -> list[dict]:
    """people_search's ranking, with what only the database supplies filled
    in: corroboration, same office, and the stored profile it reads for
    affinity and for a desk when there is no title."""
    from jobagent.people import people_search
    for p in people:
        p.setdefault("corroboration", 1)
    return people_search.rank(people, role)


def link(db: sqlite3.Connection, company: str, role: str,
         ranked: list[dict]) -> None:
    """Remember who was found for this application, and why they fit."""
    akey = app_identity.key(company, role)
    now = _today()
    for p in ranked:
        if not p.get("id"):
            continue
        db.execute(
            "INSERT OR REPLACE INTO matches (person_id, app_key, company, role,"
            " band, desk, why, computed) VALUES (?,?,?,?,?,?,?,?)",
            (p["id"], akey, company, role, p.get("band", ""),
             float(p.get("desk_score") or 0),
             "; ".join(p.get("desk_reasons") or []), now))
    db.commit()


def for_application(db: sqlite3.Connection, company: str, role: str,
                    location: str = "") -> list[dict]:
    """Rank everyone known at the firm for this role, and remember it."""
    ranked = at_company(db, company, role, location)
    link(db, company, role, ranked)
    return ranked


def stats(db: sqlite3.Connection) -> dict:
    one = lambda q: db.execute(q).fetchone()[0]
    return {
        "people": one("SELECT count(*) FROM people"),
        "firms": one("SELECT count(DISTINCT company_key) FROM people"),
        "with_title": one("SELECT count(*) FROM people WHERE title != ''"),
        "with_address": one("SELECT count(*) FROM people WHERE email != ''"),
        "published_address": one(
            "SELECT count(*) FROM people WHERE email_basis LIKE '%published%'"),
        "corroborated": one(
            "SELECT count(*) FROM (SELECT person_id FROM sightings GROUP BY "
            "person_id HAVING count(DISTINCT kind) > 1)"),
        "applications_linked": one("SELECT count(DISTINCT app_key) FROM matches"),
        "with_education": one("SELECT count(DISTINCT person_id) FROM education"),
        "with_history": one("SELECT count(DISTINCT person_id) FROM employment"),
        "with_exams": one("SELECT count(DISTINCT person_id) FROM facts "
                          "WHERE kind='exam'"),
        "firms_with_format": one("SELECT count(*) FROM firms WHERE pattern != ''"),
    }


# ---------------------------------------------------------------------------
# Keeping the keys in step with the alias table.

_CHILD_TABLES = ("sightings", "matches", "education", "employment", "facts")


def remap_company_keys(db: sqlite3.Connection) -> list[tuple]:
    """Re-key rows whose company_key predates an alias. Returns what moved.

    `record` keys every person with `company_id.key`, so a firm learned
    before its alias existed keeps the old key forever and reads as a second
    firm: "The WLV Corporation" sat under `theaes` with six of `wlv`'s
    sixteen people, the same six, scraped from the same kelvale.com pages.

    Adding the alias fixes what arrives next but not what is already stored,
    and this is the other half. It is the only safe way to do it, because a
    person may exist under both keys: moving the row would break
    UNIQUE(company_key, name_key), so a clash moves the *children* onto the
    surviving row and drops the duplicate instead of failing.
    """
    moves = []
    for key, company in db.execute(
            "SELECT DISTINCT company_key, company FROM people").fetchall():
        want = company_id.key(company) or key
        if want == key:
            continue
        for pid, nkey in db.execute(
                "SELECT id, name_key FROM people WHERE company_key=?",
                (key,)).fetchall():
            keep = db.execute(
                "SELECT id FROM people WHERE company_key=? AND name_key=?",
                (want, nkey)).fetchone()
            if keep and keep["id"] != pid:
                for table in _CHILD_TABLES:
                    db.execute(f"UPDATE OR IGNORE {table} SET person_id=? "
                               f"WHERE person_id=?", (keep["id"], pid))
                db.execute("DELETE FROM people WHERE id=?", (pid,))
                moves.append((key, want, nkey, "merged"))
            else:
                db.execute("UPDATE people SET company_key=? WHERE id=?",
                           (want, pid))
                moves.append((key, want, nkey, "moved"))
        row = db.execute("SELECT * FROM firms WHERE company_key=?",
                         (key,)).fetchone()
        if row:
            db.execute("DELETE FROM firms WHERE company_key=?", (key,))
            if not db.execute("SELECT 1 FROM firms WHERE company_key=?",
                              (want,)).fetchone():
                record_firm(db, company, website=row["website"],
                            domain=row["domain"], pattern=row["pattern"],
                            basis=row["pattern_basis"])
    db.commit()
    return moves


# ---------------------------------------------------------------------------
# Filling it from what earlier searches already wrote down.

def backfill(db: sqlite3.Connection, logs: str = "logs") -> dict:
    """Load every person the existing caches hold. Safe to re-run.

    The mailbox harvest is left out on purpose: it carries no date, so it
    cannot be held to the rule that nothing before 2026 is used, and most of
    it is newsletters and recruiters rather than anyone at a firm.
    """
    counts = {}

    site = _read_json(os.path.join(logs, "site_people.json"))
    n = 0
    for key, entry in site.items():
        result = entry.get("result") or {}
        people = result.get("people") or []
        company = result.get("company") or key
        if people and company:
            n += record(db, company, people, entry.get("fetched", ""))
        if company and (result.get("website") or result.get("mail_domain")):
            record_firm(db, company, website=result.get("website", ""),
                        domain=result.get("mail_domain", ""),
                        pattern=result.get("pattern", ""),
                        basis=result.get("pattern_evidence", ""))
    counts["site_people.json"] = n

    found = _read_json(os.path.join(logs, "contacts_found.json"))
    n = 0
    for company, people in found.items():
        if isinstance(people, list):
            n += record(db, company, [dict(p, source=p.get("source") or
                                           "past outreach research")
                                      for p in people])
    counts["contacts_found.json"] = n

    shared = _read_json(os.path.join(logs, "common_ground.json"))
    n = 0
    for entry in shared.values():
        company = entry.get("company") if isinstance(entry, dict) else ""
        people = (entry.get("people") or []) if isinstance(entry, dict) else []
        if company and people:
            n += record(db, company, [
                {"name": p.get("name"), "title": p.get("title"),
                 "location": p.get("location", ""),
                 "url": p.get("profile_url") or p.get("source", ""),
                 "source": "common ground research"} for p in people])
    counts["common_ground.json"] = n
    return counts


def _read_json(path: str) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}




# ---------------------------------------------------------------------------

def background(p: dict) -> str:
    """One line: degrees, past firms, licences, credentials, languages."""
    bits = []
    edu = [" ".join(x for x in (e.get("degree"), e.get("school")) if x)
           for e in p.get("education") or []]
    if edu:
        bits.append("; ".join(edu[:2]))
    past = [e["firm"] for e in p.get("employment") or []
            if not affinity.same_employer(e["firm"], p.get("company", ""))]
    if past:
        bits.append("before: " + ", ".join(dict.fromkeys(past[:3])))
    if p.get("exams"):
        bits.append("Series " + "/".join(x for x in p["exams"] if x.isdigit()))
    if p.get("credentials"):
        bits.append(", ".join(p["credentials"][:3]))
    if p.get("languages"):
        bits.append("speaks " + ", ".join(p["languages"]))
    if p.get("years_experience"):
        bits.append(f"{p['years_experience']}y experience")
    return " · ".join(bits)


def run(company: str = "", role: str = "", location: str = "",
        do_backfill: bool = False, show: int = 15) -> None:
    db = connect()
    if do_backfill:
        for source, n in backfill(db).items():
            print(f"  {source:28s} {n} new")
    if company:
        ranked = for_application(db, company, role, location) if role else \
            at_company(db, company, role, location)
        if not ranked:
            print(f"Nobody recorded at {company} yet. Run find-people first.")
        f = firm(db, company)
        if f.get("pattern"):
            print(f"  address format at {f['domain']}: {f['pattern']} "
                  f"({f['pattern_basis']})")
        for p in ranked[:show]:
            extra = []
            if p["corroboration"] > 1:
                extra.append(f"{p['corroboration']} sources")
            if p.get("same_office"):
                extra.append("same office")
            if p.get("desk_reasons"):
                extra.append(", ".join(p["desk_reasons"][:2]))
            if p.get("affinity_reasons"):
                extra.append(", ".join(p["affinity_reasons"]))
            print(f"  {p['band']:11s} {p['name'][:24]:24s} {p['title'][:36]:36s}"
                  f" {p['email'][:30]:30s} {'; '.join(extra)}")
            more = background(p)
            if more:
                print(f"  {'':11s} {more[:110]}")
        print()
    s = stats(db)
    print(f"{s['people']} people at {s['firms']} firms · {s['with_title']} with a "
          f"title · {s['with_address']} with an address "
          f"({s['published_address']} published) · {s['corroborated']} named by "
          f"2+ sources · {s['applications_linked']} applications linked")
    print(f"{s['with_education']} with education · {s['with_history']} with past "
          f"firms · {s['with_exams']} with FINRA licences · "
          f"{s['firms_with_format']} firms with a known address format")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--company", default="")
    ap.add_argument("--role", default="")
    ap.add_argument("--location", default="")
    ap.add_argument("--backfill", action="store_true")
    a = ap.parse_args()
    run(a.company, a.role, a.location, a.backfill)
