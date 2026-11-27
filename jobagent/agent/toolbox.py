"""What the agent can do: this project's own functions, as tools.

The pipeline in flows/watch.py is a fixed sequence, and it is good at what
can be decided by rule. What it cannot do is judge -- which of thirty cold
applications is worth a research request today, which thread is the one to
answer first, which row on the sheet is junk. The agent is a model that
reads the same state and makes those calls, and this module is the whole of
what it is allowed to touch.

The rules live here, in code, rather than in a prompt the model may or may
not follow:

  * No tool sends anything. There is no send function to call. The two
    tools with side effects leave unsent Gmail drafts, through the same
    guarded paths the nightly run uses (address check, bounce history,
    style gate, once-only follow-up).
  * Nothing before 2026, nothing for a row that is no longer open, nothing
    at a firm that already has a draft. Refused with the reason, so the
    model can say why in the brief instead of retrying.
  * Side effects are capped per process: research_and_draft and
    draft_followup each have a budget, and dry-run turns both into reports
    of what would have happened.

Each tool returns plain data. Many of the functions underneath print as they
go; that output is captured and returned under "log" instead, because in the
MCP server stdout is the protocol channel and a stray print would corrupt it.
"""

import contextlib
import datetime
import io
import json
import os
import time
from dataclasses import dataclass, field

# Memory rule, 2026-11: never touch or draft from pre-2026 rows or contacts.
CUTOFF = datetime.date(2026, 1, 1)
MAX_RESULT = 24_000


@dataclass
class Limits:
    dry_run: bool = False
    drafts_left: int = 3
    followups_left: int = 5
    did: list = field(default_factory=list)   # what side effects happened


LIMITS = Limits()


def configure(dry_run: bool | None = None, max_drafts: int | None = None,
              max_followups: int | None = None) -> Limits:
    """Set the budget for this process. Unset values come from the env.

    The MCP server is a separate process the CLI spawns, so the runner
    passes its limits as JOBAGENT_* variables; in-process callers pass them
    as arguments.
    """
    def env_int(name, default):
        try:
            return int(os.environ.get(name, default))
        except ValueError:
            return default

    LIMITS.dry_run = (dry_run if dry_run is not None
                      else os.environ.get("JOBAGENT_DRY_RUN", "") == "1")
    LIMITS.drafts_left = (max_drafts if max_drafts is not None
                          else env_int("JOBAGENT_MAX_DRAFTS", 3))
    LIMITS.followups_left = (max_followups if max_followups is not None
                             else env_int("JOBAGENT_MAX_FOLLOWUPS", 5))
    LIMITS.did = []
    return LIMITS


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict
    required: tuple
    fn: object
    writes: bool

    def definition(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": {"type": "object",
                                 "properties": self.properties,
                                 "required": list(self.required)}}


TOOLS: dict[str, Tool] = {}


def tool(description: str, properties: dict | None = None,
         required: tuple = (), writes: bool = False):
    def register(fn):
        TOOLS[fn.__name__] = Tool(fn.__name__, description, properties or {},
                                  required, fn, writes)
        return fn
    return register


def definitions() -> list[dict]:
    return [t.definition() for t in TOOLS.values()]


def call(name: str, arguments: dict | None) -> tuple[str, bool]:
    """Run one tool. Returns (json text, is_error). Never raises."""
    found = TOOLS.get(name)
    if found is None:
        return json.dumps({"error": f"no tool named {name!r}"}), True
    args = dict(arguments or {})
    missing = [r for r in found.required if r not in args]
    unknown = [a for a in args if a not in found.properties]
    if missing or unknown:
        return json.dumps({"error": f"missing {missing}, unknown {unknown}"}), True

    _refresh_if_stale()
    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log):
            result = found.fn(**args)
        is_error = isinstance(result, dict) and "refused" in result
        payload = {"result": result}
    except Exception as exc:  # a tool failure is data for the model
        payload = {"error": f"{type(exc).__name__}: {str(exc)[:400]}"}
        is_error = True
    tail = log.getvalue().strip()
    if tail:
        payload["log"] = tail[-6000:]
    text = json.dumps(payload, default=_jsonable, ensure_ascii=False)
    if len(text) > MAX_RESULT:
        text = text[:MAX_RESULT] + ' ... [truncated]"}'
    return text, is_error


def _jsonable(value):
    if isinstance(value, (set, frozenset, tuple)):
        return sorted(value, key=str)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return str(value)


