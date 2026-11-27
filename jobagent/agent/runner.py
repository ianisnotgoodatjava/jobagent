"""The agent pass: after the pipeline, a model reads the state and acts.

flows/watch.py runs first and does everything a rule can decide. Then this
starts a model with the toolbox (agent/toolbox.py) and web search, and gives
it one job: look at the whole search the way its owner would over coffee --
who is waiting on a reply, which cold applications deserve research today, what
follow-ups are due, what on the sheet is wrong -- act within a small budget,
and leave a brief.

The brief lands in logs/briefs/, one per day: the first pass of the day
writes it and later passes skip, so a Mac that slept through 06:00 still
gets one from the 18:00 run. A macOS notification says it is there.

Which model drives it is sources/llm.py's call. The loop needs tools, so it
runs on Claude -- the CLI (a subscription) with this project's MCP server
attached, or the API (a metered key) with the same toolbox in-process. A
Muse-only setup gets the pipeline and a note that the agent pass needs
Claude.
"""

import datetime
import json
import os
import subprocess
import sys
from jobagent.core import home, storage

BRIEF_DIR = storage.directory("briefs")
AGENT_DIR = storage.directory("agent")
SERVER = "jobtracker"

SYSTEM = """You are the job-search agent for {name}. You run unattended on \
their laptop, right after a deterministic pipeline has synced Gmail into their \
application sheet and drafted outreach for brand-new applications. You work \
only through the jobtracker tools and web search.

You are for the judgment calls rules cannot make: which applications deserve \
research today, what in the inbox needs them first, what on the sheet looks \
wrong. And for a short brief they read in the morning.

Hard rules. The tools enforce them as well, so a refusal is final: report it, \
do not retry it or work around it.
- Nothing is ever sent. research_and_draft and draft_followup leave unsent \
Gmail drafts; {first} reviews and sends every one.
- Nothing from before 2026. Nothing for rejected or closed applications. One \
firm, one approach at a time.
- Contacts are real, verified people, mid-level practitioners over \
department heads. Never invent a name, a quote, a fact or a number.
- Budget this run: at most {max_drafts} research_and_draft and \
{max_followups} draft_followup calls.{dry}

Write the way {first} writes notes to self: plain and specific, row numbers and \
names, no filler, no praise. If a tool fails, say which one in Flags and move on."""

DAILY = """Today is {today:%A, %B %-d, %Y}. Run the daily pass.

1. Look: pipeline_summary, unanswered_threads, drafts_waiting, followups_due, \
drafting_gaps.
2. Act, within budget:
   - research_and_draft for the best drafting gaps: applied in 2026, a real \
employer (not a staffing agency, not a job title sitting in the company \
column), PUBLISHES or KNOWN_ADDR tier before COLD, recent before old, and \
never two rows at the same firm.
   - draft_followup for follow-ups that are due with no draft already waiting.
3. Reply with only the brief, in Markdown, under 350 words, in exactly these \
sections:

# {today:%a %b %-d}
**Do first** -- up to five items, most urgent first: threads waiting on an \
answer (who, what they asked, how many days ago), interviews, anything with a \
deadline. Sheet row where there is one. If nothing is waiting, one line saying so.
**Ready to send** -- drafts you created this pass (to whom, which row), then \
how many other drafts are waiting.
**Pipeline** -- one line: counts by status, applications in the last 14 days.
**Flags** -- junk or duplicate rows, research that found nobody, refusals, \
failed tools. Leave the section out if there are none."""

ASK = """{question}

Answer from the tools, citing sheet rows. Be brief. Do not create drafts \
unless the question asks for one."""


def server_command() -> list[str]:
    """How to start the MCP server: this interpreter, the server script.

    The script by path rather than `-m`, so it starts from a checkout whose
    venv never ran `pip install -e .`.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return [sys.executable, "-u", os.path.join(here, "mcp_server.py")]


def registration() -> tuple[str, dict]:
    """How to register the MCP server with Claude: the `claude mcp add`
    line for Claude Code and the block for Claude Desktop's config.

    A home directory set by hand is passed along, because neither client
    starts the server with this shell's environment.
    """
    command, *rest = server_command()
    env = {home.ENV: home.home()} if os.environ.get(home.ENV) else {}
    flags = "".join(f" -e {k}={_quote(v)}" for k, v in env.items())
    line = (f"claude mcp add {SERVER} --scope user{flags} -- "
            + " ".join(_quote(a) for a in [command, *rest]))
    desktop = {"command": command, "args": rest}
    if env:
        desktop["env"] = env
    return line, {"mcpServers": {SERVER: desktop}}


def _quote(arg: str) -> str:
    return f"'{arg}'" if " " in arg else arg


def mcp_config(dry_run: bool, max_drafts: int, max_followups: int) -> str:
    """Writes the --mcp-config file for one run and returns its path."""
    os.makedirs(AGENT_DIR, exist_ok=True)
    command, *args = server_command()
    config = {"mcpServers": {SERVER: {
        "type": "stdio", "command": command, "args": args,
        "env": {home.ENV: home.home(),
                "JOBAGENT_DRY_RUN": "1" if dry_run else "0",
                "JOBAGENT_MAX_DRAFTS": str(max_drafts),
                "JOBAGENT_MAX_FOLLOWUPS": str(max_followups)},
    }}}
    path = os.path.abspath(os.path.join(AGENT_DIR, "mcp.json"))
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
    return path


def briefed_today(today: datetime.date | None = None) -> str:
    """The path of today's brief, or "" if there is none yet."""
    today = today or datetime.date.today()
    try:
        names = sorted(os.listdir(BRIEF_DIR))
    except OSError:
        return ""
    stamp = f"{today:%Y-%m-%d}"
    hits = [n for n in names if n.startswith(stamp) and n.endswith(".md")]
    return os.path.join(BRIEF_DIR, hits[-1]) if hits else ""


