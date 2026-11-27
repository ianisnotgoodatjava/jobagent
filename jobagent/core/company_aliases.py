"""The names you have corrected a company *away from*, kept as aliases.

Renaming a company on the sheet broke the scanner's memory of the
application. `app_scanner` decides "already tracked?" from the names in the
Company column, so once row 697 said "Howden" instead of the ATS's
"hyperiongrp", the same confirmation email no longer matched anything and
the next automated pass added it again — twice in one day, as rows 701 and
702 ("hyperiongrp" and "Loves").

So every Company correction records the name it replaced. The scanner reads
those aliases alongside the sheet, and the mail an ATS keeps sending under
its own name still lands on the row it already created.

Keyed on the normalized spelling, since the raw name arrives from a sender
display name and varies in case and punctuation.
"""


from jobagent.core.company_match import normalize
from jobagent.core import storage

STORE = storage.location("company_aliases")

_cache: dict | None = None


def load() -> dict:
    global _cache
    if _cache is None:
        _cache = storage.read(STORE, {}) or {}
    return _cache


def record(raw: str, corrected: str) -> None:
    """Remembers that `raw` is this sheet's old name for `corrected`."""
    global _cache
    raw, corrected = (raw or "").strip(), (corrected or "").strip()
    key = normalize(raw)
    # Nothing to remember if the correction only changed spacing or case, and
    # an empty key would match every company with a blank name.
    if not key or not corrected or key == normalize(corrected):
        return
    _cache = storage.merge(STORE, {key: corrected}, indent=1, sort_keys=True)


def aliases_of(company: str) -> list[str]:
    """The old names that were corrected to `company`."""
    target = normalize(company)
    return [raw for raw, corrected in load().items()
            if normalize(corrected) == target]
