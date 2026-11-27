"""Claude through the `claude` CLI, signed in to a Claude subscription.

Why the CLI and not the API, learned the expensive way before muse.py
existed: a subscription OAuth token (sk-ant-oat01-, what `claude
setup-token` prints) over the raw Messages API is throttled to Haiku, and
every Opus request comes back 429 however long you back off. The same token
driving the CLI runs Opus with web search, because that path bills against
the subscription's own entitlements. So for someone who pays for Claude and
not for an API key, this is the backend that works.

Two entry points:

  respond()    one request in, text out -- the same contract as
               muse.respond, so llm.py can treat the two as interchangeable.
  run_agent()  a multi-turn run with this project's MCP tools attached,
               which is what the agent pass is.

Three rules carried over from the version of this that ran in 2026-09:

  * The child gets a clean environment. Launched from inside an interactive
    Claude Code session it otherwise inherits that session's variables and
    behaves as a nested child, permission rules and all.
  * Task and Bash are never available: --tools names the only built-ins
    that exist in the run. Task spawned a research subagent that ran
    twenty-odd searches per contact and emptied the usage window; Bash has
    no place in writing an email.
  * A usage-ceiling 429 is not retried. Retrying it spent four more attempts
    of quota on an identical request. The next scheduled pass picks it up.
"""

import json
import os
import shutil
import subprocess
import time
from jobagent.core import storage

# The latest Opus. An older CLI refuses it by name ("does not support this
# model; version ... or newer is required"), in which case the request is
# retried once on the "opus" alias -- the newest Opus that CLI does know --
# and a note says to update. A stale install should cost quality, not the run.
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
ALIAS = "opus"

# launchd starts with PATH=/usr/bin:/bin:/usr/sbin:/sbin, where `which`
# finds nothing. These are where the installers put it.
_KNOWN = ("/opt/homebrew/bin/claude", "/usr/local/bin/claude",
          os.path.expanduser("~/.claude/local/claude"),
          os.path.expanduser("~/.local/bin/claude"))

_TRANSIENT = ("connection closed", "rate limit", "429", "try again later",
              "overloaded", "timed out", "unable to connect", "enotfound",
              "econnreset", "etimedout", "network", "529")
_CEILING = ("would exceed", "usage limit", "limit reached", "out of extra usage")


class NotSignedIn(RuntimeError):
    """The CLI exists but has no credential. Not worth retrying."""


class QuotaExhausted(RuntimeError):
    """The subscription's usage window is full. Not worth retrying."""


def binary() -> str:
    found = shutil.which("claude")
    if found:
        return found
    for path in _KNOWN:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    return ""


def child_env() -> dict:
    """The environment the CLI runs in.

    CLAUDE* variables are what make a child read as nested inside the
    session that launched it, so they go -- except the two that carry a
    credential or a config location. ANTHROPIC_API_KEY goes too: with it set
    the CLI bills the metered key, and the whole reason to be on this path is
    to use the subscription instead (claude_api.py is the key's own path).
    """
    keep = {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR"}
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAUDE") or k in keep}
    env.pop("ANTHROPIC_API_KEY", None)
    # The bare launchd PATH cannot find node, git or the CLI's own helpers.
    extra = "/opt/homebrew/bin:/usr/local/bin"
    env["PATH"] = f"{extra}:{env.get('PATH', '/usr/bin:/bin')}"
    return env


_ready: bool | None = None


def available() -> bool:
    """Whether the CLI is installed and signed in. Costs no model request.

    `claude auth status` answers from local state in about a second, so it
    is cached for the process rather than re-asked on every call.
    """
    global _ready
    if _ready is None:
        exe = binary()
        if not exe:
            _ready = False
        else:
            try:
                out = subprocess.run([exe, "auth", "status"],
                                     capture_output=True, text=True,
                                     timeout=30, env=child_env())
                _ready = out.returncode == 0 and '"loggedIn": true' in out.stdout
            except (OSError, subprocess.TimeoutExpired):
                _ready = False
    return _ready


def _base_argv(model: str, effort: str) -> list[str]:
    return [binary(), "-p",
            "--output-format", "json",
            "--model", model,
            "--effort", effort,
            "--no-session-persistence",
            # Nothing from ~/.claude or the project's .claude/ applies: no
            # hooks, no permission rules, no MCP servers but the ones named.
            "--setting-sources", "",
            "--strict-mcp-config"]
    # Tools are fenced per caller: respond() whitelists with --tools, and
    # run_agent() -- which cannot, see there -- denies by name.


def _invoke(argv: list[str], stdin: str, timeout: int,
            extra_env: dict | None = None, cwd: str = "") -> dict:
    """One CLI run. Returns the parsed JSON result; raises on failure."""
    env = child_env()
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.run(argv, input=stdin, capture_output=True,
                              text=True, timeout=timeout, env=env,
                              cwd=cwd or os.getcwd())
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"claude CLI timed out after {timeout}s")

    try:
        data = json.loads(proc.stdout)
    except ValueError:
        data = {"is_error": True,
                "result": (proc.stderr or proc.stdout or "").strip()}
    if proc.returncode == 0 and not data.get("is_error"):
        return data

    detail = str(data.get("result") or proc.stderr or "")[:300]
    lowered = detail.lower()
    if "not logged in" in lowered or "/login" in detail or "invalid api key" in lowered:
        raise NotSignedIn(
            "The claude CLI is installed but not signed in. Run `claude` in a "
            "terminal and `/login`, or put a `claude setup-token` token in "
            ".env as CLAUDE_CODE_OAUTH_TOKEN.")
    if any(m in lowered for m in _CEILING):
        raise QuotaExhausted(f"Claude subscription usage window is full: "
                             f"{detail[:140]}")
    raise RuntimeError(detail or f"claude CLI exited {proc.returncode}")


