"""How near a published title sits to the job applied to.

people_search ranked desk match by counting the words a title and a role
share. That misses most real matches, because a desk is named differently by
the posting and by the person sitting on it:

    role   "Structured Finance Analyst, CMBS Surveillance"
    title  "Vice President, Securitization"            shared words: 0

and it rewards coincidences, because one shared word is one shared word
whether it is "securitization" or "markets".

This scores in two parts. A word in both counts, after a light stem so
"underwriter" meets "underwriting". A word that belongs to the same desk
family as one in the role counts too, at a discount: same desk, different
name. Families are written down rather than learned because the list of
desks applied to is short and the cost of a wrong family is a wrong email.

Pure logic: no network, no files.
"""

import re

# Words that appear in every other title and prove nothing about the desk.
# Kept in step with people_search's list, plus a few the family match made
# visible ("markets" alone joins capital-markets and equity-markets people).
STOP = {
    "analyst", "associate", "senior", "junior", "the", "and", "of", "for",
    "vice", "president", "director", "manager", "global", "group", "team",
    "internal", "external", "head", "lead", "officer", "chief", "consultant",
    "specialist", "professional", "executive", "business", "corporate",
    "general", "regional", "americas", "markets", "market", "services",
    "management", "managing", "assistant", "intern", "summer", "program",
    "level", "entry", "full", "time", "new", "york", "remote", "hybrid",
    "with", "from", "into", "role", "position", "staff", "principal",
    "partner", "member", "office", "division", "department", "desk",
}

# Same desk, different words. Each family is one kind of work; a word may sit
# in more than one (risk is credit risk and market risk).
FAMILIES = {
    "structured": {"structured", "securitization", "securitized", "abs",
                   "cmbs", "rmbs", "clo", "clos", "mbs", "conduit",
                   "surveillance", "warehouse", "esoteric", "cdo"},
    "credit": {"credit", "lending", "loan", "loans", "leveraged", "debt",
               "underwriting", "ratings", "rating", "counterparty",
               "distressed", "restructuring", "covenant"},
    "fixed_income": {"fixed", "income", "bond", "bonds", "rates", "treasury",
                     "municipal", "muni", "munis", "sovereign", "govies",
                     "duration", "curve"},
    "equity": {"equity", "equities", "stock", "stocks", "ecm", "ipo",
               "convertible", "convertibles"},
    "derivatives": {"derivative", "derivatives", "options", "swaps", "swap",
                    "futures", "volatility", "exotics", "otc"},
    "fx": {"fx", "currency", "currencies", "foreign", "exchange", "forex"},
    "commodities": {"commodity", "commodities", "energy", "power", "gas",
                    "oil", "metals", "carbon", "environmental", "capacity",
                    "emissions", "renewable", "renewables", "solar", "storage"},
    "trading": {"trading", "trader", "traders", "sales", "execution",
                "flow", "market-making", "electronic", "brokerage"},
    "research": {"research", "strategist", "strategy", "economist",
                 "economics", "macro", "insights", "analytics"},
    "quant": {"quantitative", "quant", "modeling", "modelling", "model",
              "models", "data", "statistical", "machine", "learning"},
    "risk": {"risk", "var", "stress", "validation", "credit", "market",
             "liquidity", "counterparty", "operational"},
    "banking": {"banking", "m&a", "mergers", "acquisitions", "advisory",
                "coverage", "origination", "dcm", "ecm", "leveraged",
                "sponsors", "fig"},
    "investments": {"portfolio", "investment", "investments", "fund", "funds",
                    "allocation", "manager", "asset", "wealth", "pm",
                    "private", "alternatives"},
    "project_finance": {"project", "infrastructure", "tax", "equity",
                        "lihtc", "renewable", "development", "developer"},
    "insurance": {"insurance", "reinsurance", "actuarial", "actuary",
                  "underwriting", "claims", "catastrophe", "specialty"},
    "accounting": {"accounting", "audit", "controller", "reporting",
                   "valuation", "valuations", "tax", "finance", "fp&a"},
}

# Discount for a family match against an exact one. A title saying the
# role's own word is better evidence than a title on the same desk.
FAMILY_WEIGHT = 0.5

_SUFFIXES = ("izations", "ization", "ations", "ation", "ings", "ing",
             "ers", "er", "ies", "es", "s")


def stem(word: str) -> str:
    """A light stem: "underwriter" and "underwriting" both give "underwrit"."""
    w = word.lower()
    for suffix in _SUFFIXES:
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            return w[: -len(suffix)]
    return w


def words(text: str) -> set[str]:
    """The meaningful words of a title or a role."""
    raw = re.findall(r"[a-z][a-z&\-]*[a-z]|[a-z]{2,}", (text or "").lower())
    out = set()
    for w in raw:
        if w in STOP:
            continue
        # Acronyms like ABS, CLO and FX are short and are the desk.
        if len(w) <= 3 and not any(w in fam for fam in FAMILIES.values()):
            continue
        out.add(w)
    return out


def families(ws: set[str]) -> set[str]:
    return {name for name, fam in FAMILIES.items() if ws & fam}


def score(title: str, role: str) -> tuple[float, list[str]]:
    """(score, reasons). 0 when nothing ties the title to the role.

    One point per word in both (stemmed), FAMILY_WEIGHT per desk family the
    two share that the exact words did not already account for.
    """
    want, have = words(role), words(title)
    if not want or not have:
        return 0.0, []
    exact = {stem(w) for w in want} & {stem(w) for w in have}
    # Report a word as the role spells it, not its stem.
    reasons = [f"both say '{min(w for w in want if stem(w) == st)}'"
               for st in sorted(exact)]
    total = float(len(exact))
    # A family counts once, and only when the shared exact words have not
    # already put both sides in it.
    exact_words = {w for w in want | have if stem(w) in exact}
    covered = families(exact_words)
    for name in sorted((families(want) & families(have)) - covered):
        total += FAMILY_WEIGHT
        reasons.append(f"same desk ({name.replace('_', ' ')})")
    return total, reasons


def location_match(person_location: str, job_location: str) -> bool:
    """Whether a person's city is the job's city — the same office."""
    a = re.sub(r"[^a-z ]", " ", (person_location or "").lower()).split()
    b = re.sub(r"[^a-z ]", " ", (job_location or "").lower()).split()
    if not a or not b:
        return False
    city_a, city_b = " ".join(a[:2]), " ".join(b[:2])
    return city_a == city_b or a[0] == b[0] and len(a[0]) > 3
