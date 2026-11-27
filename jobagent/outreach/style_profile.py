"""Measures how the sender actually writes, from the emails he actually sent.

The generated drafts kept coming out wrong in a way that was obvious in
hindsight: subject lines like "Question on insurer-sponsored private credit
vehicles" when 95% of the real corpus is the sender's own subject template,
"Recent <School> Graduate Inquiring about <area> at <company>". The prompt
described the voice in adjectives
("plain, specific, unhurried") and left everything structural to invention.

So this stops describing and starts measuring. It reads the sent corpus and
reports the distribution of the things that were being got wrong -- subject
template, paragraph count, length, opening sentence, closing ingredients --
and auto_draft pastes that straight into the prompt. When the corpus is
rebuilt after more mail goes out, the profile moves with it rather than
going stale in a docstring.

Measured over the initial (non-follow-up) emails in the corpus:
  subject    85% the sender's own template, 10% the same
             with "Interested in". Nothing else clears 1%.
  shape      5 paragraphs in 63% of them, median 152 words
  opening    "I hope you're doing well. My name is <name>, and I recently
             graduated from <school> with a degree in <degree>"
  closing    asks for 15-20 minutes (97%), attaches the resume (89%)

Read-only over outreach_corpus.json.
"""

from jobagent.core import email_text, sender_profile
import collections
import json
import re
import statistics

CORPUS = "outreach_corpus.json"

_ARTIFACT_RE = re.compile(r"[᐀-ᙿ]")

# How far back "how he writes" is allowed to reach. His writing moved over
# the year the corpus covers, and averaging across the move described a
# person who no longer exists: 5 paragraphs when he now writes 6, a median of
# 159 words when he now writes 188, and — worst of it — an opening sentence
# he has retired. The retired form appears 152 times, 114 of those in the
# oldest half, so it wins most_common() outright, while the form he uses now
# has 59 uses every one of which is inside the last 90 days. The prompt was
# instructing the model to imitate 2025.
# 90 days. The cliff is between 150 and 180: at 180 the retired opening wins
# the count again, and at 90 the numbers land exactly on the window someone
# had already measured by hand into a comment in draft_writer.py — 188 words,
# 6 blocks, p25 169, p75 207 — which never reached any code path.
RECENT_DAYS = 90

# ... but never to the point of having nothing to measure. If the window is
# thin, it widens to this many of the newest emails instead.
MIN_EMAILS = 120


def _sent_on(email: dict):
    """The date an email was sent, or None. Corpus dates are ISO and complete."""
    import datetime
    raw = (email.get("date") or "")[:10]
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None


def _initial(corpus: list[dict], days: int | None = RECENT_DAYS,
             follow_up: bool = False) -> list[dict]:
    """His first-contact emails, newest first, limited to the recent window.

    Follow-ups are excluded because they are a different kind of writing —
    three lines bumping a thread is no model for a first approach. Pass
    `follow_up=True` to measure that other population instead, which is what
    the length gate needs before it judges a nudge: his follow-ups run 77
    words to his first contacts' 196, so the first-contact floor rejects
    every real one he has sent.

    The window is anchored on the newest email in the corpus, NOT on today.
    This matters more than it looks: the corpus is rebuilt by hand and was
    twelve weeks behind when this was written, so a window measured from
    today selected zero emails, measure() returned {}, prompt_block() returned
    "" — and auto_draft quietly fell back to a one-line description of the
    voice. A stale corpus should make the profile old, never make it vanish.
    """
    # First contacts are "outreach" by kind, not merely "not Re:": eight
    # thank-you notes after interviews and the availability replies were all
    # being measured as cold emails.
    if follow_up:
        initial = [e for e in corpus if e.get("is_follow_up") and e.get("body")]
    else:
        initial = [e for e in corpus if e.get("body") and not e.get("is_follow_up")
                   and e.get("kind", "outreach") == "outreach"]
    dated = sorted(((d, e) for e in initial if (d := _sent_on(e))),
                   key=lambda pair: pair[0], reverse=True)
    if not dated:
        return initial if follow_up else _weighted(initial)
    if days is None:
        everyone = [e for _, e in dated]
        return everyone if follow_up else _weighted(everyone)

    import datetime
    newest = dated[0][0]
    cutoff = newest - datetime.timedelta(days=days)
    window = [e for d, e in dated if d >= cutoff]
    if len(window) < MIN_EMAILS:
        window = [e for _, e in dated[:MIN_EMAILS]]
    return window if follow_up else _weighted(window)


