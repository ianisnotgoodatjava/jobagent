"""Drafts outreach to a team the moment an application lands on the sheet.

The gap this closes has been the same one all along: an application gets
logged, and then nothing happens until someone sits down and researches a
person by hand. Every piece needed to do it automatically already existed and
none of them were connected --

  company_formats.domain_for   which domain the employer reads mail at
  people_search.find           who works there, ranked by desk and seniority
  company_formats.address_for  their address, from a convention with evidence
  recent_contacts              minus anyone written to inside the year
  draft_writer                 the Gmail draft itself

so this is wiring, not new machinery, and it runs straight after
app_scanner.scan in the pipeline.

Up to three contacts per firm per round, on the desk applied to. One is a
single point of failure -- people leave, filter, and ignore mail -- and ten
is what turned Drafts into twelve Nomura emails nobody would send. The sender set
one per firm after a draft went to someone who did not work there, then
allowed three per team again on 2026-11-23; the verification rule is what
fixed that mistake, not the count. Several applications at one firm (BMO's
three Global Markets roles) still share one set of contacts: the newest
application gets them and the rest are reported as covered.

What it will not do is invent. No contact, no domain, no address convention
means no draft and a line saying which of the three was missing. A draft to a
guessed address costs a bounce, and bounces are charged against the domain for
every later email sent there.
"""

from jobagent.core import sender_profile
import os

from jobagent.people import company_formats
from jobagent.store import gmail_client
from jobagent.people import people_search
from jobagent.people import recent_contacts
from jobagent.outreach import style_check
from jobagent.core import status as status_mod

CONTACTS_PER_APPLICATION = 3
RESUME = sender_profile.resume_path()


def _signoff() -> str:
    """Just the name.

    This used to append `signature_contact` -- his address and phone -- under
    the sign-off. That block appears in 0 of the 345 emails he has actually
    sent, and style_check's own docstring calls it "a perfect discriminator
    on its own, and pure model invention". Worse, the bank redacts the phone,
    so what Gmail rendered was literally "<his address> | REDACTED" beneath
    his name. The recipient already has his address: he is the sender.
    """
    from jobagent.core import resume_bank
    return resume_bank.load().get("profile", {}).get("name", "")


def _body(company: str, role: str, contact: dict, bullets: list[str]) -> str:
    """The email, in the five-paragraph shape the sent corpus measures.

    Structure is measured rather than invented -- see style_profile: 88% of
    real subjects use one template, the median body is 152 words, and 97% ask
    for 15-20 minutes. What cannot be automated is the specific reason for
    writing to this person, so the paragraph that would carry it says only
    what is true: the desk they are on, and the work actually done.
    """
    title = (contact.get("title") or "").strip()
    # "since you senior Vice President Internal Audit there" -- a job title is
    # a noun, not a verb phrase, and splicing it in as one produced exactly the
    # sentence a reader stops at.
    # "an" goes by sound, not spelling, and the two cases differ: the word
    # "Senior" takes "a", the acronym "SVP" takes "an" because it is read
    # letter by letter. Checking the vowel rule alone produced "an Senior".
    first = title.split()[0] if title else ""
    if first.isupper() and len(first) > 1:
        article = "an" if first[0] in "AEIOUFHLMNRSX" else "a"
    else:
        article = "an" if first[:1].upper() in "AEIOU" else "a"
    who = f"{article} {title} there" if title else "someone working there"

    # "Dear Smith," reads as a stranger addressing a file. Without knowing how
    # someone is titled, the first name is the safe form and the one the sent
    # corpus already uses ("Dear Smith,").
    parts = [w for w in contact["name"].split() if len(w) > 1]
    greeting = parts[0] if parts else contact["name"]

    lead = (bullets[0] if bullets else "").strip()
    if lead and not lead.endswith((".", "!", "?")):
        lead += "."
    middle = "My background is quantitative, but the work I have found most useful has been applied."
    if lead:
        middle += f" {lead}"
    middle += (" What I can't tell from outside is how much of the role is the "
               "analysis itself and how much is making the case for it to "
               "people who have a view of their own, which is the sort of "
               "thing I'd rather ask about than guess at.")

    # intro_opening() is already the whole self-introduction, down to the
    # degree and the employer. Appending a second "graduated from <school>
    # degree in <major>" produced, in every draft this ever
    # with a" wrote: "...and currently work at <employer>. graduated with a
    # degree in <major>."
    #
    # The closing is the measured phrasing, not a paraphrase of it. Each of
    # these was a separate style_check failure: the noun-form ask is in 88% of
    # his recent emails and "If you'd be open to" is in none of them, the
    # en-dash in "15-20" in 98%, and "resume for your reference" in 67%
    # against "for context" in 12%.
    return "\n\n".join([
        f"Dear {greeting},",
        f"{sender_profile.intro_opening()} I'm "
        f"reaching out because I applied to the {role} role at {company}, and "
        f"as {who} you seemed like a good person to ask about the work.",
        middle,
        "If you have 15\u201320 minutes available for a brief call or an "
        "in-person chat in the coming weeks, I would really appreciate the "
        "opportunity to hear about your career path and your experience "
        "there. I've attached my resume for your reference.",
        "Thank you for your time and consideration, and I look forward to "
        "hearing from you.",
        f"Best regards,\n{_signoff()}",
    ])


