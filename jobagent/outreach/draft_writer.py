"""Turns a researched contact plus a hook into a finished, sendable email.

`batch_outreach` writes the scaffolding and stops at [[WHO]] and [[HOOK]],
because plain Python cannot find out who works on a desk. `auto_draft` fills
those in through the Muse API. This sits between them: the research and the
hook come in as arguments — from the API, or from a person who did the
reading — and everything mechanical is handled here.

Keeping the boilerplate in one place matters more than it looks. The parts of
a cold email that are identical across thirty companies are exactly the parts
that go stale or inconsistent when they are retyped thirty times; the hook is
the only part that should differ, so it is the only part this asks for.

Nothing is sent. Output is a file, and optionally an unsent Gmail draft.
"""

import json
import os
import re

from jobagent.core import sender_profile
from jobagent.store import sheets
from jobagent.core import storage

OUT_DIR = storage.directory("drafts_ready")
CONTACTS_FILE = storage.location("contacts_found")
RESUME = sender_profile.resume_path()

# Measured over the initial outreach emails in the corpus, sent between
# 2026-08-28, not described in adjectives. The shape below is what those
# emails actually are:
#
#   subject   93% "<subject_prefix> <area> at <company>",
#             the area a median of 2 words ("Research", "ABS", "Fund Ratings")
#   body      median 6 blocks, 188 words (p25 169, p75 207)
#   opening   85% begin "I hope you're doing well."
#   closing   86% ask for 15-20 minutes, 91% mention the attached resume,
#             82% sign off "Best regards", 56% add a separate thank-you line
#
# Two things the earlier template got wrong and this one does not: it opened
# by announcing that he had applied for the role, which his own emails never
# do — they ask to learn about the work — and it appended an email/phone
# signature block that appears in none of them.
# The subject line, as measured from the sender's own outreach: 93% of the
# last three months' initial emails follow one template, and the area is a
# median of two words ("Research", "ABS", "Fund Ratings") — never the
# posting's full title. The template itself lives in the profile.
def _subject_prefix() -> str:
    return sender_profile.subject_prefix()

# Seniority and shape, not subject matter. Removing these is what turns a
# posting title into a topic.
_ROLE_NOISE_RE = re.compile(
    r"\b(senior|junior|jr\.?|sr\.?|entry[- ]level|graduate|campus|"
    r"assistant|associate|analyst|analytics professional|intern(ship)?|"
    r"manager|director|vice president|vp|officer|representative|rep|"
    r"specialist|consultant|coordinator|generalist|trainee|program(me)?|"
    r"rotational?|full[- ]time|part[- ]time|i{1,3}|20\d\d)\b",
    re.I)

_PLACE_RE = re.compile(
    r"\s*[-–(,]\s*(new york|nyc|ny|charlotte|boston|chicago|london|remote|"
    r"united states|us|usa|bethesda|stamford|jersey city|taipei)\b[^,]*",
    re.I)
_REQ_RE = re.compile(r"\s*[-–(#]?\s*(req(uisition)?\.?\s*)?\b\d{4,}\b\)?", re.I)

# The measured median is 2 words. Anything much longer stops reading like a
# subject area and starts reading like a pasted job title.
MAX_TOPIC_WORDS = 4

# Words that survive the noise filter but name no subject area on their own.
_GENERIC = {"business", "portfolio", "corporate", "global", "group", "markets",
            "finance", "financial", "capital", "operations", "strategy",
            "management", "services", "solutions", "support", "technology"}


# Compounds where a "noise" word is actually part of the subject area.
# "Analyst - Manager Research" is manager research — stripping "Manager" as a
# seniority word left "Research", which is a different job.
_PROTECTED_RE = re.compile(
    r"\b(manager research|manager selection|managed care|"
    r"investment manager|director of research|program trading)\b", re.I)


# A topic cut to four words must not end mid-phrase. Cutting "Equity
# Derivatives Sales and Trading" at four words put "Sales and at U.S. Bank"
# in a subject line.
_DANGLING = {"and", "&", "or", "of", "for", "the", "in", "to", "with", "at",
             "on", "a", "an"}


