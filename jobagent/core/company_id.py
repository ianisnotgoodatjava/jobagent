"""One answer to "are these the same employer".

Three functions used to decide this and they disagreed. `normalize` kept
every word, `_core` stripped suffixes, `slug` removed spaces, and callers
picked whichever was nearest. The results were not merely inconsistent, they
were wrong in both directions at once:

    _core("Calstead Global")       -> "sp"              two characters
    normalize("Norworth Group")  -> "norworth"           never equals "norworth ratings"
    "Normere's WorkWise"        -> a second Normere's row on the sheet

and the prefix matching built on top of those keys sent an email intended for
Thorhaven to alphacapital.online, a different firm that merely starts alike.

The lesson from the sheet's own 492 spellings is that no rule separates these:

    Gargate            / Gargate Fenworth            one company
    Corvale Investments / Corvale Elmmoor            two companies
    Lynhaven         / Lynhaven Clean Technology   one company
    Darhaven Harshaw Banking / Darhaven Harshaw Trust  two companies
    Dunford         / Dunford Rosdale & Selfield   one company
    Bank of Quengate  / Bank of New Pelridge Darcroft     two companies

Every pair shares a leading word. Half are the same employer and half are
not, and nothing in the text says which. So this module does not guess.

    key()   is exact: punctuation and legal suffixes removed, nothing else.
            Two names match only if they reduce to the same key.
    ALIASES carries the cases a rule cannot reach, written down once.
    same()  is key equality after alias resolution. There is no prefix rule,
            no edit distance, no "starts with". Those are what caused the
            bugs above, and a missed duplicate is cheap next to an email
            delivered to the wrong company.

`propose_aliases()` reads the sheet and suggests additions. It only ever
suggests: a human decides, because the sheet is exactly where the
undecidable pairs live.
"""

import re
import unicodedata

# Words that are decoration on a legal name, not part of the identity. Kept
# deliberately short: every entry here is a chance to merge two real
# companies, and "Group", "Trust" and "Partners" are load-bearing in finance
# ("Merholm Group" is not "Merholm Calhaven Advisors").
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "plc",
    "corp", "corporation", "co", "company", "sa", "nv", "ag", "gmbh", "pte",
}

# Variant spelling -> the name it is really the same employer as. Each line is
# a judgement that no rule could make, and is here because it was seen on the
# sheet. Keys are compared after key(), so case and punctuation do not matter.
ALIASES = {
    # The ATS or Workday tenant name leaking in as the employer.
    "moodysworkwise": "moodys",
    "msigusa": "msig usa",
    "mufgubhr": "mufg",
    "bancofcal": "banc of california",
    # Same employer, longer legal or divisional name.
    "allstateinsurance": "allstate",
    "fanaticsbetting&gaming": "fanatics",
    "palmettocleantechnology": "palmetto",
    "scalemicrogrids": "scale",
    "tomomortgage": "tomo",
    "constellationenergy": "constellation",
    "redwoodtrustteamaspire": "redwood trust",
    "theaes": "aes",
    "themathergroup": "mather group",
    "thesegalgroup": "segal group",
    "jlllasalle": "jll",
    "fitchratings": "fitch",
    "fitchgroup": "fitch",
    "spglobal": "s&p global",
    "bmocapitalmarkets": "bmo",
    # Found by propose_aliases over the sheet, then judged one at a time.
    # Each matters because the two spellings carry different statuses, so
    # treating them as separate firms lets a closed one speak for an open one
    # -- "Merley Bank" was interview-rejected while "Merley" is
    # interviewing, and the drafts for the live role were queued for deletion.
    "flagstarbank": "flagstar",
    "otcflowus": "otc flow",
    "virtusinvestmentpartners": "virtus",
}

