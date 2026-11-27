"""What a person shares with the sender: a school, an employer, a language.

Desk and seniority decide whether someone can speak for the job. Whether
they answer a stranger is mostly decided by something else: a cold note from
a fellow graduate of the sender's school, or from someone who also sat at
the same bank, is
read as a note from someone in their circle. So when two people are equally
placed for the desk, the one with something in common goes first.

The weights are small and additive to the desk score, not a separate tier:
a shared school never lifts a Managing Director over a VP (band is ranked
first), and on the same band it counts as much as one exact desk word.

Pure logic: the sender's profile is passed in.
"""

import re

from jobagent.core import bio_facts

SCHOOL_WEIGHT = 1.0
EMPLOYER_WEIGHT = 0.75
LANGUAGE_WEIGHT = 0.25

# Words that do not identify an employer; "Bank SinoPac" and "SinoPac
# Securities" are one group, "Bank of America" and "Bank SinoPac" are not.
_GENERIC = {
    "inc", "llc", "lp", "llp", "ltd", "co", "corp", "corporation", "company",
    "the", "and", "of", "group", "holdings", "securities", "international",
    "bank", "financial", "capital", "partners", "global", "usa", "us", "na",
    "america", "americas", "services", "management", "investments",
    "markets", "advisors", "asset", "plc", "sa", "ag", "limited",
}


def _firm_words(name: str) -> frozenset:
    words = re.findall(r"[a-z0-9]+", (name or "").lower().replace("&", " "))
    return frozenset(w for w in words if w not in _GENERIC and len(w) > 2)


def same_employer(a: str, b: str) -> bool:
    """"GOLDMAN, SACHS & CO." and "Goldman Sachs" are one employer.

    Every distinctive word of the shorter name has to be in the longer. Used
    only to rank, never to address mail, so a near miss costs an ordering.
    """
    wa, wb = _firm_words(a), _firm_words(b)
    if not wa or not wb:
        return False
    small, big = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return small <= big


def me_from_resume(bank: dict) -> dict:
    """{schools, employers, languages} from resume_bank.json's shape."""
    profile = bank.get("profile") or {}
    edu = profile.get("education") or {}
    edus = edu if isinstance(edu, list) else [edu]
    schools = [e.get("school", "") for e in edus if isinstance(e, dict)]
    employers = list(dict.fromkeys(
        e.get("company", "") for e in bank.get("experience") or []
        if isinstance(e, dict) and e.get("company")))
    languages = [re.sub(r"\s*\(.*\)", "", l).strip()
                 for l in profile.get("languages") or []]
    return {"schools": [s for s in schools if s], "employers": employers,
            "languages": [l for l in languages if l and l.lower() != "english"]}


def score(person: dict, me: dict) -> tuple[float, list[str]]:
    """(score, reasons) for what `person` has in common with `me`.

    `person` carries education [{school}], employment [{firm}] and
    languages, as people_db.at_company returns them.
    """
    if not me:
        return 0.0, []
    total, reasons = 0.0, []

    mine = {bio_facts.school_key(s) for s in me.get("schools") or []}
    for e in person.get("education") or []:
        if bio_facts.school_key(e.get("school", "")) in mine:
            total += SCHOOL_WEIGHT
            reasons.append(f"also studied at {e['school']}")
            break

    for firm in me.get("employers") or []:
        if any(same_employer(firm, e.get("firm", ""))
               for e in person.get("employment") or []):
            total += EMPLOYER_WEIGHT
            reasons.append(f"also worked at {firm}")
            break

    langs = {l.lower() for l in me.get("languages") or []}
    for lang in person.get("languages") or []:
        if lang.lower() in langs:
            total += LANGUAGE_WEIGHT
            reasons.append(f"speaks {lang}")
            break
    return total, reasons