# --- helpers ---------------------------------------------------------------

def _rows() -> list[dict]:
    from jobagent.store import sheets
    return sheets.list_rows()


def _applied(row: dict):
    from jobagent.core.date_utils import parse_date
    return parse_date(row.get("Applied Draes", "") or "")


def _brief(row: dict) -> dict:
    """The fields worth a model's attention, without the long text columns."""
    from jobagent.core import status as status_mod
    applied = _applied(row)
    return {
        "row": row.get("row_number"),
        "company": (row.get("Company") or "").strip(),
        "position": (row.get("Position") or "").strip(),
        "status": status_mod.normalize(row.get("Status", "")) or "blank",
        "applied": applied.isoformat() if applied else "",
        "network": (row.get("Network") or "").strip(),
        "contact": (row.get("Contact") or "").strip(),
        "has_link": bool((row.get("Job Link") or "").strip()),
        "location": (row.get("location") or "").strip(),
    }


def _row(number: int) -> dict | None:
    return next((r for r in _rows() if r.get("row_number") == number), None)


# How long a read is trusted. The store layer caches the sheet and the draft
# list for the life of the process, which is right for a twenty-minute run
# and wrong for an MCP server that Claude Desktop keeps open for days. One
# agent pass makes dozens of calls a few seconds apart and should not
# re-read the sheet for each, so the window is a minute -- and fifteen for
# the follow-up scan, which walks Gmail threads for a minute every time.
FRESH_SECONDS = 60
FOLLOWUPS_FRESH_SECONDS = 15 * 60
_last_refresh = time.monotonic()
_followup_cache: list | None = None
_followups_read_at = 0.0


def _refresh_if_stale(now: float | None = None) -> None:
    global _last_refresh, _followup_cache
    now = time.monotonic() if now is None else now
    if now - _last_refresh >= FRESH_SECONDS:
        from jobagent.store import gmail_client, sheets
        sheets.forget_cache()
        gmail_client.forget_draft_cache()
        _last_refresh = now
    if (_followup_cache is not None
            and now - _followups_read_at >= FOLLOWUPS_FRESH_SECONDS):
        _followup_cache = None


def _followups() -> list[dict]:
    global _followup_cache, _followups_read_at
    if _followup_cache is None:
        from jobagent.outreach import followup_model, followup_scheduler
        learned = followup_model.timing()
        min_age = learned.get("median_days", 7) if learned else 7
        _followup_cache = followup_scheduler.candidates(
            min_age_days=min_age, max_age_days=30)
        _followups_read_at = time.monotonic()
    return _followup_cache


# --- reading ---------------------------------------------------------------

@tool("Counts of applications by status, how many were applied to recently, "
      "and every row currently at the interviewing or offer stage. Start here.",
      {"days": {"type": "integer",
                "description": "Window for 'recent', in days. Default 14."}})
def pipeline_summary(days: int = 14) -> dict:
    rows = [_brief(r) for r in _rows()]
    today = datetime.date.today()
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    recent = [r for r in rows if r["applied"] and
              (today - datetime.date.fromisoformat(r["applied"])).days <= days]
    return {
        "today": today.isoformat(),
        "total_rows": len(rows),
        "by_status": by_status,
        f"applied_last_{days}_days": len(recent),
        "interviewing_or_offer": [r for r in rows
                                  if r["status"] in ("interviewing", "offer")],
    }


@tool("List applications on the sheet, newest first, optionally filtered.",
      {"status": {"type": "string", "description":
                  "applied, interviewing, interview-rejected, rejected or offer"},
       "company": {"type": "string",
                   "description": "Case-insensitive substring of the company"},
       "since_days": {"type": "integer",
                      "description": "Only rows applied within this many days"},
       "limit": {"type": "integer", "description": "Default 40"}})
def list_applications(status: str = "", company: str = "",
                      since_days: int = 0, limit: int = 40) -> list[dict]:
    rows = [_brief(r) for r in _rows()]
    if status:
        rows = [r for r in rows if r["status"] == status.strip().lower()]
    if company:
        rows = [r for r in rows if company.lower() in r["company"].lower()]
    if since_days:
        cutoff = datetime.date.today() - datetime.timedelta(days=since_days)
        rows = [r for r in rows if r["applied"]
                and datetime.date.fromisoformat(r["applied"]) >= cutoff]
    rows.sort(key=lambda r: r["applied"], reverse=True)
    return rows[:max(1, min(limit, 200))]


