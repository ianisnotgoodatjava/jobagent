"""What a published bio says about someone, beyond their name and title.

A firm's bio page is a paragraph: "Prior to joining Stoford, Jane was a Vice
President at Normere's. She holds a B.S. in Finance from New York University
and is a CFA charterholder." Kept as a title alone, that paragraph throws
away the three facts that decide whether a cold email lands: the desk she
came from, the school she shares or does not share with the sender, and how
long she has been doing this.

This reads those facts out with fixed patterns. Bios are written to a house
style, so a short list of phrasings covers most of them, and a phrase that
is not recognised costs a missing fact rather than a wrong one: every
pattern needs a degree word or a school word, an employer needs a hiring
verb in front of it ("prior to joining", "began her career at"), and nothing
is guessed from a lone capitalised word.

FINRA's exam codes are read here too, because they are the nearest thing
the registry has to a desk: a Series 79 is an investment banker, a 57 a
trader, an 86/87 a research analyst.

Pure logic: no network, no files.
"""

import re

# ---------------------------------------------------------------------------
# Schools

_SCHOOL_WORD = r"(?:University|College|Institute|School|Polytechnic|Academy|Conservatory)"
_CAP = r"(?:[A-Z][A-Za-z.'\-]+|[A-Z]{2,})"
# No "at": "International Finance at City University" is a field and a school.
_JOIN = r"(?:\s+(?:of|and|for|the|&|in|de)\s+|\s+)"
# A campus city after a comma; never another school word ("University of
# Chicago, Graduate School of Business" is not in Graduate School).
_CITY = (rf"(?:,\s+(?!(?:[A-Z][a-z]+\s+)?{_SCHOOL_WORD}\b)[A-Z][a-z]+"
         rf"(?:\s+[A-Z][a-z]+)?(?=[\s.,;)]|$))?")
# "New York University", "University of California, Halstead",
# "the Wharton School of the University of Pennsylvania".
_SCHOOL = re.compile(
    rf"College of William (?:and|&) Mary|"
    rf"(?:{_CAP}(?:{_JOIN}{_CAP})*\s+)?{_SCHOOL_WORD}(?:\s+(?:of|for|at)\s+(?:the\s+)?{_CAP}(?:\s+{_CAP})*)*"
    + _CITY)

# Schools written without a school word. Only taken in a sentence that is
# already about a degree, so "Columbia" the river or "Duke" the title never
# arrive here.
_BARE_SCHOOLS = {
    "nyu": "New York University", "mit": "Massachusetts Institute of Technology",
    "ucla": "University of California, Los Angeles", "usc": "University of Southern California",
    "lse": "London School of Economics", "cuny": "City University of New York",
    "suny": "State University of New York", "wharton": "Wharton School",
    "harvard": "Harvard University", "yale": "Yale University",
    "princeton": "Princeton University", "columbia": "Columbia University",
    "cornell": "Cornell University", "stanford": "Stanford University",
    "dartmouth": "Dartmouth College", "duke": "Duke University",
    "georgetown": "Georgetown University", "fordham": "Fordham University",
    "baruch": "Baruch College", "insead": "INSEAD", "berkeley": "University of California, Berkeley",
    "upenn": "University of Pennsylvania", "penn": "University of Pennsylvania",
    "nyu stern": "New York University", "stern": "New York University",
}

# Different names for one school, so a match is a match. The key is what
# school_key returns; the values are substrings that identify it.
_SCHOOL_ALIASES = {
    "new york university": ("new york university", "nyu", "stern school",
                            "courant institute", "tandon", "gallatin"),
    "university of pennsylvania": ("university of pennsylvania", "wharton", "upenn"),
    "columbia university": ("columbia university", "columbia business school"),
    "massachusetts institute of technology": ("massachusetts institute of technology",
                                              "mit sloan", "sloan school"),
    "university of california, berkeley": ("berkeley", "haas school"),
    "harvard university": ("harvard",),
    "london school of economics": ("london school of economics", "lse"),
}


def school_key(name: str) -> str:
    """One spelling per school: "NYU Stern" and "New York University" agree."""
    low = re.sub(r"\s+", " ", (name or "").lower()).strip()
    low = re.sub(r"^the ", "", low)
    for key, needles in _SCHOOL_ALIASES.items():
        if any(re.search(rf"\b{re.escape(n)}\b", low) for n in needles):
            return key
    return re.sub(r"[^a-z ,]", "", low).strip(" ,")


