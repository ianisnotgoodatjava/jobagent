"""The Skills line, tailored to one posting -- from what you can show, only.

The resume's skills line was the same seven words on every application. A
credit-analyst posting that asks for Excel, financial modeling and
statistics got "Microsoft Office Suite, Java, Python, R, Tableau, Excel,
Applied Math": what it wanted was there, but sixth, or not named at all,
while two of your bullets are tagged "financial modeling" and one
"statistics". An ATS keyword screen and a recruiter's six-second read both
see the order and the words on the line, not what the bullets underneath
prove.

So per posting the line is rebuilt in three parts, and nothing else:

  1. what the posting asks for that you have, most-asked first;
  2. the rest of the skills you listed, in your own order;
  3. never a skill without evidence.

Evidence is something you wrote yourself: the skills you listed, a tag on
one of your bullets or projects, a phrase in one, or a certification. A
listed skill is never dropped -- only moved. A skill the posting wants and
you cannot show goes in the report as a gap, never on the page: an
invented line on a resume is the thing you cannot defend in the interview,
the same rule resume_pdf keeps for the bullets.

Rules over a model, because this runs every time a posting is pasted and
should be instant, free and the same twice. Pure logic: no files, no network.
"""

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Skill:
    key: str
    display: str
    patterns: tuple            # how a posting (or a listed skill) says it
    tags: tuple = ()           # bullet/project tags that prove it
    text: tuple = ()           # phrases in a bullet or project that prove it
    implied_by: tuple = ()     # listed skills that cover it ("Microsoft Office")
    case_sensitive: bool = False


# Finance-desk vocabulary. Order does not matter; detection is by pattern.
SKILLS = (
    # tools
    Skill("excel", "Excel",
          (r"\b(?:ms |microsoft )?excel\b(?!\s+(?:at|in|as|under|when|with)\b)",
           r"\bspreadsheets?\b"),
          tags=("excel",), implied_by=("office",)),
    Skill("vba", "VBA", (r"\bvba\b", r"\bexcel macros?\b"), tags=("vba",)),
    Skill("powerpoint", "PowerPoint", (r"\bpower ?point\b",),
          tags=("powerpoint", "presentation", "investor presentations"),
          implied_by=("office",)),
    Skill("office", "Microsoft Office",
          (r"\b(?:ms|microsoft) office\b", r"\boffice suite\b",
           r"\bmicrosoft 365\b")),
    Skill("python", "Python", (r"\bpython\b", r"\bpandas\b", r"\bnumpy\b"),
          tags=("python",)),
    Skill("r", "R", (r"(?<![A-Za-z0-9&/.'-])R(?![A-Za-z0-9&/'+-])",),
          tags=("r",), case_sensitive=True),
    Skill("sql", "SQL", (r"\bsql\b", r"\bpostgres\w*\b", r"\bmysql\b"),
          tags=("sql",)),
    Skill("java", "Java", (r"\bjava\b",), tags=("java",)),
    Skill("javascript", "JavaScript", (r"\bjavascript\b", r"\btypescript\b"),
          tags=("javascript",)),
    Skill("matlab", "MATLAB", (r"\bmatlab\b",), tags=("matlab",)),
    Skill("stata", "Stata", (r"\bstata\b",), tags=("stata",)),
    Skill("sas", "SAS", (r"\bsas\b",), tags=("sas",)),
    Skill("tableau", "Tableau", (r"\btableau\b",), tags=("tableau",)),
    Skill("power_bi", "Power BI", (r"\bpower ?bi\b",), tags=("power bi",)),
    Skill("bloomberg", "Bloomberg", (r"\bbloomberg\b",), tags=("bloomberg",)),
    Skill("factset", "FactSet", (r"\bfact ?set\b",), tags=("factset",)),
    Skill("capiq", "Capital IQ", (r"\bcap(?:ital)? ?iq\b",), tags=("capital iq",)),
    Skill("pitchbook", "PitchBook", (r"\bpitch ?book\b",), tags=("pitchbook",)),
    # methods
    Skill("financial_modeling", "Financial Modeling",
          (r"\bfinancial model(?:l)?(?:ing|s)?\b", r"\b(?:three|3)[- ]statement\b",
           r"\bbuild(?:ing)? (?:financial )?models\b"),
          tags=("financial modeling",)),
    Skill("dcf", "DCF Valuation",
          (r"\bdcf\b", r"\bdiscounted cash flows?\b"),
          text=(r"\bdcf\b", r"\bdiscounted cash flows?\b")),
    Skill("valuation", "Valuation", (r"\bvaluations?\b",), tags=("valuation",)),
    Skill("lbo", "LBO Modeling", (r"\blbos?\b", r"\bleveraged buyouts?\b"),
          text=(r"\blbos?\b", r"\bleveraged buyouts?\b")),
    Skill("accounting", "Accounting",
          (r"\baccounting\b", r"\bgaap\b", r"\bfinancial statements?\b"),
          tags=("accounting",)),
    Skill("statistics", "Statistics",
          (r"\bstatistic(?:s|al)\b", r"\bregressions?\b", r"\beconometric\w*\b"),
          tags=("statistics", "econometrics")),
    Skill("machine_learning", "Machine Learning",
          (r"\bmachine learning\b", r"\bml\b", r"\bpredictive model\w*\b"),
          tags=("machine learning", "predictive modeling")),
    Skill("time_series", "Time Series Analysis", (r"\btime[- ]series\b",),
          tags=("time series", "garch")),
    Skill("backtesting", "Backtesting", (r"\bback-?test\w*\b",),
          tags=("backtesting",)),
    Skill("data_analysis", "Data Analysis",
          (r"\bdata analy(?:sis|tics)\b", r"\banaly[sz]e (?:large |complex )?data",
           r"\bdata[- ]driven\b"),
          tags=("data analysis",)),
    Skill("derivatives", "Derivatives",
          (r"\bderivatives?\b", r"\boptions (?:pricing|trading|markets?)\b",
           r"\bswaps\b", r"\bfutures\b"),
          tags=("derivatives", "options")),
    Skill("credit_analysis", "Credit Analysis",
          (r"\bcredit (?:analysis|research|underwriting|risk|analyst)\b",),
          tags=("credit",)),
    Skill("risk_management", "Risk Management",
          (r"\brisk management\b", r"\brisk analy\w*\b", r"\bmarket risk\b"),
          tags=("risk",)),
    Skill("underwriting", "Underwriting", (r"\bunderwrit\w*\b",),
          tags=("underwriting",)),
    Skill("project_finance", "Project Finance", (r"\bproject finance\b",),
          tags=("project finance",)),
    Skill("applied_math", "Applied Math",
          (r"\bapplied math\w*\b", r"\bmathemat\w*\b",
           r"\bquantitative (?:skills|background|analysis|aptitude|ability)\b"),
          tags=("quantitative",)),
)
BY_KEY = {s.key: s for s in SKILLS}

