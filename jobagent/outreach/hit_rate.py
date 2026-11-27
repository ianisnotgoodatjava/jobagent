"""Whether the outreach landed, counted per email and kept.

`contact_policy` already reports reply rate by seniority, address format and
length, which answers "who is worth writing to". This answers a narrower
question and keeps the answer: of the notes actually sent, which earned a
reply, which never arrived, and which are simply too young to call.

That last group is why this module exists rather than one more table in
contact_policy. A reply rate computed over everything sent treats an email
posted this morning as a failure, and the weekend's nine applications would
have scored 0% on the day they were drafted. The number then moves for a
fortnight with no new information in it, which is worse than no number:
it looks like a measurement and behaves like a countdown.

So silence is only a miss once silence means something. How long that takes
is measured from his own history rather than assumed -- the gap from an
opening email to the first human reply, over every thread that got one --
and anything younger than the 90th percentile of that is reported as
pending, outside the denominator. Done this way the rate can only be moved
by an outcome, which is the property that makes it worth tracking at all.

The second thing it measures is the choice he actually took risk on. He
accepted derived addresses on 2026-11-22, so the useful split is not
"first.last against initial+last" -- that is a fact about spelling -- but
how the address was come by: proven by delivered mail, published by the
firm, derived from a recorded convention, or guessed outright. Those are
the four things a draft can be built on, and their bounce rates are the
cost of the decision.

Records persist to logs/outreach_outcomes.json. A thread that replied or
bounced cannot change, so reruns re-resolve only the undecided ones; the
Gmail walk that takes ninety seconds cold takes a few on the second pass.

Read-only with respect to mail. It never sends, drafts, or deletes.
"""

import collections
import datetime
import email.utils
import re
import statistics

from jobagent.core.config import ME_RE, NETWORKING_KEYWORDS
from jobagent.people import contact_policy
from jobagent.sources import interview_signals
from jobagent.store import gmail_client
from jobagent.core import storage

RECORDS = storage.location("outreach_outcomes")

# Fall back to this when his own history has too few replies to measure a
# latency from. Chosen as the median of the measured p90 over the periods
# this was written against, and only ever used for a cold corpus.
FALLBACK_CUTOFF_DAYS = 21

# An outcome that cannot change, so a rerun need not look at the thread.
TERMINAL = ("hit", "bounced")

# How the address was arrived at, best evidence first. The order is the one
# report() prints and the one _provenance returns on a tie.
PROVENANCE = ("published", "derived", "guessed")


def _date(raw) -> datetime.date | None:
    """A date out of an RFC 2822 header or an ISO string."""
    if not raw:
        return None
    if isinstance(raw, datetime.date):
        return raw
    try:
        return datetime.date.fromisoformat(str(raw)[:10])
    except ValueError:
        pass
    try:
        return email.utils.parsedate_to_datetime(str(raw)).date()
    except (TypeError, ValueError):
        return None


def _address(header: str) -> str:
    _, addr = email.utils.parseaddr(header or "")
    return addr.lower().strip()


# ---------------------------------------------------------------------------
# How the address was come by.

_PUBLISHED_RE = re.compile(r"published|rating action|press release|the firm's "
                           r"own|investor-contacts", re.I)
_AGGREGATOR_RE = re.compile(r"leadiq|rocketreach|clay|mailmo|prospeo|zoominfo",
                            re.I)


def _provenance(address: str, formats: dict) -> str:
    """Whether the address was published by the firm, derived, or guessed.

    What is being measured is the decision, so the category has to be one
    that was knowable *before* the email went out. An earlier version added
    a "proven" tier for any address listed in logs/address_patterns.json,
    which records what actually arrived -- and that file has no dates, so
    membership in it is a fact about the outcome, not about the choice. It
    classified 81 of 92 threads "proven" and then reported their bounce
    rate, which is delivery measured against delivery. The tell was a 19.8%
    bounce rate inside the category defined by having been delivered.

    So this reads only the `source` note in data/company_formats.json, which
    is written when the convention is established and says where it came
    from: the firm's own pages, a directory aggregator, or nothing at all.
    """
    address = (address or "").lower()
    if not address or "@" not in address:
        return "guessed"
    entry = formats.get(address.partition("@")[2]) or {}
    if not entry:
        return "guessed"
    source = entry.get("source") or ""
    if _PUBLISHED_RE.search(source) and not _AGGREGATOR_RE.search(source):
        return "published"
    return "derived"