# The model actually in use. Drops to ALIAS for the rest of the process the
# first time the CLI refuses MODEL, so a batch pays that refusal once.
_model = MODEL


def _with_retries(build, stdin: str, timeout: int, max_attempts: int,
                  extra_env: dict | None = None, cwd: str = "") -> dict:
    """Runs `build(model)` with the model fallback and transient retries."""
    global _model
    attempt = 0
    while True:
        attempt += 1
        try:
            return _invoke(build(_model), stdin, timeout, extra_env, cwd)
        except (NotSignedIn, QuotaExhausted):
            raise
        except (RuntimeError, TimeoutError) as exc:
            text = str(exc).lower()
            if "does not support this model" in text and _model != ALIAS:
                print(f"    this claude CLI predates {MODEL}; using the "
                      f"'{ALIAS}' alias. `brew upgrade --cask claude-code` "
                      f"(or `claude update`) fixes it.", flush=True)
                _model = ALIAS
                attempt -= 1
                continue
            if attempt >= max_attempts or not any(m in text for m in _TRANSIENT):
                raise
            wait = 120 * attempt if ("429" in text or "rate limit" in text) \
                else 10 * attempt
            print(f"    claude CLI: {str(exc)[:60]} — retrying in {wait}s "
                  f"(attempt {attempt}/{max_attempts})", flush=True)
            time.sleep(wait)


def respond(system: str, prompt: str, web_search: bool = False,
            effort: str = "high", max_attempts: int = 4,
            timeout: int = 1200) -> str:
    """One request, returned as the model's text. Same contract as muse."""
    tools = "WebSearch,WebFetch" if web_search else ""

    def build(model):
        argv = _base_argv(model, effort) + ["--system-prompt", system,
                                            "--tools", tools]
        if tools:
            argv += ["--allowed-tools", tools]
        return argv

    data = _with_retries(build, prompt, timeout, max_attempts)
    return str(data.get("result") or "")


# Built-ins the agent must never have. Bash is the one that matters: in
# dontAsk mode the CLI still auto-allows commands it judges read-only, and
# `echo` went through in testing. Task/Agent spawn subagents that multiply
# cost. Naming a tool the installed CLI lacks was harmless on 2.1.212
# (tested with a made-up name), so both spellings of the subagent tool go in.
DENY = ("Bash", "Task", "Agent", "Write", "Edit", "MultiEdit", "NotebookEdit")


def run_agent(task: str, system: str, mcp_config: str,
              server_name: str = "jobtracker", effort: str = "high",
              timeout: int = 2700, web: bool = True,
              workspace: str = "") -> dict:
    """A multi-turn run with one MCP server's tools and, optionally, the web.

    The fence is three things, because --tools cannot be one: on this CLI
    it filters MCP tools out along with the built-ins, and an agent run that
    way had no tracker tools at all.

      * dontAsk: anything not on the allow-list is refused rather than
        prompted for -- nobody is there at 06:00 to answer a prompt. The
        allow-list is this MCP server's tools plus web search and fetch.
      * DENY names the built-ins that must not run even when the CLI would
        auto-allow them.
      * The working directory is `workspace`, an empty folder. The CLI lets
        read-only tools into the working directory without asking, so it
        must not be the project, where .env and token.json live; a read of
        the project by absolute path was refused in testing.

    Returns {text, cost_usd, turns, model}.
    """
    builtins = "WebSearch,WebFetch" if web else ""
    workspace = workspace or os.path.abspath(
        os.path.join(storage.directory("agent"), "workspace"))
    os.makedirs(workspace, exist_ok=True)

    def build(model):
        allowed = f"mcp__{server_name}" + (f",{builtins}" if builtins else "")
        return _base_argv(model, effort) + [
            "--system-prompt", system,
            "--mcp-config", mcp_config,
            "--allowed-tools", allowed,
            "--disallowed-tools", ",".join(DENY),
            "--permission-mode", "dontAsk",
        ]

    # A research tool call runs a whole drafting request of its own, which
    # outlasts the CLI's default wait on an MCP tool.
    data = _with_retries(build, task, timeout, max_attempts=2,
                         extra_env={"MCP_TOOL_TIMEOUT": str(15 * 60 * 1000),
                                    "MCP_TIMEOUT": "60000"},
                         cwd=workspace)
    usage = data.get("modelUsage") or {}
    return {"text": str(data.get("result") or ""),
            "cost_usd": data.get("total_cost_usd"),
            "turns": data.get("num_turns"),
            "model": ", ".join(usage) or MODEL}
