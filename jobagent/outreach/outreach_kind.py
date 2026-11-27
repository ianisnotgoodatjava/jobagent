"""What kind of email a sent message is, so only first-contact outreach is learned from.

The corpus is everything in Sent that matched the networking search, and the
only split it carried was "subject starts with Re:". So eight thank-you notes
after interviews ("Thank You - Jane Doe"), interview-availability replies, a
graduation-date request and a forwarded offer letter all counted as first
approaches — and the newest "outreach example" in the corpus was a thank-you
for a Chimera interview. A thank-you note is a different kind of writing:
it opens with gratitude, not with who he is, and it is written to someone who
already said yes. Imitating it in a cold email is a mistake a reader notices.

Kinds:
  outreach    first contact: introduces himself and asks for time
  follow_up   a reply or a nudge in an existing thread
  thank_you   after a call or interview
  logistics   scheduling, availability, confirmations, admin
  forward     forwarded mail
  other       none of the above

Rules over model: the kinds are told apart by a handful of fixed openings,
which a few hundred examples would only learn less reliably.
"""

import re

from jobagent.core import sender_profile

_TEMPLATE_SUBJECT = re.compile(
    r"(?i)\b(inquir\w*|interested|seeking insights?|interest)\b.*\b(about|in|into|on)\b")
_SELF_INTRO = re.compile(
    rf"(?i)\bmy name is {re.escape(sender_profile.first_name()) or r'\w+'}\b")
_THANKS_OPEN = re.compile(
    r"(?i)^\W*(dear|hi|hello)?\s*[\w.\- ]{0,40},?\s*thank you (so much |very much )?"
    r"(for (taking the time|your time|meeting|speaking|the (call|conversation|chat|opportunity))"
    r"|again)")
_LOGISTICS = re.compile(
    r"(?i)\b(availability|phone screen|interview (invitation|availability|time)|"
    r"confirm(ing|ation)?|reschedul\w*|calendar invite|graduation date|"
    r"offer letter|background check|start date)\b")


def kind(record: dict) -> str:
    subject = (record.get("subject") or "").strip()
    body = record.get("body") or ""
    low = subject.lower()
    if low.startswith(("fwd:", "fw:")) or "---------- forwarded message" in body.lower():
        return "forward"
    if low.startswith("re:") or record.get("is_follow_up"):
        return "follow_up"
    if low.startswith("thank you") or _THANKS_OPEN.search(body[:300]):
        return "thank_you"
    intro = bool(_SELF_INTRO.search(body[:600]))
    if _LOGISTICS.search(subject) or (not intro and _LOGISTICS.search(body[:400])):
        return "logistics"
    if intro or _TEMPLATE_SUBJECT.search(subject):
        return "outreach"
    return "other"


def dedupe(records: list[dict]) -> list[dict]:
    """One record per email actually written.

    The same note to Bryan in May 2025 is in the corpus seven times, once
    per guessed address, and seven copies of one email outvote six different
    ones in every count the style profile takes. So the recipient is not
    part of the key: the same words are one piece of writing.
    """
    seen, out = set(), []
    for r in records:
        key = ((r.get("subject") or "").lower(),
               re.sub(r"\s+", " ", (r.get("body") or ""))[:400])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out