@tool("Everything on one sheet row, including the requirements text.",
      {"row": {"type": "integer", "description": "Sheet row number"}},
      required=("row",))
def get_application(row: int) -> dict:
    found = _row(row)
    if not found:
        return {"refused": f"no row {row} on the sheet"}
    return {k: v for k, v in found.items() if v not in ("", None)}


@tool("Open applications with no outreach draft yet, with a findability tier "
      "for each (PUBLISHES and KNOWN_ADDR are the cheapest to reach; COLD the "
      "hardest). These are the candidates for research_and_draft.",
      {"days": {"type": "integer",
                "description": "Applied within this many days. Default 60."},
       "limit": {"type": "integer", "description": "Default 40"}})
def drafting_gaps(days: int = 60, limit: int = 40) -> dict:
    from jobagent.people import findability
    have, missing = findability.coverage(days_back=days)
    gaps = [{**_brief(row), "tier": tier} for row, tier in missing]
    order = {t: i for i, t in enumerate(findability.ORDER)}
    gaps.sort(key=lambda g: (order.get(g["tier"], 99), g["applied"]),
              reverse=False)
    return {"with_draft": len(have), "without_draft": len(missing),
            "gaps": gaps[:limit]}


@tool("Unsent Gmail drafts waiting for the user to review and send.",
      {"limit": {"type": "integer", "description": "Default 40"}})
def drafts_waiting(limit: int = 40) -> dict:
    from email.utils import parsedate_to_datetime
    from jobagent.store import gmail_client

    def when(d):
        try:
            return parsedate_to_datetime(d.get("date") or "").date()
        except (TypeError, ValueError):
            return datetime.date.min

    drafts = gmail_client.draft_headers()
    recent = sorted(drafts, key=when, reverse=True)
    return {"count": len(drafts),
            "count_from_2026": sum(when(d) >= CUTOFF for d in drafts),
            "drafts": [{"to": d.get("to", ""), "subject": d.get("subject", ""),
                        "date": when(d).isoformat()} for d in recent[:limit]]}


@tool("Threads where someone asked the user something and they have not "
      "answered: "
      "the most time-sensitive thing in the inbox. Takes a minute or two.",
      {"days": {"type": "integer", "description": "Look back. Default 45."}})
def unanswered_threads(days: int = 45) -> list[dict]:
    from jobagent.outreach import open_loops
    return open_loops.scan(days_back=days, limit=400)


@tool("Cold outreach that got no reply and is due exactly one follow-up "
      "(never a second; never on a thank-you note or scheduling mail). Each "
      "has a thread_id for draft_followup. Takes a minute.")
def followups_due() -> list[dict]:
    return [{"thread_id": c["thread_id"], "to": c["to"],
             "subject": c["subject"], "sent": c["sent"], "age_days": c["age"],
             "draft_already_waiting": c["has_draft"],
             "original_opening": (c.get("original_body") or "")[:280]}
            for c in _followups()]


@tool("Companies where someone replied to the user and then the thread went "
      "quiet with no rejection: the warm leads easiest to lose. Slow -- "
      "several minutes -- so use it only when it matters.",
      {"min_silent_days": {"type": "integer", "description": "Default 21"}})
def warm_leads(min_silent_days: int = 21) -> list[dict]:
    from jobagent.outreach import reoutreach
    leads = reoutreach.scan(days_back=240, min_silent_days=min_silent_days)
    return [{k: v for k, v in lead.items() if k != "body"} for lead in leads]


@tool("Build the user's one-page resume PDF tailored to a job posting: the "
      "skills line reordered so what the posting asks for comes first, with "
      "skills their own bullets prove added when the posting names them, "
      "and never a skill without evidence. Returns the file path, the "
      "skills line and the gaps (what the posting wants that they cannot "
      "show). Writes a local PDF only.",
      {"posting": {"type": "string",
                   "description": "The posting text, pasted in full"},
       "company": {"type": "string"},
       "role": {"type": "string"}},
      required=("posting",))
def tailor_resume(posting: str, company: str = "", role: str = "") -> dict:
    from jobagent.apply import tailor
    if len(posting.strip()) < 200:
        return {"refused": "that is too short to be a posting -- paste the "
                           "whole thing"}
    result = tailor.tailor(posting, company=company, role=role)
    return {"path": os.path.abspath(result["path"]),
            "skills_line": result["skills"], "report": result["report"]}


