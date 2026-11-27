"""Scores a drafted email against the corpus, and says where it drifts.

Reinforcement learning is the wrong tool for this, and it is worth saying
why rather than half-building it. RL needs a reward per generation, and the
only outcome available here is reply / no reply: 345 emails, no reply
labels populated at all, and even fully labelled it would be roughly 38
positives. Worse, it is confounded -- Norworth replied twice and JRS never,
on near-identical text, because replies depend on who was written to far
more than on how. Optimising that signal teaches recipient selection while
appearing to teach style.

The tractable version of the same idea is a discriminator: learn what
separates his writing from generated writing, and use it as a gate. The
separation turned out to be blunt and measurable rather than subtle.
Comparing 12 generated drafts against 345 real emails:

  signature   every generated draft ended with his contact block.
              That appears in 0 of 345 real ones -- he signs off "Best
              regards, <name>" and stops. A perfect discriminator on its
              own, and pure model invention.
  length      199 words median generated against 159 real. Bounded at the
              95th percentile (229) rather than the interquartile range --
              an early cut using 139-171 flagged three quarters of his own
              emails, since by definition half of them fall outside it.
  vocabulary  "problem", "view", "question", "partly", "hoped", "liked",
              "you'd" all appear repeatedly in generated text and never in
              his. So does the bank he asked twice not to mention.

None of that needed a trained model to find, and a rule that names the
specific failure is more useful at generation time than a probability.
"""

from jobagent.core import sender_profile
import re


# Present in generated drafts, absent from all 345 real emails.
def _never_used() -> tuple:
    """Terms that must not appear in a draft -- his own contact details, and a
    bank he asked twice not to be named. They are personal, so they come from
    resume_bank.json rather than this file."""
    from jobagent.core import resume_bank
    return tuple(t.lower() for t in
                 resume_bank.load().get("background", {}).get("never_use", []))

# Words the generator reaches for that he does not. Not banned outright --
# one is fine, a cluster means the register has drifted.
_MODEL_TELLS = (
    "problem", "view", "partly", "hoped", "liked", "you'd", "question",
    "rotated", "quant", "publishing",
)

# From the corpus: 310 of 345 sign off exactly this way -- a closing, then
# The sender's full name on its own line. The name comes from the profile.
def _sign_off_re() -> re.Pattern:
    name = r"\s+".join(re.escape(p) for p in sender_profile.get("name").split())
    return re.compile(
        rf"(best regards|sincerely|best|warm regards)\s*,?\s*\n\s*"
        rf"{name or r'[^\n]+'}\s*$", re.I)


_SIGN_OFF_RE = _sign_off_re()

# 5th and 95th percentile, not the interquartile range. The IQR is the middle
# half by construction, so using it as a limit rejected 75% of his real
# emails.
#
# Measured, not frozen. These were the literals 129 and 229, taken from the
# whole corpus when this was written. His emails got longer — the recent
# median is 188 against 159 across all of it — so the old ceiling flagged 11%
# of his own recent writing as too long, and every flag pulled the generator
# back toward the length he had stopped using.
_FALLBACK_LOW, _FALLBACK_HIGH = 129, 229

# A follow-up is a different population, and judging one by the first-contact
# range is not a near miss: his follow-ups run 79 words median against 196,
# so the 152-word floor rejected every real follow-up he has ever sent, and
# the only way to satisfy it was to pad a three-line nudge into a second
# introduction. Measured over the follow-ups in the corpus.
_FALLBACK_FOLLOW_UP = 42, 160


def _length_range(follow_up: bool = False) -> tuple[int, int]:
    """His 5th-95th percentile length, over the same recent window the style
    profile uses. Falls back to the figures measured when this was written if
    there is no corpus to read."""
    try:
        from jobagent.outreach import style_profile
        low, high = style_profile.measure(
            follow_up=follow_up)["words_p5_p95"]
        return int(low), int(high)
    except Exception:
        return (_FALLBACK_FOLLOW_UP if follow_up
                else (_FALLBACK_LOW, _FALLBACK_HIGH))


