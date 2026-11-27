"""Which of his outreach emails a real person answered.

hit_rate keeps outcomes for the drafts sent since it was written — seventy
emails. The corpus the drafts imitate is four hundred, and none of it said
whether the email worked, so every example counted the same: the note that
got a call back and the one nobody opened. This reads each one's thread once
and records whether a human replied.

A reply counts when a later message in the thread is from someone other than
him and is not a bounce or an auto-reply ("Automatic reply", "Out of office").
His own addresses come from OWN_EMAIL in .env. Read-only against Gmail;
cached in logs/outreach_replies.json, so after the first run only new emails
cost a request.
"""

import os
import re
import time

from jobagent.outreach import outreach_kind
from jobagent.core import storage

CACHE = storage.location("outreach_replies")

_BOUNCE = re.compile(r"(?i)mailer-daemon|postmaster|mail delivery (subsystem|system)")
_AUTO = re.compile(
    r"(?i)^(automatic reply|auto(matic)?[- ]?reply|out of (the )?office|ooo\b|"
    r"autoreply|away from|undeliverable|delivery status)")


def _own_addresses() -> set:
    own = {a.strip().lower() for a in os.environ.get("OWN_EMAIL", "").split(",")
           if a.strip()}
    if not own:
        raise RuntimeError("Set OWN_EMAIL in .env so his own messages are "
                           "not counted as replies.")
    return own


def _address(header: str) -> str:
    m = re.search(r"[\w.+'-]+@[\w.-]+\.\w+", header or "")
    return m.group(0).lower() if m else ""


def read_thread(messages: list[dict], sent_id: str, own: set) -> dict:
    """{replied, replied_on, bounced} from a thread's message headers.

    `messages` are {id, internalDate, from, subject, date}; only messages
    after the sent one count.
    """
    sent = next((m for m in messages if m["id"] == sent_id), None)
    after = int(sent["internalDate"]) if sent else 0
    out = {"replied": False, "replied_on": "", "bounced": False}
    for m in sorted(messages, key=lambda m: int(m["internalDate"])):
        if int(m["internalDate"]) <= after:
            continue
        sender = _address(m.get("from", ""))
        if not sender or sender in own:
            continue
        if _BOUNCE.search(m.get("from", "")):
            out["bounced"] = True
            continue
        if _AUTO.search((m.get("subject") or "").strip()):
            continue
        out["replied"] = True
        out["replied_on"] = (m.get("date") or "")[:16]
        break
    return out


def load() -> dict:
    return storage.read(CACHE, {}) or {}


def _save(cache: dict) -> None:
    storage.write(CACHE, cache, indent=1, sort_keys=True)


def label(records: list[dict] | None = None, refresh: bool = False,
          pause: float = 0.25) -> dict:
    """Fetches thread outcomes for every outreach email not yet cached."""
    from googleapiclient.errors import HttpError
    from jobagent.outreach import outreach_corpus
    from jobagent.store import gmail_client

    records = records if records is not None else outreach_corpus.load()
    cache = {} if refresh else load()
    own = _own_addresses()
    svc = gmail_client._service()

    def call(fn):
        for attempt in range(8):
            try:
                return fn()
            except HttpError as exc:
                if exc.resp.status in (403, 429):
                    time.sleep(15 * (attempt + 1))
                    continue
                if exc.resp.status == 404:
                    return None
                raise
        return None

    todo = [r for r in records
            if outreach_kind.kind(r) == "outreach" and r["id"] not in cache]
    for n, r in enumerate(todo, 1):
        msg = call(lambda: svc.users().messages().get(
            userId="me", id=r["id"], format="minimal").execute())
        if not msg:
            cache[r["id"]] = {"replied": False, "replied_on": "",
                              "bounced": False, "missing": True}
            continue
        thread = call(lambda: svc.users().threads().get(
            userId="me", id=msg["threadId"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]).execute()) or {}
        messages = []
        for m in thread.get("messages", []):
            h = {x["name"].lower(): x["value"]
                 for x in m.get("payload", {}).get("headers", [])}
            messages.append({"id": m["id"], "internalDate": m.get("internalDate", "0"),
                             "from": h.get("from", ""), "subject": h.get("subject", ""),
                             "date": h.get("date", "")})
        cache[r["id"]] = {**read_thread(messages, r["id"], own),
                          "thread": msg["threadId"]}
        if n % 25 == 0:
            _save(cache)
            print(f"  {n}/{len(todo)} threads read", flush=True)
        time.sleep(pause)
    _save(cache)
    return cache


def _key(r: dict) -> tuple:
    return ((r.get("subject") or "").lower(),
            re.sub(r"\s+", " ", (r.get("body") or ""))[:400])


def annotate(records: list[dict], cache: dict | None = None) -> list[dict]:
    """Copies of the records with `kind` and `replied` set, one per email.

    `replied` is true when any copy of the email got an answer — the same
    note sent to three guessed addresses is answered at most once, and that
    one answer belongs to the writing, not to the address.
    """
    cache = load() if cache is None else cache
    answered = {_key(r) for r in records
                if (cache.get(r.get("id")) or {}).get("replied")}
    return [{**r, "kind": outreach_kind.kind(r), "replied": _key(r) in answered}
            for r in outreach_kind.dedupe(records)]


def run(refresh: bool = False) -> None:
    from jobagent.outreach import outreach_corpus
    records = outreach_corpus.load()
    cache = label(records, refresh=refresh)
    rows = [r for r in annotate(records, cache) if r["kind"] == "outreach"]
    hits = sum(r["replied"] for r in rows)
    print(f"\n{hits} of {len(rows)} outreach emails got a human reply "
          f"({hits / max(len(rows), 1):.0%}).")
