"""The model router and the agent: what they may do, and what they refuse.

None of this needs a model, Gmail or the sheet. The parts worth testing are
the decisions -- which backend answers, when a failure falls through, which
rows the agent may spend research on, and that the MCP channel stays clean
when the code underneath prints -- and every one of those can be exercised
with fakes.
"""

import datetime
import io
import json
import os
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jobagent.agent import mcp_server, runner, toolbox  # noqa: E402
from jobagent.core import home  # noqa: E402
from jobagent.sources import claude_api, claude_cli, llm  # noqa: E402


class FakeBackend:
    def __init__(self, ready=True, fail=None, text="ok"):
        self.ready, self.fail, self.text, self.calls = ready, fail, text, 0

    def available(self):
        return self.ready

    def respond(self, system, prompt, web_search=False, effort="high",
                max_attempts=5):
        self.calls += 1
        if self.fail:
            raise self.fail
        return self.text


@pytest.fixture
def backends(monkeypatch):
    fakes = {"muse": FakeBackend(ready=False),
             "claude_api": FakeBackend(ready=False),
             "claude_cli": FakeBackend(text="from cli")}
    monkeypatch.setattr(llm, "BACKENDS", fakes)
    monkeypatch.setattr(llm, "_tripped", {})
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    return fakes


class TestRouter:
    """The outage this exists for: one provider, one missing key, ninety
    scheduled passes that drafted nothing and said so only in a log."""

    def test_the_first_ready_backend_answers(self, backends):
        assert llm.respond("s", "p") == "from cli"
        assert llm.last_used == "claude_cli"

    def test_a_failure_falls_through_to_the_next(self, backends):
        backends["muse"] = FakeBackend(fail=RuntimeError("503"))
        assert llm.respond("s", "p") == "from cli"
        assert backends["muse"].calls == 1

    def test_all_failing_raises_with_every_reason(self, backends):
        backends["claude_cli"].fail = RuntimeError("usage window full")
        backends["claude_api"] = FakeBackend(fail=RuntimeError("401"))
        with pytest.raises(RuntimeError) as err:
            llm.respond("s", "p")
        assert "usage window full" in str(err.value) and "401" in str(err.value)

    def test_nothing_configured_says_how_to_fix_it(self, backends):
        backends["claude_cli"].ready = False
        assert not llm.available()
        with pytest.raises(SystemExit) as err:
            llm.respond("s", "p")
        assert "claude" in str(err.value) and "MODEL_API_KEY" in str(err.value)

    def test_provider_order_can_be_forced(self, backends, monkeypatch):
        backends["muse"].ready = True
        monkeypatch.setenv("LLM_PROVIDER", "claude_cli,muse")
        assert llm.ready() == ["claude_cli", "muse"]

    def test_an_unknown_provider_is_an_error_not_a_silent_skip(self, backends,
                                                              monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "gpt")
        with pytest.raises(SystemExit):
            llm.order()

    def test_out_of_quota_is_asked_once_per_run(self, backends):
        """2026-11-25 06:00: one full usage window, thirteen requests."""
        backends["muse"] = FakeBackend(fail=RuntimeError(
            "Error code: 429 - insufficient_quota"))
        assert llm.respond("s", "p") == "from cli"
        assert llm.respond("s", "p") == "from cli"
        assert backends["muse"].calls == 1
        assert llm.ready() == ["claude_cli"]

    def test_when_all_are_out_the_reason_says_so(self, backends):
        backends["claude_cli"].fail = claude_cli.QuotaExhausted("window full")
        with pytest.raises(RuntimeError):
            llm.respond("s", "p")
        assert not llm.available()
        assert "out of quota" in llm.why_unavailable()

    def test_muse_alone_cannot_drive_the_agent(self, backends):
        backends["claude_cli"].ready = False
        backends["muse"].ready = True
        assert llm.available() and llm.agent_backend() == ""


