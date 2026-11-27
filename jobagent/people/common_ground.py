"""After an application goes in: who at the firm shares something with you?

A shared school, language, sport or background turns a cold email into a
warm one, and the person most worth finding is the one nearest the role --
the same desk first, then the same function, then anyone at the firm. This
answers that once per application, right after it lands on the sheet.

What counts as "shared" is yours to say, in core/shared_traits: your resume's
schools, languages and employers, a `common_ground` list in profile.json, and
`--trait kind:term` for a one-off. Nothing about who you are is in this code.

Two routes, like the rest of sourcing:

  links     always, free. A LinkedIn people search and a web query, built
            for this company, role and set of traits, to open and browse
            yourself. LinkedIn is never fetched or automated.
  research  with a model (sources/llm.py). One web-search request per
            application that returns named people, each with the page that
            states what they share with you. shared_traits checks every one,
            and a guess -- from a name, from a market they cover -- is
            refused with the reason, not reported.

Read-only as far as anyone else is concerned: nothing is sent and the sheet
is not touched. Findings go to the data directory, outside the repository,
so the names of real people never reach it.
"""

import datetime
import os
import re

from jobagent.core import affinity
from jobagent.core import app_identity
from jobagent.core import company_id
from jobagent.core import sender_profile
from jobagent.core import shared_traits as traits_mod
from jobagent.core import storage
from jobagent.core.date_utils import parse_date
from jobagent.people import people_search
from jobagent.sources import llm

STORE_FILE = storage.location("common_ground")
RAW_DIR = storage.directory("common_ground_raw")

# The same firm, checked for another role this recently under the same
# traits, is not re-billed by the scheduled sweep: who works there has not
# changed, only how near the role they sit. Asking by hand still researches.
REUSE_DAYS = 30

_KIND_WORDS = {
    traits_mod.SCHOOL: "studied at",
    traits_mod.LANGUAGE: "speaks",
    traits_mod.HERITAGE: "is from, or describes their background as",
    traits_mod.GROUP: "is a member of",
    traits_mod.SPORT: "plays or played",
    traits_mod.HOBBY: "lists as an interest",
    traits_mod.EMPLOYER: "worked at",
    traits_mod.PLACE: "lives in or grew up in",
}

SYSTEM = """You find people at one company who publicly share something with a job applicant, so the applicant can network with them about a role they just applied to.

WHAT COUNTS -- the person states one of these about themselves, on a page you can cite:
{TRAITS}

WHAT NEVER COUNTS -- never report anyone on these grounds:
- their name or photo. Never guess anyone's background, nationality, ethnicity or religion from a name or a face; names are shared across many countries.
- a market, region or sector they cover professionally (an analyst on a country's equities is not from that country).
- a namesake: a different school, group or company with a similar name.
- something you assume rather than read.

WHO TO LOOK FOR
People who work at the company NOW -- not alumni of it -- nearest the role first: the same team or desk, then the same function, then anyone at the firm. Any seniority; a peer is as useful as a director. Look at public profiles surfaced by web search, the firm's own pages, alumni, club and team pages, conference bios and press releases.

NEVER invent a person or a connection. If you are not sure they work there now, or the connection is not stated on a page you can cite, leave them out. Finding nobody is a fine answer; a made-up person is not.

OUTPUT -- exactly this, nothing else. One block per person, at most 6, nearest the role first:

=== PERSON ===
NAME: <full name>
TITLE: <current title at the company>
TEAM: <team, desk or division, or UNKNOWN>
LOCATION: <city, or UNKNOWN>
PROFILE_URL: <public profile URL, or UNKNOWN>
TRAIT: <which item from the list above>
EVIDENCE: <the connection in the source's own words, e.g. "B.A., Example University" or "Captain, varsity rowing">
SOURCE: <URL of the page that states it>
RELEVANCE: <same team | same function | elsewhere at the firm>
WHY_RELEVANT: <one line: how their work relates to the role>

If you find nobody, output exactly:
=== NONE ===
<one line on where you looked>"""