def _system(dry_run: bool, max_drafts: int, max_followups: int) -> str:
    from jobagent.core import sender_profile
    return SYSTEM.format(
        name=sender_profile.get("name") or "the user",
        first=sender_profile.first_name() or "the user",
        max_drafts=max_drafts, max_followups=max_followups,
        dry=("\n- This is a DRY RUN: the two drafting tools only report what "
             "they would do. Say so at the top of the brief." if dry_run else ""))


def _notify_mac(title: str, text: str) -> None:
    """A banner, best effort. Never lets a notification failure matter."""
    if sys.platform != "darwin":
        return
    def q(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"')[:220] + '"'
    try:
        subprocess.run(["osascript", "-e",
                        f"display notification {q(text)} with title {q(title)}"],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _headline(brief: str) -> str:
    """The first item under Do first, for the notification banner."""
    lines = [ln.strip(" -*") for ln in brief.splitlines()]
    try:
        start = next(i for i, ln in enumerate(lines) if ln.lower().startswith("do first"))
    except StopIteration:
        return "Today's brief is ready."
    for ln in lines[start + 1:]:
        if ln:
            return ln
    return "Today's brief is ready."


def run(question: str | None = None, dry_run: bool = False,
        force: bool = False, max_drafts: int = 3, max_followups: int = 5,
        notify: bool = True, email: bool = False) -> str:
    """One agent pass. Returns the path of what it wrote, or "" if skipped."""
    from jobagent.sources import llm

    print("=" * 68)
    print(f"AGENT — {datetime.datetime.now():%Y-%m-%d %H:%M}"
          f"{'  (dry-run)' if dry_run else ''}")
    print("=" * 68)

    backend = llm.agent_backend()
    if not backend:
        print("  skipped — the agent needs Claude: sign in the `claude` CLI or "
              "set ANTHROPIC_API_KEY.\n  Models ready: " + llm.describe())
        return ""
    if question is None and not force:
        existing = briefed_today()
        if existing:
            print(f"  already briefed today ({existing}); --force to run again")
            return ""

    today = datetime.date.today()
    task = (ASK.format(question=question) if question
            else DAILY.format(today=today))
    system = _system(dry_run, max_drafts, max_followups)
    print(f"  model: {backend}   budget: {max_drafts} research, "
          f"{max_followups} follow-ups\n", flush=True)

    from jobagent.sources import claude_cli
    try:
        if backend == "claude_cli":
            out = claude_cli.run_agent(
                task, system, mcp_config(dry_run, max_drafts, max_followups),
                server_name=SERVER)
        else:
            from jobagent.agent import toolbox
            from jobagent.sources import claude_api
            toolbox.configure(dry_run, max_drafts, max_followups)
            out = claude_api.run_agent(task, system, toolbox.definitions(),
                                       toolbox.call)
    except (claude_cli.QuotaExhausted, claude_cli.NotSignedIn) as exc:
        # Not a crash: no brief is written, so the next pass tries again.
        print(f"  skipped — {exc}\n  the next scheduled pass writes the brief")
        return ""

    text = out["text"].strip()
    if not text:
        raise RuntimeError("the agent finished without writing anything")

    stamp = datetime.datetime.now()
    if question:
        os.makedirs(AGENT_DIR, exist_ok=True)
        path = os.path.join(AGENT_DIR, f"ask-{stamp:%Y-%m-%d-%H%M%S}.md")
        body = f"> {question}\n\n{text}\n"
    elif dry_run:
        # Not in BRIEF_DIR: a rehearsal must not count as today's brief, or
        # the real pass that follows it would skip.
        os.makedirs(AGENT_DIR, exist_ok=True)
        path = os.path.join(AGENT_DIR, f"dry-run-{stamp:%Y-%m-%d-%H%M}.md")
        body = text + "\n"
    else:
        os.makedirs(BRIEF_DIR, exist_ok=True)
        path = os.path.join(BRIEF_DIR, f"{stamp:%Y-%m-%d-%H%M}.md")
        body = text + "\n"
    footer = (f"\n---\n_{backend} · {out.get('model') or ''} · "
              f"{out.get('turns') or '?'} turns"
              + (f" · ${out['cost_usd']:.2f}" if out.get("cost_usd") else "")
              + "_\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body + footer)

    print(text)
    print(footer.strip("\n-_ "))
    print(f"\n  -> {path}")

    if question is None and not dry_run:
        if notify:
            _notify_mac("Job search — today", _headline(text))
        if email:
            from jobagent.store import notify as mail
            to = mail.send_to_self(f"[job agent] {today:%a %b %-d}", text)
            print(f"  emailed the brief to {to}")
    return path