def _shorten(topic: str) -> str:
    """Bring an over-long topic down to MAX_TOPIC_WORDS at a clean break.

    The half before an "and" is a complete area ("Equity Derivatives Sales");
    failing that, cut at the limit and drop whatever connective is left
    hanging off the end.
    """
    first = re.split(r"\s+(?:and|&)\s+", topic, maxsplit=1)[0]
    if first != topic and 2 <= len(first.split()) <= MAX_TOPIC_WORDS:
        return first
    words = topic.split()[:MAX_TOPIC_WORDS]
    while len(words) > 1 and words[-1].lower() in _DANGLING:
        words.pop()
    return " ".join(words)


def subject_topic(role: str, fallback: str = "Research") -> str:
    """"Revenue Economics Analyst" -> "Revenue Economics"."""
    protected = _PROTECTED_RE.search(role or "")
    if protected:
        return protected.group(0).title()
    # "Trading Analyst at Radwell Torbrook": the company is added by
    # subject_for, so leaving it here printed it twice.
    text = re.sub(r"\s+(?:at|@)\s+.*$", "", role or "", flags=re.I)
    text = _REQ_RE.sub("", text)
    text = _PLACE_RE.sub("", text)
    segments = [seg.strip(" -–—/") for seg in re.split(r"[,/]|\s[-–—]\s", text)]
    cleaned = []
    for seg in segments:
        without = _ROLE_NOISE_RE.sub(" ", seg)
        without = re.sub(r"\s{2,}", " ", without).strip(" -–—,&")
        if len(without) >= 3:
            cleaned.append(without)
    if not cleaned:
        return fallback
    # Shortest is not the same as best. Splitting "Portfolio Analyst, Public
    # Sector Finance" leaves "Portfolio" and "Public Sector Finance"; the
    # short one is the generic half and says nothing. Prefer a segment that
    # names a subject area, then take the shortest of those.
    cleaned = [re.sub(r"\s*\([^)]*\)", "", c).strip() for c in cleaned]
    cleaned = [c for c in cleaned if len(c) >= 3]
    if not cleaned:
        return fallback
    usable = [c for c in cleaned if len(c.split()) <= MAX_TOPIC_WORDS]
    if not usable:
        return _shorten(max(cleaned, key=len))
    informative = [c for c in usable
                   if len(c.split()) >= 2 or c.lower() not in _GENERIC]
    pool = informative or usable
    multi = [c for c in pool if len(c.split()) >= 2]
    if multi:
        return min(multi, key=lambda c: (len(c.split()), len(c)))
    return min(pool, key=len)