# How much a mention counts, by where in the posting it is.
WEIGHTS = {"requirements": 3, "responsibilities": 2, "text": 1}
MAX_ADDED = 4


def _compiled(skill: Skill) -> list[re.Pattern]:
    flags = 0 if skill.case_sensitive else re.I
    return [re.compile(p, flags) for p in skill.patterns]


_PATTERNS = {s.key: _compiled(s) for s in SKILLS}


def mentions(text: str, skill: Skill) -> list[int]:
    """Start offsets of every mention of `skill` in `text`."""
    return sorted(m.start() for p in _PATTERNS[skill.key]
                  for m in p.finditer(text or ""))


def which(label: str) -> str:
    """The skill a listed label names ("Microsoft Office Suite" -> office),
    or "" for one this vocabulary does not know."""
    for skill in SKILLS:
        if mentions(label, skill):
            return skill.key
    return ""


# --- what the posting asks for ------------------------------------------------

def wanted(parsed: dict) -> list[tuple[str, int]]:
    """[(skill key, weight)], most wanted first.

    A mention under the requirements counts three times one in passing prose,
    and ties go to whichever the posting named first.
    """
    sections = {"requirements": "\n".join(parsed.get("requirements") or []),
                "responsibilities": "\n".join(parsed.get("responsibilities") or []),
                "text": parsed.get("text") or ""}
    scored = []
    for skill in SKILLS:
        weight = sum(WEIGHTS[name] * len(mentions(body, skill))
                     for name, body in sections.items())
        if weight:
            first = min((mentions(sections["text"], skill) or [10**9])[0],
                        10**9)
            scored.append((skill.key, weight, first))
    scored.sort(key=lambda t: (-t[1], t[2]))
    return [(key, weight) for key, weight, _ in scored]