# Pairs propose_aliases offers that are deliberately NOT merged:
#   XTK QNV Investment Management / XTK QNV -- mail from the asset manager
#     should not change a row at the operating company, which is a different
#     employer with a different desk.
#   Rosmere Investments / Rosmere -- "Rosmere" alone is too generic to claim.
#   First National Bank / F.N.B. Corporation -- two different banks that
#     share a generic name; "First National Bank" names dozens of unrelated
#     banks, so a shared word is not evidence of a shared employer.
#   Pencroft Penley / Fenridge -- Fenridge owns it, but Pencroft Penley & Co.
#     still trades under its own name with its own desks at pencroft.com, so
#     an equity research analyst there is not reachable as Fenridge.

_PUNCT_RE = re.compile(r"[^\w\s&]+")
_SPACE_RE = re.compile(r"\s+")

# Shorter than this and a key is not an identity, it is a collision waiting to
# happen: _core turned "Calstead Global" into "sp".
MIN_KEY_LENGTH = 3


def _fold(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _PUNCT_RE.sub(" ", text.lower())
    return _SPACE_RE.sub(" ", text).strip()


def _learned_alias(spaced: str, folded: str) -> str:
    """What `company_aliases` has recorded, if anything.

    That store is written whenever a company is corrected on the sheet, so it
    holds the renames this mailbox has actually produced -- "grierock" to
    Cormore, "lavine" to QLC. It is keyed with spaces where this module folds
    them out, so both spellings are tried.
    """
    try:
        from jobagent.core import company_aliases
        learned = company_aliases.load()
    except Exception:
        return ""
    for candidate in (spaced, folded):
        hit = learned.get(candidate)
        if hit:
            return hit
    return ""


def key(name: str) -> str:
    """The identity of an employer name. Exact, never fuzzy.

    "Lorbrook Insurance Company" and "Lorbrook" give different keys on
    purpose — if they are the same employer, an alias says so. Two alias
    sources feed in: the table below, and the renames `company_aliases` has
    learned from corrections made on the sheet.
    """
    words = [w for w in _fold(name).split() if w not in _LEGAL_SUFFIXES]
    if not words:
        return ""
    folded = "".join(words)
    alias = ALIASES.get(folded) or _learned_alias(" ".join(words), folded)
    if alias and _fold(alias) != _fold(name):
        return key(alias)
    return folded


def same(a: str, b: str) -> bool:
    """Whether two spellings name the same employer.

    No length floor here. This comparison is exact, so a short key is only a
    risk to someone matching loosely on it -- that is what is_reliable() is
    for. Applying the floor here meant same("BP", "BP") was False and the
    company could never match itself.
    """
    ka = key(a)
    return bool(ka) and ka == key(b)


def is_reliable(name: str) -> bool:
    """False when a key is too short to identify anything on its own.

    "KNV" and "XRV" are real companies with three-letter names. The caller
    still needs to know that matching on such a key alone is weak evidence,
    which is the mistake the domain lookup made.
    """
    return len(key(name)) >= MIN_KEY_LENGTH + 1


def propose_aliases(names) -> list[tuple[str, str]]:
    """(variant, canonical) pairs a human might want to add to ALIASES.

    Suggestion only. The pairs this finds are precisely the undecidable ones
    -- one name extending another -- and roughly half of them on this sheet
    are different companies.
    """
    keys = {}
    for n in names:
        k = key(n)
        if k:
            keys.setdefault(k, n)
    out = []
    for k, name in sorted(keys.items()):
        for other_k, other in keys.items():
            if other_k == k or len(other_k) < MIN_KEY_LENGTH + 1:
                continue
            if k.startswith(other_k) and k != other_k and k not in ALIASES:
                out.append((name, other))
    return out


def _check_aliases() -> list[str]:
    """Alias keys that key() can never produce, so the entry does nothing.

    "dunfordrosdaleselfield" was such an entry: folding keeps "&", so the real
    key is "fanaticsbetting&gaming" and the alias sat there looking correct
    while matching nothing. A wrong alias is invisible -- it fails by not
    firing -- so it is checked rather than trusted.
    """
    bad = []
    for variant in ALIASES:
        words = [w for w in _fold(variant).split() if w not in _LEGAL_SUFFIXES]
        if "".join(words) != variant:
            bad.append(variant)
    return bad