# ---------------------------------------------------------------------------
# Degrees

_DEGREES = [
    (r"Ph\.?\s?D\.?|doctorate|Doctor(?:'s|s)? of Philosophy", "PhD"),
    (r"J\.?D\.?(?![a-z])|Juris Doctor", "JD"),
    (r"LL\.?M\.?", "LLM"),
    (r"M\.?B\.?A\.?(?![a-z])|Master(?:'s|s)? of Business Administration", "MBA"),
    (r"M\.?S\.?c?\.?(?![a-zA-Z])|Master(?:'s|s)? of Science|master'?s degree in [a-z]+ science", "MS"),
    (r"M\.?A\.?(?![a-zA-Z])|Master(?:'s|s)? of Arts", "MA"),
    (r"M\.?Eng\.?|Master(?:'s|s)? of Engineering", "MEng"),
    (r"M\.?P\.?[AP]\.?(?![a-zA-Z])|Master(?:'s|s)? of Public (?:Administration|Policy)", "MPA"),
    # "Masters of Architecture" is written as often as "Master's". Without
    # the bare plural, [Mm]aster matches only "Master" and the leftover "s"
    # becomes the field: "Masters of Architecture" read as "s of Architecture".
    (r"[Mm]aster(?:'s|s)?(?: degree)?", "Master's"),
    (r"B\.?B\.?A\.?(?![a-zA-Z])|Bachelor(?:'s|s)? of Business Administration", "BBA"),
    (r"B\.?A\.?Sc\.?(?![a-zA-Z])|B\.?S\.?c?\.?(?![a-zA-Z])|Bachelor(?:'s|s)? of Science", "BS"),
    (r"B\.?A\.?(?![a-zA-Z])|Bachelor(?:'s|s)? of Arts", "BA"),
    (r"B\.?Eng\.?|Bachelor(?:'s|s)? of Engineering", "BEng"),
    (r"A\.?B\.?(?![a-zA-Z])", "AB"),
    (r"[Bb]achelor(?:'s|s)?(?: degree)?|undergraduate degree", "Bachelor's"),
]
_DEGREE = re.compile("|".join(f"(?P<d{i}>{p})" for i, (p, _) in enumerate(_DEGREES)))
_EDU_VERB = re.compile(
    r"(?i)\b(graduat\w*|degree|alumn\w*|studied|earned|received|holds|attended|"
    r"bachelor|master|doctorate)\b")
_FIELD = re.compile(
    r"\b(?:in|of)\s+((?:[A-Z][a-z]+|and|&)(?:\s+(?:[A-Z][a-z]+|and|&|of))*)")
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")


def _degree_name(match: re.Match) -> str:
    for i, (_, name) in enumerate(_DEGREES):
        if match.group(f"d{i}"):
            return name
    return ""


def _sentences(text: str) -> list[str]:
    # Abbreviated degrees end in dots ("B.A. in Economics"); do not split there.
    protected = re.sub(r"\b([A-Z])\.(?=[A-Z]?\.?\s)", r"\1<dot>", text or "")
    protected = re.sub(r"\b(Mr|Ms|Mrs|Dr|St|Jr|Sr|Inc|Co|Corp)\.", r"\1<dot>", protected)
    parts = re.split(r"(?<=[.!?;])\s+(?=[A-Z\"(])|\n+", protected)
    return [p.replace("<dot>", ".").strip() for p in parts if p.strip()]


def _listed(text: str, heading: str) -> list[str]:
    """Lines a bio page listed under a heading, as site_people.prose keeps
    them: "Education: MBA, Strategy & Innovation, Boston University"."""
    return [m.group(1).strip() for m in
            re.finditer(rf"(?im)^{heading}:\s*(.+)$", text or "")]