_NONE_RE = re.compile(r"^\s*===\s*NONE\s*===\s*$(.*)", re.M | re.S)

# A wenworth agency is not the employer: its people are recruiters, not the
# desk. Same rule auto_draft applies before spending research on a row.
_AGENCY_RE = re.compile(
    r"staffing|recruit|talent partners|search partners|\bagency\b", re.I)


# --- traits --------------------------------------------------------------------

def my_traits(extra: list[str] | None = None,
              only_extra: bool = False) -> list[dict]:
    """The traits to look for: profile.json, the resume, and any --trait."""
    me = {}
    if not only_extra:
        try:
            from jobagent.core import resume_bank
            me = affinity.me_from_resume(resume_bank.load())
        except Exception:
            me = {}
    return traits_mod.from_profile(sender_profile.load(), me, extra,
                                   only_extra=only_extra)


def describe(traits: list[dict]) -> str:
    """The trait list as the model reads it."""
    lines = []
    for t in sorted(traits, key=lambda t: traits_mod.KINDS.index(t["kind"])):
        also = [x for x in t["terms"] if x.lower() != t["label"].lower()]
        lines.append(f"- {t['kind']}: {_KIND_WORDS[t['kind']]} {t['label']}"
                     + (f" (also written: {', '.join(also[:8])})" if also else ""))
    return "\n".join(lines)


# --- the store -----------------------------------------------------------------

def _load() -> dict:
    return storage.read(STORE_FILE, {}) or {}


def _save(store: dict) -> None:
    storage.merge(STORE_FILE, store, indent=1, ensure_ascii=False)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "x"


# --- deciding ------------------------------------------------------------------

def _desk_score(person: dict, role: str) -> int:
    """Desk words shared between their title/team and the role applied to."""
    want = set(traits_mod.role_terms(role, limit=99))
    have = set(traits_mod.role_terms(
        f"{person.get('title', '')} {person.get('team', '')}", limit=99))
    return len(want & have)


def screen(people: list[dict], role: str, traits: list[dict]
           ) -> tuple[list[dict], list[dict]]:
    """(report these, refused with a reason), the first ranked.

    Pure: takes what the model returned and decides, so it can be tested
    without a model or a network.
    """
    kept, refused, seen = [], [], set()
    for person in people:
        why = traits_mod.check(person, traits)
        key = (person.get("name") or "").strip().lower()
        if not why and key in seen:
            why = "listed twice"
        if why:
            refused.append({**person, "refused": why})
            continue
        seen.add(key)
        hit = traits_mod.match(person["evidence"], traits)
        kept.append({**person, "kind": hit["kind"], "trait": hit["label"],
                     "band": people_search.band(person.get("title", ""))})
    kept.sort(key=lambda p: traits_mod.sort_key(p, _desk_score(p, role)))
    return kept, refused


def research(company: str, role: str, traits: list[dict], location: str = "",
             skills: str = "") -> dict:
    """One paid web-search request, checked. Never raises for a bad answer."""
    system = SYSTEM.replace("{TRAITS}", describe(traits))
    prompt = (f"Company: {company}\n"
              f"Role applied to: {role or 'unknown'}\n"
              f"Location of the role: {location or 'unknown'}\n"
              + (f"What the role involves: {skills[:500]}\n" if skills else "")
              + "\nFind people currently at this company who state one of "
                "the listed things about themselves, nearest this role first.")
    try:
        text = llm.respond(system, prompt, web_search=True, effort="medium")
    except RuntimeError as exc:
        return {"people": [], "refused": [], "error": str(exc)}

    kept, refused = screen(traits_mod.parse(text), role, traits)
    result = {"people": kept, "refused": refused}
    none = _NONE_RE.search(text)
    if none and not kept:
        result["note"] = none.group(1).strip()[:200]

    os.makedirs(RAW_DIR, exist_ok=True)
    raw = os.path.join(RAW_DIR, f"{datetime.date.today():%Y%m%d}_"
                                f"{_slug(company)}_{_slug(role)}.txt")
    with open(raw, "w", encoding="utf-8") as f:
        f.write(text)
    result["raw"] = raw
    return result