def _bullets(row: dict) -> list[str]:
    try:
        from jobagent.apply import prep_package
        bank = prep_package.load_bank()
        parsed = {"requirements": [row.get("Required Skills & Experience", "")]}
        category = prep_package.detect_category(row.get("Position", ""), parsed)
        return [b["text"] for _, b in
                prep_package.rank_bullets(bank, parsed, category)[:1]]
    except Exception:
        return []


def for_row(row: dict, want: int = CONTACTS_PER_APPLICATION,
            dry_run: bool = False) -> dict:
    """Finds contacts for one application and drafts to them."""
    company = (row.get("Company") or "").strip()
    role = (row.get("Position") or "").strip()
    result = {"company": company, "role": role, "drafted": [], "why": ""}

    domain, note = company_formats.domain_for(company)
    # No domain from mail history is no longer the end: the firm's own site
    # often publishes addresses, and they say where its staff read mail.
    # The posting text the sheet already holds says which team and office
    # the job is in; searching without it ranked the firm at large.
    from jobagent.core import posting_target
    posting = posting_target.read(
        " ".join(x for x in (row.get("Required Skills & Experience", ""),
                             row.get("location", "") or row.get("Location", ""))
                 if x), role, company)
    found = people_search.find(company, role=role, domain=domain, posting=posting)
    domain = domain or found.get("domain", "")
    if not domain:
        result["why"] = (f"no mail domain — {note}; the firm's site "
                         f"publishes no address either")
        return result
    candidates = found["candidates"][:want]
    if not candidates:
        blocked = len(found["dropped"])
        result["why"] = (f"no reachable contact at {domain}"
                         + (f" ({blocked} found but not writable)" if blocked else ""))
        return result

    # The subject shape 88% of the sent corpus uses. See style_profile.
    from jobagent.outreach import draft_writer
    topic = draft_writer.subject_topic(role)
    subject = (f"{sender_profile.subject_prefix()} {topic} at "
               f"{draft_writer.display_company(company)}")
    bullets = _bullets(row)
    drafted_addresses = _drafted_addresses()

    # "Up to three contacts per firm" has to mean three in total, not three
    # more every night. It did mean three more: Kaizen Equity collected
    # rhys@ on the 21st and nora@ and tomas@ on the 23rd, and this pass
    # would have added three strangers at CAE in the same week as a reply
    # to a contact there who had interviewed him. Writing to a colleague of
    # someone mid-conversation with you is worse than writing to nobody.
    held = _covered_at(domain, drafted_addresses)
    room = want - len(held)
    if room <= 0:
        result["why"] = (f"{len(held)} contact(s) at {domain} already "
                         f"emailed or drafted: "
                         + ", ".join(sorted(held)[:3]))
        return result
    candidates = candidates[:room]

    for contact in candidates:
        if recent_contacts.is_duplicate(contact["email"], subject):
            continue
        # Not the same address -- the same person under a different guess at
        # the convention. See recent_contacts.already_addressed.
        clash = recent_contacts.already_addressed(
            contact.get("name", ""), domain, drafted_addresses)
        if clash and clash != contact["email"].lower():
            result.setdefault("skipped", []).append(
                f"{contact.get('name', '')} — already drafted as {clash}")
            continue

        body = _body(company, role, contact, bullets)
        # A gate nothing calls is decoration. This one exists because
        # generated drafts drift in measurable ways, and it was measuring
        # nothing while this function wrote straight to Gmail: every draft
        # it made failed on nine counts, his own phone number among them.
        problems = style_check.check(body, subject)
        if problems:
            result.setdefault("skipped", []).append(
                f"{contact.get('name', '')} — off-voice: {problems[0]}")
            continue

        if dry_run:
            result["drafted"].append(contact["email"])
            drafted_addresses.add(contact["email"].lower())
            continue
        try:
            gmail_client.create_draft(
                contact["email"], subject, body,
                attachments=[RESUME] if os.path.exists(RESUME) else None)
            result["drafted"].append(contact["email"])
            drafted_addresses.add(contact["email"].lower())
        except Exception as exc:
            result["why"] = f"draft failed: {type(exc).__name__}"
    return result


