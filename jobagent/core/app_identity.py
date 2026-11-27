"""One answer to "is this the same application?".

Five places had their own version of this, and they disagreed. The one that
mattered: `corrections` and `auto_draft` keyed on the position verbatim, while
`job_finder` stripped the ATS reference first. So

    U.S. Bank | Investment Portfolio Analyst
    U.S. Bank | Investment Portfolio Analyst Job ID# 2025

were the same application to one module and two to the others. That is the
exact failure `corrections` exists to prevent: lock a status while the title
still carries the requisition, let `fix_positions` clean the title, and the
lock silently stops matching — so the next scan re-applies the status the user
had already corrected.

`legacy_key` reproduces the old spelling, because there are locks and drafted
markers on disk that were written with it. Lookups try the current key first
and fall back, so nothing saved before this existed is lost.
"""

from jobagent.core.company_match import _core, normalize


def key(company: str, position: str) -> str:
    """Identity that survives re-sorting, renumbering and ATS reference noise.

    The company half comes from company_id, so an application filed under an
    ATS tenant name -- "Normere's WorkWise", "grierock" -- keys the same as
    one filed under the employer. Keying it on _core instead is what let the
    same application be logged twice under two spellings.
    """
    from jobagent.core import company_id
    from jobagent.core.job_title import strip_req
    c = company_id.key(company or "")
    p = " ".join(normalize(strip_req(position or "")).split())
    return f"{c}|{p}"


def legacy_key(company: str, position: str) -> str:
    """The pre-consolidation spelling, for reading state written under it."""
    c = (_core(company or "").replace(" ", "")
         or normalize(company or "").replace(" ", ""))
    p = " ".join(normalize(position or "").split())
    return f"{c}|{p}"


def dated_key(company: str, position: str, applied: str) -> str:
    """Identity for one application when the same role was applied to twice.

    Company and position alone cannot separate a re-application from the
    original: Brincroft re-posted "Analyst, Financial Instruments & Technology" in
    November, the July row was already rejected, and the two share this
    identity exactly -- so a status lock recorded for the July application
    governed the live November one as well. The applied date is the only thing
    that tells them apart. Falls back to the undated key when the date is
    missing or unparseable, which is also what every lock written before this
    existed is keyed by.
    """
    from jobagent.core.date_utils import parse_date
    d = parse_date(applied or "")
    k = key(company, position)
    return f"{k}@{d.isoformat()}" if d else k


def row_key(row: dict) -> str:
    return key(row.get("Company", ""), row.get("Position", ""))


def lookup(store: dict, company: str, position: str):
    """store[key] if present, else the legacy key, else None."""
    k = key(company, position)
    if k in store:
        return store[k]
    return store.get(legacy_key(company, position))


def keys_for(company: str, position: str) -> tuple[str, str]:
    """Both spellings, for membership tests over a set of saved keys."""
    return key(company, position), legacy_key(company, position)