def _listed_degree(line: str) -> dict | None:
    """One line of an Education list: "MBA, Finance, Boston University"."""
    school = _SCHOOL.search(line)
    name = _tidy_school(school.group(0)) if school else ""
    if not name:
        bare = next((full for w, full in _BARE_SCHOOLS.items()
                     if re.search(rf"\b{re.escape(w)}\b", line, re.I)), "")
        name = bare
    if not name:
        return None
    d = _DEGREE.search(line[: school.start()] if school else line)
    degree = _degree_name(d) if d else ""
    field = ""
    head = line[: school.start()] if school else ""
    if d and head:
        rest = head[d.end():].strip(" ,–—-")
        rest = re.sub(r"^(?:in|of)\s+", "", rest)
        if rest and not _DEGREE.fullmatch(rest) and len(rest.split()) <= 6:
            field = rest.strip(" ,")
    years = _YEAR.findall(line)
    return {"school": name, "degree": degree, "field": field,
            "year": years[-1] if years else ""}


def education(text: str) -> list[dict]:
    """[{school, degree, field, year}] from the sentences about a degree,
    and from the lines a page lists under an "Education" heading."""
    text = (text or "").replace("\u2019", "'")
    out, seen = [], set()
    for line in _listed(text, "Education"):
        entry = _listed_degree(line)
        if entry and (school_key(entry["school"]), entry["degree"]) not in seen:
            seen.add((school_key(entry["school"]), entry["degree"]))
            out.append(entry)
    body = re.sub(r"(?im)^[A-Z][a-z ]+:\s.*$", "", text)
    for sentence in _sentences(body):
        degrees = list(_DEGREE.finditer(sentence))
        if not degrees and not _EDU_VERB.search(sentence):
            continue
        schools = [m for m in _SCHOOL.finditer(sentence)]
        names = [(m.start(), _tidy_school(m.group(0))) for m in schools]
        if not names and (degrees or _EDU_VERB.search(sentence)):
            for word in re.findall(r"\b[A-Za-z]+(?:\s+Stern)?\b", sentence):
                full = _BARE_SCHOOLS.get(word.lower())
                if full and (word[0].isupper()):
                    names.append((sentence.find(word), full))
                    break
        names = [(at, n) for at, n in names if n and len(n.split()) <= 12]
        # "Cornell University and New York University" is two schools.
        split = []
        for at, n in names:
            parts = re.split(r"\s+(?:and|&)\s+", n)
            # Not "Virginia Polytechnic Institute and State University".
            if (len(parts) > 1 and all(re.search(_SCHOOL_WORD, x) for x in parts)
                    and not re.match(r"State\b", parts[1])):
                split += [(at + n.find(x), x) for x in parts]
            else:
                split.append((at, n))
        names = split
        if not names:
            continue
        if not degrees:
            # "She graduated from Cornell University" — a school, no degree.
            if not re.search(r"(?i)graduat|alumn|attended|studied", sentence):
                continue
        for k, (at, school) in enumerate(names):
            # The degree nearest before this school, or the k-th degree.
            before = [d for d in degrees if d.start() < at]
            d = before[-1] if before else (degrees[k] if k < len(degrees) else None)
            degree = _degree_name(d) if d else ""
            field = ""
            if d:
                f = _FIELD.match(sentence, d.end() + (1 if sentence[d.end():d.end() + 1] == " " else 0))
                if f is None:
                    f = _FIELD.search(sentence[d.end(): at if at > d.end() else None])
                if f:
                    field = re.sub(r"\s+(?:and|&|of)$", "", f.group(1)).strip()
                    if _SCHOOL.fullmatch(field) or re.search(_SCHOOL_WORD, field):
                        field = ""
            # The year written after this school and before the next one;
            # with only one school, any year in the sentence is its year.
            nxt = names[k + 1][0] if k + 1 < len(names) else len(sentence)
            years = _YEAR.findall(sentence[at:nxt]) or (
                _YEAR.findall(sentence) if len(names) == 1 else [])
            key = school_key(school)
            if (key, degree) in seen:
                continue
            seen.add((key, degree))
            out.append({"school": school, "degree": degree, "field": field,
                        "year": years[0] if years else ""})
    return out