def _covered_at(domain: str, drafted: set[str]) -> set[str]:
    """Addresses at this firm he has already written to, or has queued.

    Both halves matter. Drafts alone misses the people he actually emailed
    months ago, and sent mail alone misses what last night's pass queued.
    """
    if not domain:
        return set()
    seen = set(recent_contacts.emailed_since()) | set(drafted)
    tail = "@" + domain.lower()
    return {a for a in seen if a.endswith(tail)}


def _drafted_addresses() -> set[str]:
    """Every address already sitting in Drafts, lowercased."""
    try:
        out = {recent_contacts._address(h.get("to", ""))
               for h in gmail_client.draft_headers()}
        out.discard("")
        return out
    except Exception:
        return set()


def one_per_firm(rows: list[dict]) -> tuple[list[dict], list[tuple[dict, dict]]]:
    """(rows to work, [(row skipped, row that covers it)]), order kept.

    The first row seen for an employer is kept, so a caller that sorts
    newest first keeps the newest application.
    """
    keep, covered, first = [], [], {}
    for row in rows:
        key = company_formats._slugify(row.get("Company", ""))
        if key and key in first:
            covered.append((row, first[key]))
            continue
        if key:
            first[key] = row
        keep.append(row)
    return keep, covered


def run(rows: list[dict] | None = None, limit: int = 10,
        want: int = CONTACTS_PER_APPLICATION, dry_run: bool = False) -> None:
    """Drafts for open applications that have no contact yet."""
    if rows is None:
        import datetime

        from jobagent.people import findability
        from jobagent.core.date_utils import parse_date
        _, missing = findability.coverage()
        # Newest first. An application from last spring is closed or forgotten;
        # the one logged this morning is the one worth a same-day email.
        rows = sorted(
            (r for r, _ in missing),
            key=lambda r: parse_date(r.get("Applied Draes", "")) or datetime.date.min,
            reverse=True)[:limit]
    rows = [r for r in rows if status_mod.is_open(r.get("Status", ""))]
    rows, covered = one_per_firm(rows)
    rows = rows[:limit]

    made = blocked = 0
    for row, other in covered:
        print(f"  {(row.get('Company') or '')[:26]:26s} — same firm as "
              f"{other.get('Position', '')[:40]!r}; one contact covers both")
    for row in rows:
        out = for_row(row, want=want, dry_run=dry_run)
        if out["drafted"]:
            made += len(out["drafted"])
            verb = "would draft" if dry_run else "drafted"
            print(f"  {out['company'][:26]:26s} {verb} "
                  f"{len(out['drafted'])}: {', '.join(out['drafted'])[:70]}")
        else:
            blocked += 1
            print(f"  {out['company'][:26]:26s} — {out['why']}")
    print(f"\n{'Would create' if dry_run else 'Created'} {made} draft(s) "
          f"across {len(rows) - blocked} application(s); {blocked} blocked.")