def links(company: str, role: str, traits: list[dict]) -> dict:
    query = traits_mod.xray_query(company, role, traits)
    return {"linkedin": traits_mod.linkedin_urls(company, role, traits),
            "web": traits_mod.google_url(query), "query": query}


def for_application(company: str, role: str, traits: list[dict],
                    location: str = "", skills: str = "", redo: bool = False,
                    use_model: bool = True) -> dict:
    """The answer for one application: cached, freshly researched, or links.

    A cached answer is reused only if it was searched under the same traits;
    adding a trait is a reason to look again.
    """
    key = app_identity.key(company, role)
    sig = traits_mod.signature(traits)
    store = _load()
    cached = store.get(key)
    if cached and cached.get("traits") == sig and not redo:
        return {**cached, "cached": True, **links(company, role, traits)}

    result = {"company": company, "role": role, **links(company, role, traits)}
    if not (use_model and llm.available()):
        result["skipped"] = ("no model configured" if use_model
                             else "links only")
        return result

    found = research(company, role, traits, location, skills)
    if found.get("error"):
        # Not stored: a failed request is retried next time, not remembered.
        return {**result, **found}
    record = {"company": company, "role": role,
              "company_key": company_id.key(company), "traits": sig,
              "checked": f"{datetime.date.today():%Y-%m-%d}",
              **{k: found[k] for k in ("people", "refused", "note", "raw")
                 if k in found}}
    store[key] = record
    _save(store)
    return {**result, **record}


# --- showing -------------------------------------------------------------------

def show(result: dict, verbose: bool = True) -> None:
    company, role = result.get("company", ""), result.get("role", "")
    print(f"{company}" + (f"  —  {role}" if role else ""))
    if result.get("cached"):
        print(f"  (checked {result.get('checked')}; --redo to research again)")

    people = result.get("people") or []
    if people:
        print(f"\n  Common ground ({len(people)}), nearest the role first:")
        for p in people:
            print(f"\n  · {p['name']} — {p.get('title') or 'title unknown'}"
                  + (f", {p['team']}" if p.get("team") else ""))
            print(f"      {p['relevance']}; {p['trait']} ({p['kind']}): "
                  f"{p['evidence'][:80]}")
            if p.get("why_relevant"):
                print(f"      {p['why_relevant'][:100]}")
            print(f"      source: {p['source']}")
            if p.get("profile_url") and p["profile_url"] != p["source"]:
                print(f"      profile: {p['profile_url']}")
    elif "checked" in result:
        print("\n  Nobody found who states one of your traits"
              + (f" — {result['note']}" if result.get("note") else "."))
    elif result.get("error"):
        print(f"\n  research failed: {result['error'][:120]}")
    elif result.get("skipped"):
        print(f"\n  research skipped ({result['skipped']}) — the searches "
              f"below are free.")

    refused = result.get("refused") or []
    if refused:
        reasons = {}
        for p in refused:
            reasons[p["refused"]] = reasons.get(p["refused"], 0) + 1
        print(f"\n  refused {len(refused)}: " + "; ".join(
            f"{n}× {why}" for why, n in sorted(reasons.items(),
                                               key=lambda x: -x[1])))

    if verbose or not people:
        print("\n  Browse it yourself:")
        for label, url in result.get("linkedin", []):
            print(f"    LinkedIn, {label}:\n      {url}")
        print(f"    Web:\n      {result.get('web', '')}")
    print()