def _tidy_school(name: str) -> str:
    name = re.sub(r"^(?:the|from|at|and|a|an)\s+", "", name.strip(), flags=re.I)
    name = re.sub(r"^(?:Graduate|Alumn\w+|Graduated)\s+(?:of|from)\s+(?:the\s+)?", "", name)
    # A school word alone is not a school: "a Master's from the University".
    if re.fullmatch(_SCHOOL_WORD, name.strip()):
        return ""
    name = re.sub(r"^(?:[A-Z][a-z]+\s+)?(?:degree|Bachelor|Master|Doctor)\b.*?\bfrom\s+", "", name)
    name = re.sub(r"[,.\s]+$", "", name)
    # A school name does not start with a degree word or a verb.
    if re.match(r"(?i)(holds|earned|received|graduated|is|was|has)\b", name):
        return ""
    return name


# ---------------------------------------------------------------------------
# Past employers

_ORG = (r"(?P<org>(?:[A-Z][\w&'.\-]*|[A-Z]{2,})"
        r"(?:(?:\s+|\s*&\s*|\s+(?:of|de|du|la)\s+)(?:[A-Z][\w&'.\-]*|[A-Z]{2,}))*)")
_HIRE = [
    # "Prior to joining Stoford, she was a Vice President at Normere's"
    rf"(?:Prior to|Before)\s+(?:joining|her|his|their|that)[^,]*,\s+[^.]*?\b(?:at|with|for)\s+{_ORG}",
    rf"(?:[Pp]reviously|[Ff]ormerly),?\s+(?:(?:he|she|they|[A-Z][a-z]+)\s+)?(?:worked|served|was|spent|held)[^.]*?\b(?:at|with|for)\s+{_ORG}",
    rf"(?:began|started)\s+(?:her|his|their)\s+career\s+(?:[^.]*?\b)?(?:at|with)\s+{_ORG}",
    rf"spent\s+(?:\w+|\d+)\s+(?:years?|decades?)\s+(?:[^.]*?\b)?(?:at|with)\s+{_ORG}",
    rf"(?:joined|joins)\s+(?:\w+\s+){{0,6}}?from\s+{_ORG}",
    rf"(?:held|served in)\s+(?:various\s+|senior\s+)?(?:roles|positions)\s+(?:at|with)\s+{_ORG}",
    # "Her previous experience includes serving as a principal at X and as
    # senior manager at Y."
    rf"(?:previous|prior|earlier)\s+(?:work\s+)?experience\s+includes?[^.]*?\b(?:at|with|for)\s+{_ORG}",
    rf"\b(?:and|also)\s+(?:as\s+|served\s+as\s+)[^.]*?\b(?:at|with|for)\s+{_ORG}",
]
_HIRE_RE = [re.compile(p) for p in _HIRE]
# Words an org capture runs into that end it.
_ORG_TAIL = re.compile(
    r"\s+(?:where|in|as|for|from|on|since|until|before|after|during|which|who)\b.*$")
_NOT_ORG = re.compile(
    r"(?i)^(the firm|the company|the bank|the group|she|he|they|her|his|their|"
    r"this|that|a|an|it|both|several|various|ankura|january|february|march|"
    r"april|may|june|july|august|september|october|november|december)$")
# Words that begin a name containing "for" or "of", so what follows is the
# rest of that name: "Organization for Security", "Council of Economic".
# Job titles also precede "for" ("Head of Investor Relations for KeySpan"),
# so only these, not any capitalised word.
_INSTITUTION_HEADS = {
    "Organization", "Organisation", "Association", "Council", "Institute",
    "Foundation", "Society", "Center", "Centre", "Committee", "Bureau",
    "Commission", "Agency", "Federation", "Alliance", "Coalition", "League",
    "Department", "Ministry", "Office", "Board", "Fund", "Network", "Forum",
}
# A capitalised phrase ending in one of these is a kind of client, not a firm.
_NOT_ORG_TAIL = {"Customers", "Clients", "Companies", "Industries", "Markets",
                 "Organizations", "Businesses", "Teams", "Sectors", "Investors"}
_ROLE_BEFORE = re.compile(
    r"\b(?:was|as|served as|worked as)\s+(?:an?\s+|the\s+)?"
    r"(?P<role>(?:[A-Z][\w&\-]*|of|and|in|,)(?:\s+(?:[A-Z][\w&\-]*|of|and|in|,))*?)\s+(?:at|with|for)\s*$")