def _evidence() -> dict:
    """The recorded address conventions, by domain."""
    return storage.read("company_formats", {}) or {}


# ---------------------------------------------------------------------------
# Collection.

def load(path: str = RECORDS) -> list[dict]:
    data = storage.read(path, [])
    return data.get("threads", []) if isinstance(data, dict) else (data or [])


def save(records: list[dict], path: str = RECORDS) -> None:
    storage.write(path, {"written": datetime.date.today().isoformat(),
                         "threads": records}, indent=1, sort_keys=True)


def _interview_in(thread: list[dict]) -> bool:
    """Whether anyone in this thread offered to meet.

    interview_signals.invite_evidence reads the body, so this is only ever
    asked of a thread fetched with one. Asked of a metadata-only thread it
    returns False every time, which would have printed "interviews 0" on
    every report whatever the truth -- a number that cannot be anything else
    is worse than no number.
    """
    return any(interview_signals.invite_evidence(m) for m in thread
               if not ME_RE.search(m.get("from", "")))


def _resolve(thread: list[dict], opener: dict) -> dict:
    """The outcome of one thread: replied, bounced, interview, how long.

    Only a message dated at or after the email counts as answering it. That
    sounds too obvious to state, and it was the first thing this got wrong:
    taking the earliest human message in the thread gave
    nora.vance@brincroft.com a reply three months before the email that it
    answered, and rcalder@torcroftresearch.com one seventeen months before.
    Both were earlier conversations with the same person, and `max(0, ...)`
    on the gap turned every one of them into a tidy nought, which is why
    most replies read as same-day and the median latency came out at zero.

    A thread already carrying a human message when he wrote is not outreach
    that landed either way, so it is marked and left out of the rate: the
    reply is to the conversation, not to this email.

    `interview` is left False here and settled by the caller, which refetches
    with bodies only for the threads that got a reply.
    """
    sent = _date(opener.get("date"))
    replied_on = None
    bounced = already_live = False

    for message in thread:
        frm = message.get("from", "")
        if ME_RE.search(frm):
            continue
        subject = message.get("subject", "")
        if (contact_policy._BOUNCE_RE.search(frm)
                or contact_policy._BOUNCE_RE.search(subject)):
            bounced = True
            continue
        if interview_signals.is_automated(frm, subject):
            continue
        when = _date(message.get("date"))
        if sent and when and when < sent:
            already_live = True
            continue
        # An undated message cannot be shown to answer this email, so it is
        # not allowed to invent a nought-day reply.
        if when is None:
            continue
        if replied_on is None or when < replied_on:
            replied_on = when

    days = None
    if sent and replied_on:
        days = (replied_on - sent).days
    return {
        "sent": sent.isoformat() if sent else "",
        "replied_on": replied_on.isoformat() if replied_on else "",
        "days_to_reply": days,
        "bounced": bounced,
        "first_touch": not already_live,
        "interview": False,
    }


