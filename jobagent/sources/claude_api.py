"""Claude over the Messages API, for a metered key (ANTHROPIC_API_KEY).

This is the path anyone else running the project would take: a key from
console.anthropic.com, billed per token, no subscription and no CLI. It is
deliberately not the path for a subscription token -- an sk-ant-oat01- token
over the raw API is throttled to Haiku (see claude_cli.py), so one is
refused here and left to the CLI.

Two entry points, matching claude_cli.py:

  respond()    one request, text out, with Claude's server-side web search
               when asked -- the same contract as muse.respond.
  run_agent()  a tool-use loop over the agent's toolbox, run in-process.

Refusal fallbacks are on: if the safety classifier declines a request on
Opus, the API re-runs it on a fallback model inside the same call instead
of returning nothing. Researching a named person is ordinary work, but a
nightly batch should degrade, not stop.
"""

import json
import os

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search",
              "max_uses": 8}
# A paused server-side search loop is resumed this many times at most.
MAX_CONTINUATIONS = 5


def _key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    # .env is copied from .env.example, so an unedited checkout arrives with
    # the placeholder in place. And a subscription token is not a key.
    if not key or key.startswith("your_") or key.startswith("sk-ant-oat"):
        return ""
    return key


def available() -> bool:
    return bool(_key())


_client = None


def client():
    global _client
    if _client is None:
        import anthropic
        # The key is passed explicitly. Constructed bare, the SDK would also
        # read ANTHROPIC_AUTH_TOKEN from the environment -- which in this
        # project's .env is the subscription token the CLI uses.
        _client = anthropic.Anthropic(api_key=_key(), max_retries=4,
                                      timeout=900)
    return _client


def _check(message) -> None:
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise RuntimeError(f"refused ({category or 'no category'})")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("response hit max_tokens before finishing")


def _text(message) -> str:
    return "".join(b.text for b in message.content if b.type == "text").strip()


def respond(system: str, prompt: str, web_search: bool = False,
            effort: str = "high", max_attempts: int = 5) -> str:
    """One request, returned as the model's text.

    Streamed, because a research request runs several searches and a long
    answer and would otherwise risk the HTTP timeout. Rate limits and 5xx
    are retried by the SDK itself (max_retries on the client).
    """
    messages = [{"role": "user", "content": prompt}]
    kwargs = dict(model=MODEL, max_tokens=64000, system=system,
                  output_config={"effort": effort},
                  betas=[FALLBACK_BETA], fallbacks="default")
    if web_search:
        kwargs["tools"] = [WEB_SEARCH]

    for _ in range(MAX_CONTINUATIONS + 1):
        with client().beta.messages.stream(messages=messages, **kwargs) as s:
            message = s.get_final_message()
        if message.stop_reason != "pause_turn":
            break
        # The server's search loop paused. Send the turn back as it is and
        # it resumes; adding a "continue" message would start a new turn.
        messages = [messages[0],
                    {"role": "assistant", "content": message.content}]
    _check(message)
    return _text(message)


def run_agent(task: str, system: str, tools: list[dict], call,
              effort: str = "high", web: bool = True,
              max_steps: int = 60) -> dict:
    """A manual tool-use loop.

    `tools` are {name, description, input_schema} definitions and `call` is
    `call(name, arguments) -> (text, is_error)`. History is append-only --
    every response's full content goes back as it came, thinking blocks
    included -- and all of one turn's tool results go back in one message.
    """
    defs = list(tools) + ([WEB_SEARCH] if web else [])
    messages = [{"role": "user", "content": task}]
    cost_in = cost_out = 0
    message = None
    for _ in range(max_steps):
        message = client().beta.messages.create(
            model=MODEL, max_tokens=16000, system=system, tools=defs,
            messages=messages, output_config={"effort": effort},
            betas=[FALLBACK_BETA], fallbacks="default")
        cost_in += message.usage.input_tokens or 0
        cost_out += message.usage.output_tokens or 0
        messages.append({"role": "assistant", "content": message.content})

        if message.stop_reason == "pause_turn":
            continue
        if message.stop_reason != "tool_use":
            break
        results = []
        for block in message.content:
            if block.type != "tool_use":
                continue
            text, is_error = call(block.name, dict(block.input or {}))
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": text, "is_error": is_error})
        messages.append({"role": "user", "content": results})
    else:
        raise RuntimeError(f"agent did not finish in {max_steps} steps")

    _check(message)
    return {"text": _text(message), "turns": len(messages) // 2,
            "model": getattr(message, "model", MODEL),
            "cost_usd": None,
            "tokens": json.dumps({"input": cost_in, "output": cost_out})}