def run(company: str = "", role: str = "", row: int | None = None,
        links_only: bool = False, redo: bool = False,
        extra: list[str] | None = None, only_extra: bool = False) -> None:
    """One application, by sheet row or by name."""
    traits = my_traits(extra, only_extra)
    if not traits:
        raise SystemExit(
            "No traits to look for. Add a common_ground list to profile.json "
            "(see profile.example.json), or pass --trait kind:term, e.g. "
            "--trait sport:rowing --trait language:Portuguese.")
    location = skills = ""
    if row:
        from jobagent.store import sheets
        r = sheets.get_row(row)
        company = (r.get("Company") or "").strip()
        role = (r.get("Position") or "").strip()
        location = r.get("Location") or r.get("location") or ""
        skills = r.get("Required Skills & Experience") or ""
    if not company:
        raise SystemExit("Give --row, or --company (and ideally --role).")
    print("Looking for: " + "; ".join(f"{t['label']} ({t['kind']})"
                                      for t in traits) + "\n")
    show(for_application(company, role, traits, location, skills, redo=redo,
                         use_model=not links_only))


# --- after applying ------------------------------------------------------------

def pending(rows: list[dict], traits: list[dict], days_back: int = 14,
            limit: int = 5, store: dict | None = None,
            today: datetime.date | None = None) -> list[dict]:
    """Recent open applications not yet checked under these traits, one per firm.

    Pure: the sweep's choice, separated so it can be tested on plain rows.
    """
    store = _load() if store is None else store
    sig = traits_mod.signature(traits)
    today = today or datetime.date.today()
    cutoff = today - datetime.timedelta(days=days_back)
    reuse_cutoff = f"{today - datetime.timedelta(days=REUSE_DAYS):%Y-%m-%d}"
    recent_firms = {v.get("company_key") for v in store.values()
                    if v.get("traits") == sig
                    and (v.get("checked") or "") >= reuse_cutoff}

    def applied(r):
        return parse_date(r.get("Applied Draes") or r.get("Applied Date")
                          or "") or datetime.date.min

    out, seen = [], set()
    for r in sorted(rows, key=applied, reverse=True):
        company = (r.get("Company") or "").strip()
        if not company or applied(r) < cutoff:
            continue
        if "reject" in (r.get("Status") or "").lower():
            continue
        if _AGENCY_RE.search(company):
            continue
        firm = company_id.key(company)
        if firm in seen or firm in recent_firms:
            continue
        done = [store[k] for k in app_identity.keys_for(
            company, r.get("Position", "")) if k in store]
        if any(d.get("traits") == sig for d in done):
            continue
        seen.add(firm)
        out.append(r)
        if len(out) >= limit:
            break
    return out


def sweep(days_back: int = 14, limit: int = 5, dry_run: bool = False) -> None:
    """The scheduled half: check each new application once per trait set."""
    traits = my_traits()
    if not traits:
        print("  no traits configured — add common_ground to profile.json")
        return
    from jobagent.store import sheets
    todo = pending(sheets.list_rows(), traits, days_back=days_back, limit=limit)
    if not todo:
        print("  nothing new to check")
        return
    if dry_run or not llm.available():
        print(f"  {len(todo)} new application(s) to check"
              + ("" if llm.available() else
                 " — no model configured, so only the free searches:"))
        for r in todo:
            print(f"    row {r.get('row_number', '?'):>4}  {r['Company'][:30]:30s}"
                  f" {(r.get('Position') or '')[:40]}")
        print("  searches for one: python cli.py common-ground --row N "
              "--links-only")
        return
    for r in todo:
        company, role = r["Company"].strip(), (r.get("Position") or "").strip()
        result = for_application(
            company, role, traits, r.get("Location") or r.get("location") or "",
            r.get("Required Skills & Experience") or "")
        people = result.get("people") or []
        line = (f"{len(people)} found" if "checked" in result
                else result.get("error", "failed")[:60])
        print(f"  {company[:30]:30s} {line}")
        for p in people[:3]:
            print(f"      {p['name'][:26]:26s} {p['relevance']:14s} "
                  f"{p['trait']}: {p['evidence'][:36]}")
    print("  details: python cli.py common-ground --row N")
