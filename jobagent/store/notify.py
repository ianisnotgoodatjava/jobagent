"""Emails you a reminder when someone actually reaches out.

The nightly run happens while you are asleep, so anything it finds has to
reach you somehow. This sends one short email to *yourself* summarising new
interview invites, coffee-chat offers and human replies since the last run.

Two hard limits, both deliberate:

  * The recipient is resolved from the authenticated Gmail profile and must be
    your own address. This never emails anyone else, whatever it is passed.
  * Everything it reports is already-seen mail, so a duplicate run cannot
    contact anyone or change anything — worst case you get the same reminder
    twice, and the seen-list below stops even that.
"""

import base64
import datetime
from email.message import EmailMessage

from googleapiclient.discovery import build

from jobagent.store.google_auth import get_credentials
from jobagent.core import storage

SEEN_FILE = storage.location("notified")


def _service():
    return build("gmail", "v1", credentials=get_credentials())


def my_address(svc=None) -> str:
    svc = svc or _service()
    return svc.users().getProfile(userId="me").execute()["emailAddress"]


def _load_seen() -> set[str]:
    return set(storage.read(SEEN_FILE, []) or [])


def _save_seen(seen: set[str]) -> None:
    # Unbounded growth would eventually make every run read a huge file; the
    # tail is all that matters for de-duplication.
    storage.add(SEEN_FILE, seen, keep_last=4000)


def send_reachout_reminder(items: list[dict], dry_run: bool = False) -> bool:
    """`items`: [{key, kind, company, role, who, detail, row}]. Returns True if sent."""
    seen = _load_seen()
    fresh = [i for i in items if i["key"] not in seen]
    if not fresh:
        print("notify: nothing new to report")
        return False

    lines = [
        f"{len(fresh)} new thing(s) worth a reply — found {datetime.date.today():%b %d}.",
        "",
    ]
    for i in fresh:
        lines.append(f"* {i['kind'].upper()} — {i['company']}"
                     f"{(' / ' + i['role']) if i.get('role') else ''}")
        if i.get("who"):
            lines.append(f"    from: {i['who']}")
        if i.get("detail"):
            lines.append(f"    {i['detail']}")
        if i.get("row"):
            lines.append(f"    sheet row {i['row']}")
        lines.append("")
    lines.append("Nothing has been replied to on your behalf — these are "
                 "waiting on you.")
    body = "\n".join(lines)

    subject = f"[job tracker] {len(fresh)} reach-out(s) waiting — {datetime.date.today():%b %d}"
    print(f"notify: {subject}")
    print(body)

    if dry_run:
        print("(dry-run — no email sent)")
        return False

    to = send_to_self(subject, body)
    _save_seen(seen | {i["key"] for i in fresh})
    print(f"notify: emailed {to}")
    return True


def send_to_self(subject: str, body: str) -> str:
    """Emails the authenticated account itself. Returns the address used.

    The address is read from the authenticated profile rather than passed
    in, so there is no argument that could redirect this to a recruiter.
    Both the reach-out reminder and the agent's brief go through here.
    """
    svc = _service()
    to = my_address(svc)
    if not to:
        raise RuntimeError("refusing to send: could not resolve your own address")

    message = EmailMessage()
    message["To"] = to
    message["From"] = to
    message["Subject"] = subject
    message.set_content(body)
    svc.users().messages().send(
        userId="me",
        body={"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()},
    ).execute()
    return to
