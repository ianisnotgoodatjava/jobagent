"""Someone other than the first owner can run this.

The first product question is what breaks for a second user, and the answer
was: their drafts attach the first owner's resume file, the model is told the
email is from the first owner, and the style gate flags every draft for not
signing off in the first owner's name. None of that shows up for the first
owner, so it is checked here rather than discovered by the second.
"""

import ast
import hashlib
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import architecture  # noqa: E402
from jobagent.core import sender_profile  # noqa: E402

# The first owner's name as it appeared in code before it moved to the
# profile. Nothing tracked may carry it -- not code, and not the comments and
# docstrings either, which is where it survived the first pass and where a
# public clone would still have shown it.
#
# Kept as SHA-256 hashes of each forbidden token, so this file does not spell
# the name out either and the check still works on a fresh clone. Every word
# token of a line is hashed, and so is each "a_b" pair inside a snake_case
# token, which is how the name appeared joined to its surname.
FIRST_OWNER_HASHES = frozenset({
    "5dfaaebff3e8ec0b796b47c7c674652150d92a16837946220d7efea32b8a854d",
    "6140545b861c9ba08c082f011cb683552c2db6185efb3bf4d928177a70119cd8",
    "b54a95127a4b573f41e335fdbd339dcc2208fbfb1ae0b6fab7599d6e2d6ec754",
    "706efceb01660c71fa747170bcf20d4e1285796b5d61e722ae2aabd4b54fb408",
})
_TOKEN = re.compile(r"\w+")


def _names_first_owner(text):
    for token in _TOKEN.findall(text):
        parts = token.split("_")
        pieces = {token, *parts} | {"_".join(parts[i:i + 2])
                                     for i in range(len(parts) - 1)}
        if any(hashlib.sha256(p.encode()).hexdigest() in FIRST_OWNER_HASHES
               for p in pieces):
            return True
    return False


def _code_strings(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docs):
            yield node.lineno, node.value


def test_no_code_is_written_for_one_person():
    hits = []
    for layer in architecture.LAYERS:
        for name in sorted(os.listdir(os.path.join(ROOT, architecture.folder(layer)))):
            if not name.endswith(".py"):
                continue
            path = os.path.join(ROOT, architecture.folder(layer), name)
            for line, text in _code_strings(path):
                if _names_first_owner(text):
                    hits.append(f"{layer}/{name}:{line}: {text.strip()[:70]!r}")
    assert not hits, "\n".join(hits)


def _tracked_python_files():
    """Every .py file in the repository, as git sees it."""
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT,
                         capture_output=True, text=True)
    if out.returncode != 0:          # not a git checkout (an export, say)
        return [os.path.join(dp, f)
                for dp, _, fs in os.walk(ROOT) for f in fs
                if f.endswith(".py") and ".venv" not in dp]
    return [os.path.join(ROOT, line) for line in out.stdout.split()]


def test_the_first_owner_is_not_named_anywhere():
    """Not in code, and not in a comment or docstring either.

    test_no_code_is_written_for_one_person above checks what the modules *do*.
    This checks what they *say*: the name had been left in some forty comments
    and docstrings on the grounds that a comment naming the real case is worth
    more than one saying "a firm", which is true right up to the point the
    repository is published.
    """
    hits = []
    for path in _tracked_python_files():
        # Kept out of its own scan, as before; it holds only hashes now.
        if os.path.abspath(path) == os.path.abspath(__file__):
            continue
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if _names_first_owner(line):
                rel = os.path.relpath(path, ROOT)
                hits.append(f"{rel}:{n}: {line.strip()[:70]!r}")
    assert not hits, ("the first owner is named in tracked files:\n"
                      + "\n".join(hits))


@pytest.fixture
def profile(monkeypatch):
    def use(**fields):
        merged = dict(sender_profile.DEFAULTS)
        merged.update(fields)
        monkeypatch.setattr(sender_profile, "_cache", merged)
    return use


def test_resume_comes_from_the_profile(profile):
    profile(name="Jane Doe", resume_path="~/Documents/cv.pdf")
    assert sender_profile.resume_path() == os.path.expanduser("~/Documents/cv.pdf")


def test_without_one_it_is_named_for_the_sender(profile):
    profile(name="Jane Q Doe", resume_path="")
    assert sender_profile.resume_path() == os.path.expanduser(
        "~/Jane_Q_Doe_Resume.pdf")
    assert sender_profile.first_name() == "Jane"
    assert sender_profile.file_stem() == "Jane_Q_Doe"


class TestWhatGetsAFollowUp:
    """Touch #2 is for cold outreach that went quiet. The first live agent
    run lined up nudges to two interviewers on their thank-you notes."""

    @pytest.mark.parametrize("subject,body", [
        ("Thank You - Jane Doe", "Hi Pat,\n\nThank you for taking the time..."),
        ("Following up", "Hi Pat, thank you for speaking with me earlier today"),
        ("Interview availability", "Hi, here is my availability next week."),
        ("Fwd: offer", "---------- Forwarded message ---------\n..."),
    ])
    def test_never_after_these(self, subject, body):
        from jobagent.outreach import followup_scheduler
        assert not followup_scheduler.worth_a_nudge(subject, body)

    def test_a_cold_note_does(self):
        from jobagent.outreach import followup_scheduler
        assert followup_scheduler.worth_a_nudge(
            "Recent Example Graduate Inquiring about Credit Research",
            "Dear Pat,\n\nI hope you are well. I'm a recent graduate...")
