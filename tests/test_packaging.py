"""One installable command, and one directory per user.

`pip install -e .` gives a `jobagent` command that works from any folder,
while `python cli.py` keeps working from a checkout for the scheduled run.
The risk in that is a second user, or a second copy, quietly reading and
writing the first one's files. Or the reverse: the existing setup moving
its state somewhere new and starting over. So where the files go is checked
here, along with the pieces a fresh install depends on.
"""

import os
import plistlib
import subprocess
import sys
import tomllib

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jobagent.console import commands  # noqa: E402
from jobagent.console.main import build_parser, from_here  # noqa: E402
from jobagent.core import home, launchd, storage  # noqa: E402


class TestHome:
    def test_a_checkout_keeps_its_files_where_they_are(self, monkeypatch):
        monkeypatch.delenv(home.ENV, raising=False)
        assert home.checkout() == ROOT
        assert home.home() == ROOT

    def test_installed_it_is_a_folder_in_the_users_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv(home.ENV, raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(home, "checkout", lambda: "")
        assert home.home() == str(tmp_path / ".jobagent")

    def test_set_by_hand_it_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv(home.ENV, str(tmp_path / "jane"))
        assert home.home() == str(tmp_path / "jane")
        monkeypatch.setenv(home.ENV, "~/elsewhere")
        assert home.home() == os.path.expanduser("~/elsewhere")

    def test_entering_creates_it_and_reads_its_env(self, monkeypatch, tmp_path):
        target = tmp_path / "jane"
        target.mkdir()
        (target / ".env").write_text("JOBAGENT_TEST_MARKER=from-home\n")
        monkeypatch.setenv(home.ENV, str(target / "sub" / ".."))
        # set, then removed: so the value .env loads is undone afterwards
        monkeypatch.setenv("JOBAGENT_TEST_MARKER", "")
        monkeypatch.delenv("JOBAGENT_TEST_MARKER")
        monkeypatch.chdir(tmp_path)
        assert home.enter() == str(target)
        assert os.getcwd() == os.path.realpath(target)
        assert os.environ["JOBAGENT_TEST_MARKER"] == "from-home"


class TestStateFollowsHome:
    def test_both_roots_are_inside_home(self):
        here = home.home()
        assert os.path.isabs(storage.DATA_DIR) and os.path.isabs(storage.CACHE_DIR)
        assert os.path.dirname(storage.DATA_DIR) == here
        assert os.path.dirname(storage.CACHE_DIR) == here
        assert os.path.dirname(storage.database("people")) == storage.CACHE_DIR

    def test_the_existing_layout_is_unchanged_in_a_checkout(self, monkeypatch):
        monkeypatch.delenv(home.ENV, raising=False)
        if os.environ.get("JOBAGENT_DATA_DIR") or os.environ.get("JOBAGENT_CACHE_DIR"):
            pytest.skip("a state root is overridden in this environment")
        assert storage.DATA_DIR == os.path.join(ROOT, "data")
        assert storage.CACHE_DIR == os.path.join(ROOT, "logs")
        assert storage.LEGACY_DIR == os.path.join(ROOT, "logs")


class TestFilesNamedOnTheCommandLine:
    """Every command runs in the home directory, so a file the user typed is
    made absolute from where they typed it first."""

    def test_typed_files_are_found_from_where_the_command_was_typed(self):
        args = build_parser().parse_args([
            "draft-outreach-email", "--row", "2", "--to", "a@example.com",
            "--subject", "s", "--body-file", "note.txt",
            "--attach", "cv.pdf", "--attach", "/abs/letter.pdf"])
        from_here(args, "/where/i/was")
        assert args.body_file == "/where/i/was/note.txt"
        assert args.attach == ["/where/i/was/cv.pdf", "/abs/letter.pdf"]

    def test_a_default_stays_with_the_projects_files(self):
        args = build_parser().parse_args(["export-past-outreach"])
        from_here(args, "/where/i/was")
        assert args.out == "past_outreach.txt"

    def test_every_file_argument_is_listed(self):
        """A new --something-file not in USER_FILES would be read from the
        home directory instead of from where the user is."""
        from jobagent.console.main import _dest
        dests = {_dest(a) for c in commands.COMMANDS for a in c.args}
        looks_like_a_file = {d for d in dests
                             if d.endswith("_file") or d in ("attach", "out")}
        assert looks_like_a_file == set(commands.USER_FILES)


class TestInstall:
    TEMPLATE = os.path.join(ROOT, "jobtracker.plist.example")

    def test_the_template_is_filled_with_real_paths(self):
        with open(self.TEMPLATE, "rb") as f:
            out = launchd.render(f.read(), "/repo", "/home/me/.jobagent",
                                 "com.jane.jobtracker",
                                 {"JOBAGENT_PYTHON": "/venv/bin/python"})
        job = plistlib.loads(out)
        assert job["Label"] == "com.jane.jobtracker"
        assert job["ProgramArguments"] == ["/bin/bash", "/repo/run_auto.sh"]
        assert job["StandardErrorPath"] == "/home/me/.jobagent/logs/launchd-stderr.log"
        assert job["EnvironmentVariables"] == {"JOBAGENT_PYTHON": "/venv/bin/python"}
        assert [t["Hour"] for t in job["StartCalendarInterval"]] == [6, 18, 23]
        assert launchd.PLACEHOLDER.encode() not in out

    def test_the_label_is_the_one_gitignore_expects(self):
        assert launchd.label("jane") == "com.jane.jobtracker"
        assert launchd.label("jane doe") == "com.jane-doe.jobtracker"
        with open(os.path.join(ROOT, ".gitignore")) as f:
            assert "com.*.jobtracker.plist" in f.read()

    def test_the_mcp_line_carries_a_home_set_by_hand(self, monkeypatch):
        from jobagent.agent import runner
        monkeypatch.delenv(home.ENV, raising=False)
        line, desktop = runner.registration()
        assert line.startswith(f"claude mcp add {runner.SERVER} --scope user -- ")
        assert "env" not in desktop["mcpServers"][runner.SERVER]

        monkeypatch.setenv(home.ENV, "/data/jane")
        line, desktop = runner.registration()
        assert f"-e {home.ENV}=/data/jane --" in line
        assert desktop["mcpServers"][runner.SERVER]["env"] == {home.ENV: "/data/jane"}

    def test_the_server_script_is_where_the_line_says(self):
        from jobagent.agent import runner
        assert os.path.isfile(runner.server_command()[-1])


class TestPackage:
    def test_requirements_and_pyproject_list_the_same_dependencies(self):
        """A checkout's venv installs from requirements.txt, and an install
        from pyproject.toml. One drifting from the other is a dependency
        only one of them has."""
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
            project = tomllib.load(f)["project"]
        with open(os.path.join(ROOT, "requirements.txt")) as f:
            lines = [ln.split("#")[0].strip() for ln in f]
        listed = {ln for ln in lines if ln}
        declared = set(project["dependencies"]) | set(
            project["optional-dependencies"]["test"])
        assert listed == declared

    def test_the_console_script_names_a_real_function(self):
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
            target = tomllib.load(f)["project"]["scripts"]["jobagent"]
        module, _, function = target.partition(":")
        import importlib
        assert callable(getattr(importlib.import_module(module), function))

    @pytest.mark.parametrize("argv", [
        [os.path.join(ROOT, "cli.py")],
        ["-m", "jobagent"],
    ])
    def test_help_answers_from_another_directory(self, argv, tmp_path):
        env = dict(os.environ, PYTHONPATH=ROOT)
        result = subprocess.run([sys.executable, *argv, "--help"],
                                cwd=tmp_path, env=env, capture_output=True,
                                text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        assert "llm-status" in result.stdout and "install" in result.stdout
        assert os.listdir(tmp_path) == [], "--help wrote into the folder it ran in"