def _clean(body: str) -> str:
    # CRLF normalised first: all 345 corpus emails use it, and a sign-off
    # regex written with \n matched none of them.
    text = (body or "").replace("\r\n", "\n")
    return re.sub(r"[᐀-ᙿ]", "", text).strip()


# A phrase in this share of his recent emails is one he always uses, so a
# draft without it is off-voice. A phrase in this share or less is one he has
# dropped, so a draft with it is off-voice. Between the two, no opinion —
# plenty of his turns of phrase are genuinely optional and a gate that
# insisted on them would just be describing one email.
EXPECTED, AVOIDED = 0.60, 0.10


def _phrase_problems(text: str) -> list[str]:
    """Where this draft disagrees with his measured recent habits.

    Rates are read off the corpus, not written down here. Agent-measured
    percentages in a comment would have been stale within weeks, and stale
    thresholds are exactly what made the old length gate reject 11% of his
    own writing.
    """
    try:
        from jobagent.outreach import style_profile
        rates = style_profile.phrase_rates()
        patterns = style_profile.PHRASES
        collapsed = style_profile._collapse(text)
    except Exception:
        return []

    out = []
    for name, share in rates.items():
        present = bool(re.search(patterns[name], collapsed, re.I))
        if share >= EXPECTED and not present:
            out.append(f"missing {name} — in {share:.0%} of his recent emails")
        elif share <= AVOIDED and present:
            out.append(f"uses {name} — in only {share:.0%} of his recent emails")
    return out


def check(body: str, subject: str = "", follow_up: bool = False) -> list[str]:
    """Every way this draft departs from the corpus. Empty means it passes.

    `follow_up` switches off the first-contact rules. A follow-up is a
    different template and measurably has not changed over the period the
    first-contact emails did: 79 words against 190, two body paragraphs, no
    self-introduction, no resume, no minutes ask. Checking one against the
    other would report a dozen problems with a perfectly good nudge.
    """
    text = _clean(body)
    lower = text.lower()
    problems = []

    if not follow_up:
        problems.extend(_phrase_problems(text))

    for token in _never_used():
        if token in lower:
            problems.append(
                f'contains "{token}" — appears in none of the emails he has sent')

    low, high = _length_range(follow_up=follow_up)
    words = len(text.split())
    if words > high:
        problems.append(f"{words} words — his recent range is {low}-{high}")
    elif words < low:
        problems.append(f"{words} words — shorter than anything he sends now")

    if not _SIGN_OFF_RE.search(text):
        problems.append(f'does not end "{sender_profile.sign_off()}"'
                        f' — nearly all of his do')

    hits = [w for w in _MODEL_TELLS if re.search(rf"\b{w}\b", lower)]
    if len(hits) >= 3:
        problems.append("register drift — uses " + ", ".join(hits[:5])
                        + "; these are generator habits, rare or absent in his")

    # A follow-up replies inside an existing thread, so its subject is
    # whatever that thread was called -- "Re: <Firm> Phone Screen Invite",
    # "Re: Thank You - <name>". None of the follow-ups in the corpus carry
    # first-contact template, and inventing a subject that did would start a
    # second thread instead of continuing the one he is following up on.
    if follow_up:
        if subject and not re.match(r"(?i)^re:\s*\S", subject):
            problems.append(f'subject "{subject[:44]}" does not reply to a '
                            f'thread — a follow-up needs "Re: <original>" or '
                            f'it arrives as a new one')
    elif subject and not re.match(sender_profile.subject_template_re(),
                                  subject):
        problems.append(f'subject "{subject[:44]}" is off-template — 88% are '
                        f'"{sender_profile.subject_template_label()}"')
    return problems


def run(path: str = "") -> None:
    """Checks drafted emails on disk, or reports the rules."""
    if not path:
        print(__doc__.strip())
        return
    with open(path) as f:
        body = f.read()
    problems = check(body)
    if not problems:
        print("passes — nothing separates this from the corpus")
        return
    for problem in problems:
        print(f"  ! {problem}")