class TestCredentials:
    def test_a_subscription_token_is_not_an_api_key(self, monkeypatch):
        """Over the raw API an oat token is throttled to Haiku; it belongs
        to the CLI path, so the API path must not claim it."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-oat01-abc")
        assert not claude_api.available()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "your_key_here")
        assert not claude_api.available()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-abc")
        assert claude_api.available()

    def test_the_cli_child_is_not_a_nested_session(self, monkeypatch):
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "desktop")
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-abc")
        env = claude_cli.child_env()
        assert "CLAUDECODE" not in env and "CLAUDE_CODE_ENTRYPOINT" not in env
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"
        # With a metered key in its env the CLI would bill it instead of the
        # subscription, which is the one thing this path is for.
        assert "ANTHROPIC_API_KEY" not in env
        assert "/opt/homebrew/bin" in env["PATH"]


class TestAgentFence:
    """How the CLI agent run is fenced. Its first live run had no tracker
    tools at all: --tools, meant to whitelist web search, also filters out
    every MCP tool, and the agent wrote a brief saying it could see nothing."""

    def test_argv(self, monkeypatch, tmp_path):
        seen = {}

        def fake(argv, stdin, timeout, extra_env=None, cwd=""):
            seen.update(argv=argv, cwd=cwd, env=extra_env)
            return {"result": "brief", "num_turns": 3}

        monkeypatch.setattr(claude_cli, "_invoke", fake)
        monkeypatch.setattr(claude_cli, "binary", lambda: "claude")
        out = claude_cli.run_agent("task", "system", "/tmp/mcp.json",
                                   workspace=str(tmp_path))
        argv = seen["argv"]
        assert out["text"] == "brief"
        assert "--tools" not in argv
        assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
        assert argv[argv.index("--allowed-tools") + 1] == \
            "mcp__jobtracker,WebSearch,WebFetch"
        denied = argv[argv.index("--disallowed-tools") + 1].split(",")
        assert {"Bash", "Task", "Agent", "Write", "Edit"} <= set(denied)
        # Not the project: read-only tools are auto-allowed in the working
        # directory, and the project holds .env and token.json.
        assert seen["cwd"] == str(tmp_path)
        assert int(seen["env"]["MCP_TOOL_TIMEOUT"]) >= 10 * 60 * 1000


def _sheet_row(number=742, company="American Express", position="Analyst",
               status="Applied", applied="2026-11-10"):
    return {"row_number": number, "Company": company, "Position": position,
            "Status": status, "Applied Draes": applied}


class TestToolboxRules:
    def test_no_tool_can_send(self):
        names = set(toolbox.TOOLS)
        assert not [n for n in names if "send" in n]
        source = open(os.path.join(ROOT, "jobagent", "agent", "toolbox.py")).read()
        for forbidden in ("send_to_self", "messages().send", "send_message"):
            assert forbidden not in source

    def test_only_the_two_drafting_tools_write(self):
        writers = {n for n, t in toolbox.TOOLS.items() if t.writes}
        assert writers == {"research_and_draft", "draft_followup"}

    @pytest.mark.parametrize("row,reason", [
        (_sheet_row(applied="2025-12-30"), "before 2026"),
        (_sheet_row(status="Rejected"), "not an open application"),
        (_sheet_row(status="Interviewing"), "not an open application"),
        (_sheet_row(company="Financial Analyst", position="Financial Analyst"),
         "junk row"),
        (_sheet_row(applied=""), "no readable applied date"),
        (None, "no row"),
    ])
    def test_rows_it_refuses(self, row, reason):
        assert reason in toolbox._research_refusal(row, 742)

    def test_an_open_2026_row_is_allowed(self):
        assert toolbox._research_refusal(_sheet_row(), 742) == ""


@pytest.fixture
def one_row(monkeypatch):
    from jobagent.outreach import auto_draft
    from jobagent.people import findability
    row = _sheet_row()
    monkeypatch.setattr(toolbox, "_row", lambda n: row if n == 742 else None)
    monkeypatch.setattr(auto_draft, "_done", lambda: set())
    monkeypatch.setattr(findability, "coverage",
                        lambda days_back=None: ([], [(row, "COLD")]))
    ran = []
    monkeypatch.setattr(auto_draft, "run", lambda **kw: ran.append(kw))
    return ran


class TestResearchBudget:
    def test_dry_run_reports_and_spends_nothing_real(self, one_row):
        toolbox.configure(dry_run=True, max_drafts=1, max_followups=0)
        text, is_error = toolbox.call("research_and_draft", {"row": 742})
        assert not is_error and json.loads(text)["result"]["dry_run"]
        assert one_row == []

    def test_the_budget_is_a_hard_cap(self, one_row):
        toolbox.configure(dry_run=False, max_drafts=1, max_followups=0)
        first, _ = toolbox.call("research_and_draft", {"row": 742})
        second, is_error = toolbox.call("research_and_draft", {"row": 742})
        assert json.loads(first)["result"]["done"]
        assert is_error and "budget" in json.loads(second)["result"]["refused"]
        assert one_row == [dict(limit=1, min_fit=0.0, dry_run=False,
                                create_drafts=True, new_only=True,
                                write_link=True, only_rows=[742])]

    def test_a_firm_with_a_draft_is_left_alone(self, one_row, monkeypatch):
        from jobagent.people import findability
        row = _sheet_row()
        monkeypatch.setattr(findability, "coverage",
                            lambda days_back=None: ([(row, "COLD")], []))
        toolbox.configure(dry_run=False, max_drafts=3, max_followups=0)
        text, is_error = toolbox.call("research_and_draft", {"row": 742})
        assert is_error and "already has a draft" in text and one_row == []


class TestFollowups:
    @pytest.fixture(autouse=True)
    def due(self, monkeypatch):
        cands = [{"thread_id": "t1", "to": "a@firm.com", "subject": "Hi",
                  "sent": datetime.date(2026, 11, 10), "age": 9,
                  "has_draft": False, "message_id": "m1"},
                 {"thread_id": "t2", "to": "b@firm.com", "subject": "Hi",
                  "sent": datetime.date(2026, 11, 9), "age": 10,
                  "has_draft": True, "message_id": "m2"}]
        monkeypatch.setattr(toolbox, "_followup_cache", cands)
        monkeypatch.setattr(toolbox, "_followups_read_at", time.monotonic())
        return cands

    def test_not_due_is_refused(self):
        toolbox.configure(dry_run=False, max_drafts=0, max_followups=5)
        text, is_error = toolbox.call("draft_followup", {"thread_id": "nope"})
        assert is_error and "not due" in text

    def test_never_a_second_draft(self):
        toolbox.configure(dry_run=False, max_drafts=0, max_followups=5)
        text, is_error = toolbox.call("draft_followup", {"thread_id": "t2"})
        assert is_error and "already waiting" in text

    def test_dry_run(self):
        toolbox.configure(dry_run=True, max_drafts=0, max_followups=5)
        text, is_error = toolbox.call("draft_followup", {"thread_id": "t1"})
        assert not is_error and json.loads(text)["result"]["dry_run"]


class TestCall:
    def test_unknown_tool_and_bad_arguments_are_errors_not_crashes(self):
        assert toolbox.call("rm_rf", {})[1]
        assert toolbox.call("get_application", {})[1]              # missing
        assert toolbox.call("pipeline_summary", {"bogus": 1})[1]   # unknown

    def test_prints_are_captured_as_log(self, monkeypatch):
        def noisy():
            print("scanning 5996 messages")
            return {"n": 1}
        monkeypatch.setitem(toolbox.TOOLS, "noisy",
                            toolbox.Tool("noisy", "", {}, (), noisy, False))
        text, is_error = toolbox.call("noisy", {})
        assert not is_error
        assert json.loads(text) == {"result": {"n": 1},
                                    "log": "scanning 5996 messages"}


class TestMcpProtocol:
    def test_initialize_echoes_the_client_version(self):
        reply = mcp_server.handle({"jsonrpc": "2.0", "id": 1,
                                   "method": "initialize",
                                   "params": {"protocolVersion": "2025-11-25"}})
        assert reply["result"]["protocolVersion"] == "2025-11-25"
        assert reply["result"]["capabilities"] == {"tools": {"listChanged": False}}

    def test_tools_list_marks_the_writers(self):
        reply = mcp_server.handle({"jsonrpc": "2.0", "id": 2,
                                   "method": "tools/list"})
        tools = {t["name"]: t for t in reply["result"]["tools"]}
        assert set(tools) == set(toolbox.TOOLS)
        assert tools["research_and_draft"]["annotations"]["readOnlyHint"] is False
        assert tools["pipeline_summary"]["annotations"]["readOnlyHint"] is True
        assert tools["get_application"]["inputSchema"]["required"] == ["row"]

    def test_notifications_get_no_reply_and_unknown_methods_an_error(self):
        assert mcp_server.handle({"jsonrpc": "2.0",
                                  "method": "notifications/initialized"}) is None
        reply = mcp_server.handle({"jsonrpc": "2.0", "id": 3,
                                   "method": "resources/list"})
        assert reply["error"]["code"] == -32601

    def test_a_printing_tool_cannot_corrupt_the_channel(self, monkeypatch):
        """The modules underneath print progress. In a stdio server stdout is
        the protocol, so one stray line breaks the client's parser."""
        def noisy():
            print("this must not reach the protocol stream")
            return "fine"
        monkeypatch.setitem(toolbox.TOOLS, "noisy",
                            toolbox.Tool("noisy", "", {}, (), noisy, False))
        out = io.StringIO()
        saved = sys.stdout
        try:
            mcp_server.serve(stdin=io.StringIO(
                '{"jsonrpc":"2.0","id":1,"method":"tools/call",'
                '"params":{"name":"noisy","arguments":{}}}\n'
                'not json\n'), stdout=out)
        finally:
            sys.stdout = saved
        lines = out.getvalue().splitlines()
        replies = [json.loads(line) for line in lines]   # every line parses
        assert replies[0]["result"]["isError"] is False
        assert replies[1]["error"]["code"] == -32700

    def test_the_script_starts_from_any_directory(self, tmp_path):
        """Claude Desktop starts servers with cwd=/. The script has to find
        the project on its own."""
        command = runner.server_command()
        messages = "".join(json.dumps(m) + "\n" for m in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
        proc = subprocess.run(command, input=messages, capture_output=True,
                              text=True, cwd=str(tmp_path), timeout=60)
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        assert [r["id"] for r in replies] == [1, 2], proc.stderr[-800:]
        assert len(replies[1]["result"]["tools"]) == len(toolbox.TOOLS)


class TestRunner:
    def test_one_brief_a_day(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner, "BRIEF_DIR", str(tmp_path))
        day = datetime.date(2026, 11, 25)
        assert runner.briefed_today(day) == ""
        (tmp_path / "2026-11-24-0600.md").write_text("yesterday")
        assert runner.briefed_today(day) == ""
        (tmp_path / "2026-11-25-1800.md").write_text("today")
        assert runner.briefed_today(day).endswith("2026-11-25-1800.md")

    def test_the_run_config_carries_the_budget(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner, "AGENT_DIR", str(tmp_path))
        path = runner.mcp_config(dry_run=True, max_drafts=2, max_followups=1)
        server = json.load(open(path))["mcpServers"][runner.SERVER]
        # And the home directory: the CLI starts the server from an empty
        # workspace, so it must not have to work out where the files are.
        assert server["env"] == {"JOBAGENT_HOME": home.home(),
                                 "JOBAGENT_DRY_RUN": "1",
                                 "JOBAGENT_MAX_DRAFTS": "2",
                                 "JOBAGENT_MAX_FOLLOWUPS": "1"}

    def test_a_full_quota_skips_the_brief_instead_of_crashing(
            self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(runner, "BRIEF_DIR", str(tmp_path))
        monkeypatch.setattr(llm, "agent_backend", lambda: "claude_cli")

        def full(*a, **kw):
            raise claude_cli.QuotaExhausted("usage window is full")
        monkeypatch.setattr(claude_cli, "run_agent", full)
        monkeypatch.setattr(runner, "mcp_config", lambda *a: "/tmp/mcp.json")
        assert runner.run(notify=False) == ""
        assert "next scheduled pass" in capsys.readouterr().out
        assert os.listdir(tmp_path) == []          # so the next pass retries

    def test_the_banner_is_the_first_thing_to_do(self):
        brief = ("# Wed Nov 25\n**Do first**\n- Reply to Jane Roe (row 478)"
                 "\n- Prep for the second round\n**Pipeline** ...")
        assert runner._headline(brief) == "Reply to Jane Roe (row 478)"