def collect(days_back: int = 520, limit: int = 600,
            cached: list[dict] | None = None,
            verbose: bool = True) -> list[dict]:
    """One record per outreach thread, reusing decided ones from `cached`."""
    known = {r["thread"]: r for r in (cached or []) if r.get("thread")}
    reusable = {tid: r for tid, r in known.items()
                if r.get("outcome") in TERMINAL}

    query = f"in:sent newer_than:{days_back}d {NETWORKING_KEYWORDS}"
    if verbose:
        print(f"Searching Gmail: {query}\n", flush=True)
    sent = gmail_client.search_messages(query, max_results=limit)
    if verbose:
        print(f"{len(sent)} sent message(s); {len(reusable)} outcome(s) "
              f"already settled\n", flush=True)

    # The opening email per thread, which is the one whose age decides
    # whether silence means anything.
    openers: dict = {}
    for message in sent:
        tid = message.get("threadId") or message["id"]
        when = _date(message.get("date"))
        best = openers.get(tid)
        if not best or (when and _date(best.get("date"))
                        and when < _date(best["date"])):
            openers[tid] = message

    formats = _evidence()
    records, walked = [], 0
    for tid, opener in openers.items():
        if tid in reusable:
            records.append(reusable[tid])
            continue
        try:
            thread = gmail_client.get_thread(tid, include_body=False)
        except Exception:
            continue
        walked += 1
        if verbose and walked % 40 == 0:
            print(f"  ...{walked} thread(s) resolved", flush=True)

        to = _address(opener.get("to", ""))
        if "@" not in to:
            continue
        outcome = _resolve(thread, opener)
        # Only a thread that answered can hold an invitation, and only a
        # thread fetched with bodies can show one. Replies are about an
        # eighth of these, so the expensive fetch stays rare -- and the
        # outcome is then final, so it is paid once.
        if outcome["replied_on"] and not outcome["bounced"]:
            try:
                outcome["interview"] = _interview_in(
                    gmail_client.get_thread(tid, include_body=True))
            except Exception:
                pass
        records.append({
            "thread": tid,
            "to": to,
            "domain": to.partition("@")[2],
            "subject": opener.get("subject", ""),
            "band": contact_policy._bucket(to, opener.get("subject", "")),
            "provenance": _provenance(to, formats),
            **outcome,
            "outcome": "",          # filled by classify() against a cutoff
        })
    return records


# ---------------------------------------------------------------------------
# Scoring.

def reply_latency(records: list[dict]) -> dict:
    """How long his replies actually take, over the threads that got one."""
    days = sorted(r["days_to_reply"] for r in records
                  if r.get("days_to_reply") is not None
                  and r["days_to_reply"] >= 0
                  and r.get("first_touch") is not False)
    if not days:
        return {"n": 0, "median": None, "p90": None,
                "cutoff": FALLBACK_CUTOFF_DAYS, "measured": False}
    p90 = days[min(len(days) - 1, int(len(days) * 0.90))]
    return {"n": len(days), "median": int(statistics.median(days)),
            "p90": int(p90), "cutoff": int(p90), "measured": True}


def classify(record: dict, cutoff: int,
             today: datetime.date | None = None) -> str:
    """hit, bounced, miss, pending, or ongoing -- and pending is not a miss.

    A bounce outranks a reply: an auto-reply from a colleague on a thread
    that also bounced is not the person answering.

    `ongoing` is a thread that already had a human message in it when he
    wrote, so whether they answer says nothing about whether this email
    landed. Those are left out of the denominator rather than credited.
    """
    today = today or datetime.date.today()
    if record.get("first_touch") is False:
        return "ongoing"
    if record.get("bounced"):
        return "bounced"
    if record.get("replied_on"):
        return "hit"
    sent = _date(record.get("sent"))
    if not sent:
        return "pending"
    return "pending" if (today - sent).days < cutoff else "miss"


def score(records: list[dict], cutoff: int,
          today: datetime.date | None = None) -> list[dict]:
    """The records with `outcome` set, ready to count."""
    return [{**r, "outcome": classify(r, cutoff, today)} for r in records]


def tally(scored: list[dict]) -> dict:
    """The funnel. `rate` is replies over the threads that have an answer."""
    counts = collections.Counter(r["outcome"] for r in scored)
    decided = counts["hit"] + counts["miss"]
    arrived = decided + counts["bounced"]
    return {
        "sent": len(scored),
        "pending": counts["pending"],
        "ongoing": counts["ongoing"],
        "bounced": counts["bounced"],
        "decided": decided,
        "hits": counts["hit"],
        "misses": counts["miss"],
        "interviews": sum(1 for r in scored
                          if r.get("interview") and r["outcome"] == "hit"),
        "rate": counts["hit"] / decided if decided else None,
        "bounce_rate": counts["bounced"] / arrived if arrived else None,
    }


