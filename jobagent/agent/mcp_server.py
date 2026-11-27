"""The toolbox as an MCP server, so any MCP client can drive the tracker.

Two clients matter. The scheduled agent pass launches the `claude` CLI with
this server attached, and that is how the agent gets its tools. And the
same server registered in Claude Desktop or Claude Code (see .mcp.json)
means the tracker can be asked things in plain language -- "who is waiting
on a reply from me?" -- or told "draft outreach for row 742", with the same
guards and budget as the scheduled run.

It speaks the stdio transport by hand: newline-delimited JSON-RPC 2.0, and
only the tools part of the protocol (initialize, tools/list, tools/call,
ping). That is about a hundred lines, needs no new dependency, and can be
tested by piping JSON through it. If the protocol needs more than tools one
day, the official `mcp` package replaces this file and nothing else.

stdout is the protocol channel and nothing else may write to it. The
modules underneath print progress freely, so sys.stdout is pointed at stderr
for the life of the process and protocol messages go to the saved original.
"""

import json
import os
import sys

NAME = "jobtracker"
VERSION = "0.1.0"
# Spoken by every MCP client this was tested against. A client that asks for
# another version is answered with its own -- the tools subset used here has
# not changed between versions.
DEFAULT_PROTOCOL = "2025-06-18"

INSTRUCTIONS = (
    "The user's job-search tracker: the application sheet, Gmail outreach and the "
    "people database. Read tools are free to call. research_and_draft and "
    "draft_followup leave UNSENT Gmail drafts and are budget-capped; nothing "
    "here can send mail. Start with pipeline_summary.")


def handle(message: dict) -> dict | None:
    """One JSON-RPC message in, the response out (None for notifications)."""
    from jobagent.agent import toolbox

    method = message.get("method", "")
    ident = message.get("id")
    params = message.get("params") or {}
    if ident is None:                      # a notification: never answered
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": ident, "result": result}

    if method == "initialize":
        return ok({
            "protocolVersion": params.get("protocolVersion") or DEFAULT_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": NAME, "version": VERSION},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        tools = []
        for d in toolbox.definitions():
            entry = {"name": d["name"], "description": d["description"],
                     "inputSchema": d["input_schema"]}
            writes = toolbox.TOOLS[d["name"]].writes
            entry["annotations"] = {"readOnlyHint": not writes,
                                    "destructiveHint": False,
                                    "openWorldHint": True}
            tools.append(entry)
        return ok({"tools": tools})
    if method == "tools/call":
        text, is_error = toolbox.call(params.get("name", ""),
                                      params.get("arguments") or {})
        return ok({"content": [{"type": "text", "text": text}],
                   "isError": is_error})
    return {"jsonrpc": "2.0", "id": ident,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(stdin=None, stdout=None) -> None:
    from jobagent.agent import toolbox

    out = stdout or sys.stdout
    sys.stdout = sys.stderr            # every stray print now goes to stderr
    toolbox.configure()                # budget from JOBAGENT_* env
    for line in (stdin or sys.stdin):
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": "parse error"}}
        else:
            try:
                reply = handle(message)
            except Exception as exc:
                reply = {"jsonrpc": "2.0", "id": message.get("id"),
                         "error": {"code": -32603,
                                   "message": f"{type(exc).__name__}: {exc}"}}
        if reply is not None:
            out.write(json.dumps(reply, default=str) + "\n")
            out.flush()


def main() -> None:
    # Started by an MCP client, the working directory is wherever that
    # client was -- "/" for Claude Desktop. Every path in the project is
    # relative to the user's home directory (core/home.py), so go there
    # first. Run as a script from a checkout, the package's parent is not
    # on sys.path yet.
    parent = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    if parent not in sys.path:
        sys.path.insert(0, parent)
    from jobagent.core import home
    home.enter()
    serve()


if __name__ == "__main__":
    main()
