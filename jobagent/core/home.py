"""Where one user's files live: JOBAGENT_HOME.

Every file the project reads or writes for its user is relative to one
directory. That covers the state in core/storage.py, plus profile.json,
resume_bank.json, .env, the Google credentials.json and token.json, the
trained .pkl models, and the drafts and briefs the runs leave. Each entry
point starts by changing into that directory, the same way run_auto.sh
always began with `cd` into the repository. So a relative path anywhere in
the project means "in this user's home".

Which directory is used:

  JOBAGENT_HOME, if it is set.
  The repository root, when the code runs from a source checkout (an
      editable install counts). That is where the existing setup already
      keeps everything, so nothing moves.
  ~/.jobagent, when installed as a package.
"""

import os

ENV = "JOBAGENT_HOME"
INSTALLED_DEFAULT = os.path.join("~", ".jobagent")


def checkout() -> str:
    """The source checkout this code runs from, or "" when it was installed.

    A checkout is recognised by what sits beside the package: cli.py and
    pyproject.toml. site-packages has neither.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    marks = ("cli.py", "pyproject.toml")
    return root if all(os.path.isfile(os.path.join(root, m)) for m in marks) else ""


def home() -> str:
    """The absolute path of this user's directory. Has no side effects."""
    chosen = os.environ.get(ENV, "").strip()
    return os.path.abspath(os.path.expanduser(chosen or checkout()
                                              or INSTALLED_DEFAULT))


def file(name: str) -> str:
    """A file in the home directory by its name, e.g. file(".env")."""
    return os.path.join(home(), name)


def enter() -> str:
    """Creates the home directory if needed, changes into it, and loads its
    .env. Returns the directory.

    For entry points only (the command line and the MCP server). Call it
    before anything imports core/storage.py, because storage reads its
    JOBAGENT_* overrides at import and those may come from .env.
    """
    path = home()
    os.makedirs(path, exist_ok=True)
    os.chdir(path)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(path, ".env"))
    return path