def display_company(name: str) -> str:
    """How you write the company in a subject line: "Bright Power", not
    "JDG XNC INC". Import spellings shout; yours do not."""
    name = (name or "").strip()
    name = re.sub(r"[,\s]+(inc|llc|l\.l\.c|ltd|corp|co)\.?$", "", name,
                  flags=re.I).strip(" ,.")
    words = name.split()
    if len(words) == 1:
        if name.isupper() and len(name) <= 6:
            return name
        if name.islower():
            return name[:1].upper() + name[1:]
        return name
    letters = [c for c in name if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return " ".join(w if len(w) <= 3 and w.isupper() else w.title()
                        for w in words)
    return name


def subject_for(row: dict, fallback: str = "Research") -> str:
    company = display_company(row.get("Company", ""))
    topic = subject_topic(row.get("Position", ""), fallback)
    return f"{_subject_prefix()} {topic} at {company}"


def _sign_off() -> str:
    """Read from the profile, not baked in — see sender_profile."""
    return sender_profile.sign_off()


def compose(contact: dict, row: dict, hook: str, ask: str = "") -> str:
    """The full email, in the four-block shape the sender actually sends.

    Measured against past_outreach.txt and the most recent sends. The only
    reference to the sender's own employer is the half-clause in the intro --
    the employer's name appears about once per email, all of them there -- and
    the paragraph after it is about why they are drawn to the field and what
    caught their attention in the reader's work. An earlier version of this
    function put a paragraph of the sender's own pricing detail in the middle
    of every email and closed it with "what I cannot see from here is";
    neither appears anywhere in the corpus.

    So `hook` is the pivot paragraph: what drew him to this field, what
    specifically caught his attention in the reader's work, and what he would
    like to hear about. `contact["focus"]`, if given, is the thing the intro
    says he came across.

    Two things it must never do, both measured: mention having applied (2% of
    the corpus says "applied", 0% says "application"), and give a reason for
    writing other than wanting to learn about the person's work.
    """
    first = (contact["name"].split() or ["there"])[0]
    company = display_company(row.get("Company", ""))
    area = subject_topic(row.get("Position", ""))
    # "Marton Torton Advisory Services's" is not how anyone writes it, and
    # "Research research team" is what appending a fixed noun produced.
    # Acronyms keep their case; ordinary words are lowercased inside the
    # sentence, the way "Capital Markets team" reads in your own emails.
    area_phrase = area if area.isupper() else area[0].upper() + area[1:]

    # What the intro says he came across. A caller that researched the person
    # passes `focus` ("your work building out Matter's insurance product");
    # the bulk generators only know a title, which still beats the generic
    # fallback inside intro_sentence.
    focus = contact.get("focus", "").strip()
    title = contact.get("title", "").strip()
    # Only if the hook has not already named the title. hook_forms.with_title
    # opens on it, and this opened on it too, so the draft said "I came across
    # your background as Vice President - US ABS Ratings at Fenley" and
    # then "I'm particularly interested in your work as Vice President - US
    # ABS Ratings at Fenley" two lines later. Two modules each decided to
    # name the title and neither knew about the other.
    if not focus and title and title.lower() not in hook.lower():
        focus = f"your background as {title} at {company}"
    intro = sender_profile.intro_sentence(area_phrase, company, focus)
    # His own closing ask, in shape from his recent sends: the call or the
    # in-person chat, the resume "for your reference", the career path, and
    # the offer to work around their schedule. The resume line lives here
    # because that is where he puts it, so whatever creates the draft has to
    # attach the file.
    request = ask or (
        "If you have 15–20 minutes sometime in the next few weeks, I "
        "would really appreciate the opportunity to hear about your "
        "experience and your career path, either by phone or over an "
        "in-person chat. I've attached my resume for your reference, and "
        "I'm happy to work around your schedule."
    )
    # "for your reference" rather than "for some additional context": the
    # context form was his norm through late 2025 and is now in 11% of his
    # recent emails against 73% for reference. "career path" (44% recent,
    # 11% before) and "in-person chat" (33%, from 0%) are the same kind of
    # measured substitution — see outreach/style_profile.PHRASES, which
    # reads these rates off the corpus so they cannot go stale here.
    thanks = ("Thank you for your time and consideration, and I look "
              "forward to hearing from you.")

    return (f"Dear {first},\n\n{intro}\n\n{hook.strip()}\n\n{request}\n\n"
            f"{thanks}\n\n{_sign_off()}\n")


def _address_note(addr: str) -> str:
    try:
        from jobagent.people import address_book
        return address_book.check(addr)
    except Exception as exc:
        return f"(address history unavailable: {type(exc).__name__})"


CREATED_FILE = storage.location("drafts_created")


def _load_created() -> set:
    return {tuple(x) for x in storage.read(CREATED_FILE, []) or []}


def _save_created(pairs: set) -> None:
    storage.add(CREATED_FILE, [list(p) for p in pairs], indent=1)


def _existing_drafts(read_gmail: bool = True) -> set:
    """{(to, subject)} already drafted.

    Mostly served from a local record of what this tool created. Reading it
    back from Gmail costs one API call per draft, and doing that on every run
    exhausted the per-minute quota mid-way through creating new ones — so
    Gmail is consulted at most once and the answer is cached.
    """
    known = _load_created()
    if known or not read_gmail:
        return known
    try:
        from jobagent.store import gmail_client
        seen = {(d["to"].strip().lower(), d["subject"].strip())
                for d in gmail_client.draft_headers()}
        _save_created(seen)
        return seen
    except Exception as exc:
        print(f"    (could not read existing drafts: {type(exc).__name__}"
              f" — relying on the local record instead)")
        return known


def write(row: dict, contacts: list[dict], out_dir: str = OUT_DIR,
          create_drafts: bool = False,
          existing: set | None = None) -> list[str]:
    """Writes one file per contact, and optionally the Gmail drafts."""
    os.makedirs(out_dir, exist_ok=True)
    from jobagent.outreach import batch_outreach
    slug = batch_outreach._slug(row.get("Company", ""))
    written = []

    for n, c in enumerate(contacts, 1):
        note = _address_note(c["email"])
        # A hard stop, not a note. This check existed in bulk_draft and was
        # lost in a refactor, after which three drafts were addressed to
        # addresses that had already bounced. Both generators write through
        # here, so this is the one place it cannot be lost from again.
        if note.startswith("BOUNCED BEFORE"):
            print(f"    skipped {c['email']}: {note}")
            continue
        body = compose(c, row, c["hook"], c.get("ask", ""))
        # Named for the company, not the row. Row numbers move every time the
        # sheet is re-sorted, so row-named files left orphans behind: after
        # ZDV shifted from 592 to 591 both existed, and the stale one still
        # carried a bug that had already been fixed.
        path = os.path.join(out_dir, f"{slug}_{n}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                f"To:        {c['email']}\n"
                f"Person:    {c['name']} — {c['title']}\n"
                f"Source:    {c.get('source', '')}\n"
                f"Address:   {c.get('confidence', '')} | history: {note}\n"
                f"Company:   {row.get('Company')}  (sheet row {row['row_number']})\n"
                f"Role:      {row.get('Position')}\n"
                f"Job link:  {row.get('Job Link') or 'unknown'}\n"
                f"Subject:   {c['subject']}\n"
                f"\n{'-' * 72}\n{body}{'-' * 72}\n")
        written.append(path)

        if create_drafts:
            key = (c["email"].strip().lower(), c["subject"].strip())
            if existing is not None and key in existing:
                print(f"    already in Gmail, not duplicated: {c['email']}")
                continue
            from jobagent.store import gmail_client
            attachments = [RESUME] if os.path.exists(RESUME) else []
            gmail_client.create_draft(to=c["email"], subject=c["subject"],
                                      body_text=body, attachments=attachments)
            if existing is not None:
                existing.add(key)
                _save_created(existing)
    return written


def run(create_drafts: bool = False, contacts_file: str = CONTACTS_FILE) -> None:
    """Generates every draft described in the contacts file."""
    with open(contacts_file) as f:
        found = json.load(f)
    existing = _existing_drafts() if create_drafts else None

    # Keyed by company, never by row number. The sheet is re-sorted and
    # de-duplicated on every scheduled pass, so a row number recorded today
    # points at a different application tomorrow — which would have sent one
    # company's contacts an email about another company's job.
    from jobagent.core import status as status_mod
    from jobagent.core.company_match import _core
    from jobagent.core.date_utils import parse_date
    import datetime

    by_company: dict[str, dict] = {}
    for r in sheets.list_rows():
        key = _core(r.get("Company", "")).replace(" ", "")
        if not key:
            continue
        best = by_company.get(key)
        # Prefer an application that is still open, then the most recent.
        rank = (status_mod.is_open(r.get("Status", "")),
                parse_date(r.get("Applied Draes", "")) or datetime.date.min)
        if best is None or rank > best[0]:
            by_company[key] = (rank, r)

    total, made = 0, 0
    for key, contacts in found.items():
        entry = by_company.get(_core(key).replace(" ", ""))
        if entry is None:
            print(f"  {key}: no application on the sheet — skipped")
            continue
        row = entry[1]
        # The subject is always derived from the row resolved right now, never
        # read back from the contacts file. A stored subject was written
        # against whatever the sheet looked like that day, and this file
        # outlives several re-sorts — Zenoline's contacts were carrying a
        # subject naming a different company entirely.
        for c in contacts:
            c["subject"] = subject_for(row)
            # Rebuild the hook through the varied forms when the pieces are
            # stored, so the researched drafts do not all share one skeleton
            # either. A hand-written hook with no stored curiosity clause is
            # left exactly as written.
            if c.get("curiosity") and c.get("title"):
                from jobagent.core import hook_forms
                c["hook"] = hook_forms.with_title(
                    c["title"], display_company(row.get("Company", "")),
                    subject_topic(row.get("Position", "")).lower(),
                    c["curiosity"], c.get("email", c["name"]))
        paths = write(row, contacts, create_drafts=create_drafts,
                      existing=existing)
        made += 1
        total += len(paths)
        print(f"  row {row['row_number']:>3} | {row['Company'][:26]:26s} | "
              f"{len(paths)} draft(s)")
        for c in contacts:
            print(f"        {c['name']} — {c['title']}  <{c['email']}>")

    print(f"\n{total} draft(s) across {made} application(s) -> {OUT_DIR}/")
    if create_drafts:
        print("Also created as unsent Gmail drafts.")
    else:
        print("Files only. Re-run with create_drafts=True to put them in Gmail.")