def _weighted(emails: list[dict]) -> list[dict]:
    """The emails with every replied one repeated, REPLY_WEIGHT times in all."""
    return emails + [e for e in emails if e.get("replied")
                     for _ in range(REPLY_WEIGHT - 1)]


def _clean(body: str) -> str:
    return _ARTIFACT_RE.sub("", body or "").replace("\r\n", "\n").strip()


def load(path: str = CORPUS) -> list[dict]:
    """His sent emails, one per piece of writing, each with `kind` and
    `replied` set — see reply_labels."""
    try:
        with open(path) as f:
            records = json.load(f)
    except (OSError, ValueError):
        return []
    from jobagent.outreach import reply_labels
    return reply_labels.annotate(records)


# How many times an email that got a reply is counted when measuring his
# style. His habits in the notes people answered should pull the profile
# toward them, without letting a handful of replies stand in for the rest.
REPLY_WEIGHT = 2


# Turns of phrase worth measuring rather than assuming. Each is something a
# generated draft gets visibly wrong in one direction or the other, and the
# rate is read off the corpus on every call so it moves when he moves.
#
# The reason this is measured and not a list of rules: three of these were
# wrong in a draft written from memory. "the intersection of quantitative
# analysis and financial markets" reads like his signature line and is in 2%
# of his recent mail, all of it one month. The sender's own job title is in
# the profile and has been used twice in the corpus. And the opener he
# uses in 85% of them was simply left out.
#
# Matched against whitespace-collapsed text: the bodies are hard-wrapped at
# about 75 characters, so a phrase regex run over the raw body undercounts
# badly — "I look forward to hearing from you" reads 6 times wrapped and 59
# unwrapped.
# The sender's own employer and job title are identity, not vocabulary, so
# they come from the gitignored profile rather than being written in here.
# With no profile filled in these match the placeholder and simply report 0%.
_NEVER = r"(?!x)x"          # matches nothing, so an unset field reports 0%


def _own_word(field: str) -> str:
    """A word-bounded regex for one field of the sender's own identity."""
    value = sender_profile.get(field).strip()
    if not value or value.isupper():   # unset, or a DEFAULTS placeholder
        return _NEVER
    return r"\b" + re.escape(value.split()[0]) + r"\b"


_EMPLOYER_RE = _own_word("employer")
_TITLE_RE = (re.escape(sender_profile.get("job_title").strip())
             if not sender_profile.get("job_title").strip().isupper()
             else _NEVER)

PHRASES = {
    "the hope-well opener": r"I hope you(?:'|\u2019)?re doing well",
    "his current employer": _EMPLOYER_RE,
    "his job title": _TITLE_RE,
    "the quantitative-analysis line": r"intersection of quantitative analysis",
    "the noun-form ask": r"If you have 15.20 minutes",
    "the adjective-form ask": r"\b(?:brief|quick) 15.20.minute\b",
    "\"if you'd be open to\"": r"If you(?:'|\u2019)?d (?:be|would be) open to",
    "\"conversation\"": r"\bconversation\b",
    "the resume, tagged \"for context\"": r"resume for context",
    "the resume, tagged \"for reference\"": r"resume for (?:your )?reference",
    "an exclamation mark": r"!",
    "a literal question": r"\?",
    "\"warm regards\"": r"warm regards",
    "a hyphen in 15-20": r"\b15-20\b",
}


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "")


def phrase_rates(path: str = CORPUS, days: int | None = RECENT_DAYS) -> dict:
    """What share of his recent first-contact emails contains each phrase."""
    emails = _initial(load(path), days=days)
    if not emails:
        return {}
    bodies = [_collapse(_clean(e["body"])) for e in emails]
    out = {}
    for name, pattern in PHRASES.items():
        rx = re.compile(pattern, re.I)
        out[name] = sum(1 for b in bodies if rx.search(b)) / len(bodies)
    return out