def employers(text: str, current: str = "") -> list[dict]:
    """[{firm, title}] for the places a bio says someone worked before.

    `current` is the firm the bio belongs to; it is never a past employer.
    """
    text = (text or "").replace("\u2019", "'")
    text = re.sub(r"(?im)^[A-Z][a-z ]+:\s.*$", "", text)
    out, seen = [], set()
    cur = re.sub(r"[^a-z]", "", (current or "").lower())
    for sentence in _sentences(text):
        for pattern in _HIRE_RE:
            for m in pattern.finditer(sentence):
                firms = _split_orgs(sentence, m.start("org"))
                # "an observer for the Organization for Security and
                # Cooperation": the word before the preposition is part of
                # a name, so what follows is not an employer.
                before = sentence[: m.start("org")].rstrip().split()
                if len(before) >= 2 and before[-1] in ("for", "of") and \
                        before[-2] in _INSTITUTION_HEADS:
                    continue
                for firm in firms:
                    if firm.split()[-1] in _NOT_ORG_TAIL:
                        continue
                    key = re.sub(r"[^a-z]", "", firm.lower())
                    if not key or key in seen or _NOT_ORG.match(firm):
                        continue
                    if cur and (key in cur or cur in key):
                        continue
                    if re.search(_SCHOOL_WORD, firm):
                        continue
                    seen.add(key)
                    role = _ROLE_BEFORE.search(sentence[: m.start("org")])
                    title = role.group("role").strip(" ,") if role else ""
                    if len(title.split()) > 8:
                        title = ""
                    out.append({"firm": firm, "title": title})
    return out


def _split_orgs(sentence: str, start: int) -> list[str]:
    """"Quenworth Selford, Harfield Rosley and Belhaven" -> three firms."""
    rest = sentence[start:]
    rest = re.split(r"(?<![A-Z])\.(?=\s|$)|[;:()]|\s[-–—]\s", rest, maxsplit=1)[0]
    pieces = re.split(r",\s+(?:and\s+)?|\s+and\s+", rest)
    out = []
    for piece in pieces:
        m = re.match(_ORG, piece.strip())
        if not m:
            break
        firm = _ORG_TAIL.sub("", m.group("org")).strip(" ,.'")
        firm = re.sub(r"'s$", "", firm)
        if firm:
            out.append(firm)
        if m.end() < len(piece.strip()):
            break
    return out


# ---------------------------------------------------------------------------
# Credentials, languages, experience

_CREDENTIALS = {
    "CFA": r"\bCFA\b|Chartered Financial Analyst",
    "CPA": r"\bCPA\b|Certified Public Accountant",
    "FRM": r"\bFRM\b|Financial Risk Manager",
    "CAIA": r"\bCAIA\b",
    "CFP": r"\bCFP\b|Certified Financial Planner",
    "CIPM": r"\bCIPM\b",
    "CMT": r"\bCMT\b|Chartered Market Technician",
    "FSA": r"\bFSA\b|Fellow of the Society of Actuaries",
    "CPCU": r"\bCPCU\b",
    "ACCA": r"\bACCA\b",
    "CFE": r"\bCFE\b|Certified Fraud Examiner",
    "CIA": r"Certified Internal Auditor",
    "PE": r"Professional Engineer\b",
    "Bar admission": r"(?im)^Bar admissions?:",
}
_LANGUAGES = {
    "Mandarin", "Cantonese", "Chinese", "Taiwanese", "Japanese", "Korean",
    "Spanish", "Portuguese", "French", "German", "Italian", "Russian",
    "Arabic", "Hindi", "Hebrew", "Dutch", "Turkish", "Vietnamese", "Thai",
    "Polish", "Greek", "Urdu", "Bengali", "Tagalog", "Swedish", "Indonesian",
}
_LANG_CONTEXT = re.compile(
    r"(?i)\b(fluent|native|speaks?|proficien\w*|bilingual|conversant|languages?)\b")
_NUMBER_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "twenty-five": 25, "thirty": 30,
    "forty": 40, "a decade": 10, "two decades": 20, "three decades": 30,
}
_EXPERIENCE = re.compile(
    r"(?i)\b(?:over|more than|nearly|almost|about|approximately|some)?\s*"
    r"(\d{1,2}|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"fifteen|twenty(?:-five)?|thirty|forty)\+?\s+years\b(?:\s+of)?"
    r"(?:\s+\w+){0,4}?\s+(?:experience|career)")