@tool("People already known at a firm -- from its own website, FINRA "
      "BrokerCheck and earlier research -- ranked for a role, best first. "
      "Local database only; no web requests.",
      {"company": {"type": "string"},
       "role": {"type": "string", "description": "The position applied to"}},
      required=("company",))
def people_at_company(company: str, role: str = "") -> dict:
    from jobagent.people import people_db
    db = people_db.connect()
    try:
        people = people_db.at_company(db, company, role)
    finally:
        db.close()
    keep = ("name", "title", "band", "email", "email_basis", "verified",
            "location", "sources", "corroboration", "url", "desk_score",
            "same_office")
    return {"company": company, "count": len(people),
            "people": [{k: p.get(k) for k in keep if p.get(k) not in (None, "")}
                       for p in people[:12]]}


# --- acting (capped, guarded, unsent drafts only) --------------------------

def _research_refusal(found: dict | None, number: int) -> str:
    """Why a row may not be researched, or "" if it may."""
    from jobagent.core import status as status_mod
    if not found:
        return f"no row {number} on the sheet"
    if status_mod.normalize(found.get("Status", "")) != status_mod.APPLIED:
        return (f"row {number} is '{found.get('Status')}', not an open "
                f"application -- nothing goes to a closed or interviewing row")
    applied = _applied(found)
    if applied is None:
        return f"row {number} has no readable applied date"
    if applied < CUTOFF:
        return f"row {number} was applied {applied}, before 2026"
    company = (found.get("Company") or "").strip().lower()
    if not company or company == (found.get("Position") or "").strip().lower():
        return f"row {number}'s company is blank or is the job title -- a junk row"
    return ""


@tool("Research one open application and leave unsent Gmail drafts to a "
      "verified mid-level person there, in the user's own voice. Uses the "
      "nightly "
      "run's own path: findable person, address checked against delivery "
      "history, style gate, never invented. Takes a few minutes and costs a "
      "model request. Refuses rows that are closed, pre-2026, junk, already "
      "researched, or at a firm that already has a draft. Budget-capped.",
      {"row": {"type": "integer", "description": "Sheet row number"}},
      required=("row",), writes=True)
def research_and_draft(row: int) -> dict:
    from jobagent.outreach import auto_draft
    from jobagent.people import findability

    found = _row(row)
    reason = _research_refusal(found, row)
    if reason:
        return {"refused": reason}
    if auto_draft._already_done(found, auto_draft._done()):
        return {"refused": f"row {row} was researched before; not re-billing"}
    have, _missing = findability.coverage()
    if any(r.get("row_number") == row for r, _tier in have):
        return {"refused": f"{found['Company']} already has a draft -- one "
                           f"firm, one approach at a time"}
    if LIMITS.drafts_left <= 0:
        return {"refused": "research budget for this run is spent"}
    if LIMITS.dry_run:
        LIMITS.drafts_left -= 1
        return {"dry_run": True, "would_research": _brief(found)}

    LIMITS.drafts_left -= 1
    auto_draft.run(limit=1, min_fit=0.0, dry_run=False, create_drafts=True,
                   new_only=True, write_link=True, only_rows=[row])
    LIMITS.did.append(f"researched row {row} ({found['Company']})")
    return {"done": True, "row": row, "company": found["Company"],
            "see_log": "the log lists who was drafted to, or why nobody was"}


@tool("Write the one allowed follow-up for a thread from followups_due, as "
      "an unsent draft inside that thread. Never a second follow-up. "
      "Budget-capped.",
      {"thread_id": {"type": "string"}}, required=("thread_id",), writes=True)
def draft_followup(thread_id: str) -> dict:
    from jobagent.outreach import followup_scheduler

    cand = next((c for c in _followups() if c["thread_id"] == thread_id), None)
    if cand is None:
        return {"refused": "that thread is not due a follow-up (replied, "
                           "already followed up, too recent or too old)"}
    if cand["has_draft"]:
        return {"refused": "a follow-up draft is already waiting in that thread"}
    if LIMITS.followups_left <= 0:
        return {"refused": "follow-up budget for this run is spent"}
    LIMITS.followups_left -= 1
    if LIMITS.dry_run:
        return {"dry_run": True, "would_follow_up": cand["to"],
                "subject": cand["subject"]}
    body = followup_scheduler.draft_one(cand)
    cand["has_draft"] = True
    LIMITS.did.append(f"follow-up drafted to {cand['to']}")
    return {"done": True, "to": cand["to"], "words": len(body.split())}