# --- what you can show --------------------------------------------------------

@dataclass(frozen=True)
class Evidence:
    where: str      # "listed", "tag", "text", "certification", "implied"
    detail: str     # the bullet id, the certification, the listed label


def inventory(bank: dict) -> dict[str, Evidence]:
    """Every skill in the vocabulary you have shown, with the strongest proof."""
    profile = bank.get("profile", {})
    found: dict[str, Evidence] = {}

    def keep(key, evidence):
        found.setdefault(key, evidence)

    listed = {which(label): label for label in profile.get("skills", [])}
    for key, label in listed.items():
        if key:
            keep(key, Evidence("listed", label))
    for skill in SKILLS:
        for owner in skill.implied_by:
            if owner in listed:
                keep(skill.key, Evidence("implied", listed[owner]))
    for cert in profile.get("certifications", []):
        for skill in SKILLS:
            if mentions(cert, skill):
                keep(skill.key, Evidence("certification", cert))
    pieces = [(b.get("id", ""), b.get("tags", []), b.get("text", ""))
              for b in bank.get("bullets", [])]
    pieces += [(p.get("id", p.get("title", "")), p.get("tags", []),
                f"{p.get('title', '')} {p.get('text', '')}")
               for p in bank.get("projects", [])]
    for ident, tags, text in pieces:
        lowered = {t.lower() for t in tags}
        for skill in SKILLS:
            if lowered & set(skill.tags):
                keep(skill.key, Evidence("tag", ident))
            elif any(re.search(p, text, re.I) for p in skill.text):
                keep(skill.key, Evidence("text", ident))
    return found


# --- the line ------------------------------------------------------------------

@dataclass
class Tailored:
    skills: list[str]                       # the line, in order
    moved_up: list[str] = field(default_factory=list)
    added: list[tuple[str, Evidence]] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    original: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.skills != self.original


def tailor(bank: dict, parsed: dict, fits=None,
           max_added: int = MAX_ADDED) -> Tailored:
    """The skills line for this posting.

    `fits(skills) -> bool` says whether a candidate line still fits the page;
    added skills are dropped, least wanted first, until it does. A listed
    skill is never dropped to make room.
    """
    listed = list(bank.get("profile", {}).get("skills", []))
    have = inventory(bank)
    label_for = {which(label): label for label in listed if which(label)}

    line, added, gaps = [], [], []
    for key, _weight in wanted(parsed):
        if key not in have:
            gaps.append(BY_KEY[key].display)
            continue
        # A listed skill keeps your spelling; an implied one is redundant
        # beside the label that covers it unless the posting names it.
        label = label_for.get(key) or BY_KEY[key].display
        if label in line:
            continue
        if key not in label_for:
            if len(added) >= max_added:
                continue
            added.append((label, have[key]))
        line.append(label)
    for label in listed:
        if label not in line:
            line.append(label)

    if fits is not None:
        while added and not fits(line):
            label, _ = added.pop()
            line.remove(label)

    # Among your own skills only: an added skill at the front pushes every
    # listed one down a place, which is not the same as being demoted.
    yours = [s for s in line if s in listed]
    moved = [s for i, s in enumerate(yours) if i < listed.index(s)]
    return Tailored(skills=line, moved_up=moved, added=added, gaps=gaps,
                    original=listed)


def report(t: Tailored) -> list[str]:
    """What changed, in a few lines, for the terminal or the agent."""
    out = [f"Skills: {', '.join(t.skills)}"]
    if not t.changed:
        out.append("  (unchanged: the posting names none of these ahead of the rest)")
    if t.moved_up:
        out.append(f"  moved up, the posting asks for them: {', '.join(t.moved_up)}")
    for label, ev in t.added:
        source = {"tag": f"your bullet '{ev.detail}' is tagged with it",
                  "text": f"your bullet '{ev.detail}' says so",
                  "certification": f"certification: {ev.detail}",
                  "implied": f"covered by '{ev.detail}' on your list"}.get(
            ev.where, ev.detail)
        out.append(f"  added {label} — {source}")
    if t.gaps:
        out.append(f"  asked for, not on the page (no evidence): {', '.join(t.gaps)}")
    return out
