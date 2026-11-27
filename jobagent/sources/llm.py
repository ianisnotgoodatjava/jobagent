"""The one door to a language model: Muse, Claude's API, or Claude's CLI.

Every model step -- researching a contact and writing the email, finding
postings, writing a follow-up, the common-ground search -- calls
respond() here and does not know or care which model answers.

Why a router and not one provider. On 2026-10-27 every model call moved to
Muse, which needs MODEL_API_KEY; the .env on the machine that runs the
schedule had only a Claude subscription token. Every scheduled pass from
then until 2026-11-25 -- about ninety of them -- printed "skipped -- no
MODEL_API_KEY" and drafted nothing, in a log nobody reads. A single
provider is a single credential, and a missing credential fails silently.

So each backend says whether it can run, the first that can is used, and
when one fails mid-batch the request falls through to the next instead of
taking the row down. Order:

  muse         MODEL_API_KEY set. Meta's Muse Spark, web search built in.
  claude_api   ANTHROPIC_API_KEY set (a metered sk-ant-api key).
  claude_cli   the `claude` CLI is signed in -- a Claude subscription.

Muse is first because setting its key is the most deliberate choice: you
only have one if you went and got it. The CLI is last because it shares a
usage window with interactive Claude Code. LLM_PROVIDER=claude_cli (or a
comma list) overrides the order.
"""

import os

from jobagent.sources import claude_api, claude_cli, muse

BACKENDS = {
    "muse": muse,
    "claude_api": claude_api,
    "claude_cli": claude_cli,
}
DEFAULT_ORDER = ("muse", "claude_api", "claude_cli")

# Which backend answered the last request, for the run log.
last_used = ""

# Backends that said they are out of quota during this process, and why.
# The 2026-11-25 06:00 pass met a full subscription window on its first
# request and then asked twelve more times -- eight research rows, five
# connection checks, the agent -- each failing the same way. A full quota
# does not clear in the seconds between rows, so once is enough per run.
_tripped: dict[str, str] = {}

_QUOTA_WORDS = ("insufficient_quota", "credit balance is too low",
                "quota exceeded", "usage window is full")


def _out_of_quota(exc: Exception) -> bool:
    return (isinstance(exc, claude_cli.QuotaExhausted)
            or any(w in str(exc).lower() for w in _QUOTA_WORDS))


def order() -> list[str]:
    wanted = os.environ.get("LLM_PROVIDER", "").strip()
    if not wanted:
        return list(DEFAULT_ORDER)
    names = [n.strip() for n in wanted.split(",") if n.strip()]
    unknown = [n for n in names if n not in BACKENDS]
    if unknown:
        raise SystemExit(f"LLM_PROVIDER names unknown backend(s): {unknown}. "
                         f"Choose from {', '.join(BACKENDS)}.")
    return names


def ready() -> list[str]:
    """The backends that can run right now, in the order they will be tried."""
    return [name for name in order()
            if name not in _tripped and BACKENDS[name].available()]


def why_unavailable() -> str:
    """For a skipped step's log line: out of quota, or never configured."""
    if _tripped:
        return "model out of quota for this run (" + ", ".join(_tripped) + \
            "); the next scheduled pass retries"
    return "no model configured (see `cli.py llm-status`)"


def available() -> bool:
    return bool(ready())


def require() -> None:
    """Exit with the setup instruction if no backend can run."""
    if not available():
        raise SystemExit(
            "No model is configured, so this step cannot run. Any one of:\n"
            "  * sign in the claude CLI (`claude`, then /login) -- uses a "
            "Claude subscription\n"
            "  * ANTHROPIC_API_KEY in .env -- a metered key from "
            "console.anthropic.com\n"
            "  * MODEL_API_KEY in .env -- Meta's Muse, from dev.meta.ai")


def describe() -> str:
    """One line for the run log: what will answer, and what would back it up."""
    names = ready()
    if not names:
        return "no model configured"
    return " -> ".join(names)


def respond(system: str, prompt: str, web_search: bool = False,
            effort: str = "high", max_attempts: int = 5) -> str:
    """One request to the first backend that answers.

    A backend that raises is reported and the next one is tried. A refusal
    is not treated differently: another provider may well answer what one
    declined, and the caller already handles an error per row.
    """
    global last_used
    names = ready()
    if not names:
        require()
    failures = []
    for name in names:
        try:
            text = BACKENDS[name].respond(system, prompt, web_search=web_search,
                                          effort=effort,
                                          max_attempts=max_attempts)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {str(exc)[:160]}")
            if _out_of_quota(exc):
                _tripped[name] = str(exc)[:160]
                print(f"    {name} is out of quota; not asking it again this "
                      f"run", flush=True)
            if name != names[-1]:
                print(f"    {name} failed ({str(exc)[:80]}) — trying "
                      f"{names[names.index(name) + 1]}", flush=True)
            continue
        last_used = name
        return text
    raise RuntimeError("every model backend failed -- " + " | ".join(failures))


def agent_backend() -> str:
    """The first ready backend that can drive a tool-using agent, or "".

    Muse answers single requests here but the agent loop is written for
    Claude's two paths, so a Muse-only setup gets the pipeline and not the
    agent pass.
    """
    for name in ready():
        if name in ("claude_cli", "claude_api"):
            return name
    return ""