def by(scored: list[dict], key: str, floor: int = 1) -> list[tuple]:
    """[(name, tally)] for each value of `key`, best rate first."""
    groups = collections.defaultdict(list)
    for record in scored:
        groups[record.get(key) or "unknown"].append(record)
    rows = [(name, tally(group)) for name, group in groups.items()
            if len(group) >= floor]
    order = {name: i for i, name in enumerate(PROVENANCE)}
    return sorted(rows, key=lambda row: (
        order.get(row[0], 99) if key == "provenance"
        else -(row[1]["rate"] if row[1]["rate"] is not None else -1)))


# ---------------------------------------------------------------------------
# Reporting.

def _pct(value) -> str:
    return "    —" if value is None else f"{value:5.1%}"


def report(days_back: int = 520, limit: int = 600, refresh: bool = False,
           path: str = RECORDS, today: datetime.date | None = None) -> dict:
    """Collects, scores, prints, and saves. Returns the overall tally."""
    cached = [] if refresh else load(path)
    records = collect(days_back, limit, cached=cached)
    if not records:
        print("no outreach found in that window")
        return {}

    timing = reply_latency(records)
    scored = score(records, timing["cutoff"], today)
    save(scored, path)

    overall = tally(scored)
    bar = "=" * 68
    print(f"\n{bar}")
    print(f"OUTREACH HIT RATE — {overall['sent']} thread(s), "
          f"last {days_back} days")
    print(bar)

    if timing["measured"]:
        when = ("the same day" if timing["median"] == 0
                else f"in {timing['median']} days")
        print(f"When someone replies they usually reply {when}; "
              f"{timing['p90']} days at the 90th percentile "
              f"({timing['n']} replies measured).")
        print(f"So silence counts as a miss only after "
              f"{timing['cutoff']} days, and anything newer is pending.")
    else:
        print(f"Not enough replies to measure how long one takes; "
              f"using {timing['cutoff']} days.")

    print(f"\n  sent        {overall['sent']:4d}")
    print(f"    ongoing   {overall['ongoing']:4d}   thread was already live; "
          f"not a first touch")
    print(f"    pending   {overall['pending']:4d}   too young to call")
    print(f"    bounced   {overall['bounced']:4d}   {_pct(overall['bounce_rate'])}"
          f" of those that got an answer")
    print(f"    decided   {overall['decided']:4d}")
    print(f"      replied {overall['hits']:4d}   {_pct(overall['rate'])}"
          f"  <- the hit rate")
    print(f"      silent  {overall['misses']:4d}")
    print(f"  interviews  {overall['interviews']:4d}   "
          f"out of {overall['hits']} replies")

    for key, title in (("provenance", "how the address was come by"),
                       ("band", "who was written to")):
        print(f"\n--- {title} ---")
        for name, group in by(scored, key):
            note = "" if group["decided"] >= 10 else "  (too few to trust)"
            print(f"  {name:24s} n={group['sent']:4d}  "
                  f"reply {_pct(group['rate'])}  "
                  f"bounce {_pct(group['bounce_rate'])}"
                  f"  pending {group['pending']:3d}{note}")

    print(f"\n{bar}")
    print(f"Saved to {path}. A reply or a bounce is final, so the next run "
          f"only\nre-reads the {overall['pending'] + overall['misses']} "
          f"thread(s) still open.")
    return overall


def pending(path: str = RECORDS, today: datetime.date | None = None
            ) -> list[dict]:
    """The outreach still waiting, oldest first -- what to follow up."""
    records = load(path)
    timing = reply_latency(records)
    out = [r for r in score(records, timing["cutoff"], today)
           if r["outcome"] == "pending"]
    return sorted(out, key=lambda r: r.get("sent") or "")