def measure(path: str = CORPUS, days: int | None = RECENT_DAYS,
            follow_up: bool = False) -> dict:
    emails = _initial(load(path), days=days, follow_up=follow_up)
    if not emails:
        return {}

    subjects = collections.Counter()
    openings = collections.Counter()
    paragraphs, words = [], []
    closing = collections.Counter()

    for email in emails:
        subject = re.sub(r"\s+", " ", (email.get("subject") or "").strip())
        if re.search(r"(?i)inquiring about", subject):
            subjects[sender_profile.subject_template_label(
                "Inquiring about")] += 1
        elif re.search(r"(?i)interested in", subject):
            subjects[sender_profile.subject_template_label(
                "Interested in")] += 1
        else:
            subjects["other"] += 1

        body = _clean(email["body"])
        blocks = [b.strip() for b in re.split(r"\n\s*\n", body) if b.strip()]
        paragraphs.append(len(blocks))
        words.append(len(body.split()))
        if len(blocks) >= 2:
            openings[re.sub(r"\s+", " ", blocks[1])[:60]] += 1

        tail = " ".join(blocks[-3:]).lower()
        if "15" in tail and "20" in tail:
            closing["asks for 15-20 minutes"] += 1
        if "resume" in tail:
            closing["mentions the attached resume"] += 1
        if "thank you for your time" in tail:
            closing["thanks them for their time"] += 1

    total = len(emails)
    spans = sorted(d for e in emails if (d := _sent_on(e)))
    return {
        "n": total,
        # So prompt_block can say which emails this is measured over. Claiming
        # "measured over 345 emails" while reading 150 of them is the kind of
        # false precision that makes a profile hard to trust.
        "span": (spans[0].isoformat(), spans[-1].isoformat()) if spans else None,
        "subjects": subjects.most_common(3),
        "paragraphs_mode": collections.Counter(paragraphs).most_common(1)[0],
        "words_median": int(statistics.median(words)),
        "words_range": (sorted(words)[total // 4], sorted(words)[3 * total // 4]),
        # 5th and 95th percentile, for style_check's length gate. The
        # interquartile range is the middle half by construction, so using it
        # as a limit rejects three quarters of his real emails.
        "words_p5_p95": (sorted(words)[max(0, int(total * 0.05))],
                         sorted(words)[min(total - 1, int(total * 0.95))]),
        "opening": openings.most_common(1)[0] if openings else ("", 0),
        "closing": [(k, v / total) for k, v in closing.most_common()],
    }


def prompt_block(path: str = CORPUS, days: int | None = RECENT_DAYS) -> str:
    """The measured style, formatted for pasting into a system prompt."""
    profile = measure(path, days=days)
    if not profile:
        return ""

    total = profile["n"]
    top_subject, top_count = profile["subjects"][0]
    para, para_n = profile["paragraphs_mode"]
    low, high = profile["words_range"]
    opening, opening_n = profile["opening"]

    span = profile.get("span")
    over = (f"his {total} most recent first-contact emails, "
            f"{span[0]} to {span[1]}" if span else f"{total} emails he has sent")
    lines = [
        f"HOW {sender_profile.first_name().upper()} ACTUALLY WRITES — "
        f"measured over {over}.",
        "Match this. It is not a style preference, it is what the corpus says.",
        "Older mail of his is deliberately excluded: he writes differently now.",
        "",
        f'SUBJECT: "{top_subject}"',
        f"  {top_count / total:.0%} of his subjects. Use this exact shape. The "
        f"area is the\n  desk or topic, not a question and not a headline. Do "
        f"not write subjects\n  like \"Question on X\" or \"Thoughts on Y\" — he "
        f"has never sent one.",
        "",
        f"LENGTH: {para} paragraphs ({para_n / total:.0%} of emails), "
        f"{profile['words_median']} words median,",
        f"  {low}-{high} typical. Going longer reads as a different person.",
        "",
        f"OPENING: begins \"{opening}...\"",
        f"  or a close variant — {opening_n / total:.0%} open this way. Greeting, "
        f"then who he is,\n  then why he is writing. Never open with the hook.",
        "",
        "CLOSING:",
    ]
    for phrase, share in profile["closing"]:
        lines.append(f"  {share:.0%} {phrase}")
    # sign_off() is two lines ("Best regards,\nJane Doe"). Flattening it has
    # to happen to the value, not to the finished sentence — applying the
    # replace to the whole f-string turned the comma it already ends with
    # into ",, " and shipped `Signs off "Best regards,, Jane Doe"` into every
    # prompt the generator has ever seen.
    flat = re.sub(r"\s*\n\s*", " ", sender_profile.sign_off()).strip()
    lines.append(f'  Signs off "{flat}".')
    return "\n".join(lines)


def run(path: str = CORPUS) -> None:
    block = prompt_block(path)
    if not block:
        print("No corpus. Run: cli.py build-outreach-corpus")
        return
    print(block)


# ---------------------------------------------------------------------------
# Few-shot exemplars.
#
# The measured profile above fixes structure -- subject shape, length, where
# the hook goes. It cannot convey rhythm: how a sentence turns, how much
# hedging goes into a question, how the middle paragraph moves from interest
# to a specific question. Describing that in prose produces the stilted
# register the drafts had.
#
# Showing two real emails does convey it. They are picked by topic so the
# examples come from the same kind of desk, which is what TF-IDF retrieval is
# actually good for.
#
# They are exemplars, never sources. Reusing their content is what produced a
# JRS draft that said "Norworth" and a management-trainee posting pitched on
# CMBS, so the instruction attached to them says so in as many words.

# No example older than this is shown, however well it matches on topic. The
# retrieval decay already prefers recent mail, but decay is a tilt and a
# strong topic match from a year ago can still win it. These two emails are
# what the generator is told to match the rhythm of, so a stale one does
# direct damage.
EXEMPLAR_MAX_DAYS = 180


def exemplars(company: str = "", role: str = "", area: str = "",
              k: int = 2) -> list[dict]:
    """Real past emails closest in topic, to show shape rather than content."""
    try:
        from jobagent.outreach import outreach_retrieval as orx
        index = orx.load_index()
    except Exception:
        return []
    query = orx.build_query(company=company, area=area, title="", role=role)
    try:
        # A wide pool, because three filters run after this one — follow-up,
        # too-short, one-per-company — and k+3 candidates for 2 slots
        # routinely came out with one. The block then announced "TWO EMAILS"
        # and showed a single example.
        hits = index.search(query, k=k + 12)
    except Exception:
        return []

    out, seen = [], set()
    for _score, record in hits:
        # Unwrapped: the corpus holds Gmail's 75-column plain text, and an
        # exemplar shown wrapped is copied wrapped — every draft then had a
        # newline mid-sentence, which Gmail displays as a broken line.
        body = email_text.unwrap(_clean(record.get("body", "")))
        # Skip follow-ups and anything too short to show structure.
        if record.get("is_follow_up") or len(body.split()) < 90:
            continue
        if orx._age_days(record, index.newest) > EXEMPLAR_MAX_DAYS:
            continue
        key = (record.get("company") or "").lower()
        if key and key in seen:
            continue
        seen.add(key)
        out.append({"subject": record.get("subject", ""), "body": body,
                    "date": (record.get("date") or "")[:10],
                    "replied": bool(record.get("replied"))})
        if len(out) >= k:
            break
    return out


def exemplar_block(company: str = "", role: str = "", area: str = "",
                   k: int = 2) -> str:
    """Exemplars formatted for the prompt, with the do-not-copy warning."""
    picked = exemplars(company, role, area, k)
    if not picked:
        return ""
    count = "TWO" if len(picked) == 2 else str(len(picked)).upper()
    parts = [
        f"{count} RECENT EMAIL{'S' if len(picked) != 1 else ''} "
        f"{sender_profile.first_name().upper()} ACTUALLY "
        f"SENT, on the nearest topic to this one.",
        "They are among his most recent, not his best-matching: he writes",
        "differently now than a year ago, and these are how he writes now.",
        "Match their rhythm, their hedging, and the way the middle paragraph",
        "moves from interest to a specific question.",
        "",
        "Do NOT reuse their content. The company, the desk, the facts and the",
        "hook must all come from your own research for THIS company. Copying",
        "a phrase from these is how a KBRA draft ended up saying \"Fitch\".",
        "An example marked GOT A REPLY is one a real person answered: where",
        "the two differ, lean toward that one.",
    ]
    for i, mail in enumerate(picked, 1):
        when = f" (sent {mail['date']})" if mail.get("date") else ""
        if mail.get("replied"):
            when += " — GOT A REPLY"
        parts += ["", f"--- EXAMPLE {i}{when} ---",
                  f"Subject: {mail['subject']}", "", mail["body"]]
    return "\n".join(parts)