_DECADES = re.compile(r"(?i)\b(a decade|two decades|three decades)\s+of\s+(?:\w+\s+){0,3}experience")


_CREDENTIAL_SHAPE = re.compile(
    r"Certified|Chartered|Licensed|License|Registered|Fellow|Series \d|"
    r"Member|Admitted|Bar\b|Accredited|Diploma|\b[A-Z]{2,5}\b")


# "Generative AI for Business Leaders, September 2025": a course completed
# on a date, which a page lists under Certifications next to the real ones.
_COURSE = re.compile(
    r"(?i)\b(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+20\d\d\b|\bintroduction to\b")


def credentials(text: str) -> list[str]:
    """Designations named anywhere, plus anything listed under a
    Certifications or Credentials heading that is not already one of them."""
    found = [name for name, pat in _CREDENTIALS.items() if re.search(pat, text or "")]
    for heading in ("Certifications", "Certification", "Credentials",
                    "Professional credentials", "Licenses"):
        for line in _listed(text, heading):
            if any(n in _CREDENTIALS and re.search(_CREDENTIALS[n], line)
                   for n in found):
                continue
            # Only lines shaped like a credential, so a list that runs on
            # into the page's footer ("LinkedIn", "Contact") adds nothing.
            if (1 <= len(line.split()) <= 10 and line not in found
                    and _CREDENTIAL_SHAPE.search(line)
                    and not _COURSE.search(line)):
                found.append(line)
    return found


def languages(text: str) -> list[str]:
    out = []
    for sentence in _sentences(text):
        if not _LANG_CONTEXT.search(sentence):
            continue
        for word in re.findall(r"\b[A-Z][a-z]+\b", sentence):
            if word in _LANGUAGES and word not in out:
                out.append(word)
    return out


def years_experience(text: str) -> int | None:
    m = _EXPERIENCE.search(text or "")
    if m:
        raw = m.group(1).lower()
        return int(raw) if raw.isdigit() else _NUMBER_WORDS.get(raw)
    m = _DECADES.search(text or "")
    return _NUMBER_WORDS.get(m.group(1).lower()) if m else None


def read(text: str, current: str = "") -> dict:
    """Everything above, for one bio. Empty lists when nothing is stated."""
    return {
        "education": education(text),
        "employment": employers(text, current),
        "credentials": credentials(text),
        "languages": languages(text),
        "years_experience": years_experience(text),
    }


# ---------------------------------------------------------------------------
# FINRA exams

# Exam -> the desk words it implies, for desk_match. Licensing is required to
# do the job, so a Series 79 is good evidence of banking even with no title.
EXAM_DESK = {
    "79": "investment banking advisory",
    "57": "trading trader",
    "86": "research",
    "87": "research",
    "16": "research",
    "52": "municipal bonds fixed income",
    "53": "municipal bonds fixed income",
    "50": "municipal advisory",
    "3": "futures commodities derivatives",
    "31": "futures",
    "32": "futures",
    "34": "forex fx",
    "82": "private placement",
    "22": "private placement",
    "99": "operations",
    "65": "wealth investment advisory",
    "66": "wealth investment advisory",
}
# Principal and supervisor exams: the person manages registered staff.
SUPERVISORY = {"24", "26", "27", "28", "9", "10", "4", "14", "51", "53", "39", "23"}

EXAM_LABEL = {
    "7": "general securities rep", "79": "investment banking rep",
    "57": "securities trader", "86": "research analyst (analysis)",
    "87": "research analyst", "63": "state law", "65": "investment adviser",
    "66": "combined state law", "24": "general securities principal",
    "99": "operations", "52": "municipal securities rep", "3": "commodity futures",
}


def exam_code(category: str) -> str:
    """"Series 57TO" -> "57", "SIE" -> "SIE"."""
    m = re.search(r"(\d+)", category or "")
    return m.group(1) if m else (category or "").strip().upper()


def desk_from_exams(exams: list[str]) -> str:
    """The desk words a set of exam codes implies, for desk_match."""
    return " ".join(dict.fromkeys(EXAM_DESK[e] for e in exams if e in EXAM_DESK))


def supervises(exams: list[str]) -> bool:
    return any(e in SUPERVISORY for e in exams)
