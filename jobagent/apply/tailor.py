"""Paste a posting, get your resume with its skills line tailored to it.

`prep` already builds a tailored PDF, but it takes a file or a URL and
writes a whole application folder. This is the short path for the moment
you are looking at a posting: copy it, and the resume is rebuilt with the
skills the posting asks for moved to the front, skills your own bullets
prove added when the posting names them, and everything it asks for that
you cannot show listed in the terminal rather than printed on the page.
core/skills.py is the rule; this is how it is reached.

  tailor              the posting on the clipboard, once
  tailor --watch      every posting you copy from now on, until Ctrl-C
  tailor --text-file  a posting saved to a file

Watching reads the clipboard every second or so while it runs, and only
then. A copy is treated as a posting when it is long enough and reads like
one -- requirement headings, or at least two skills named -- so copying an
address or a sentence does nothing.
"""

import os
import subprocess
import sys
import time

from jobagent.apply import prep_package, resume_pdf
from jobagent.core import job_posting
from jobagent.core import skills as skills_mod

OUT_ROOT = os.path.join(resume_pdf.OUT_ROOT, "tailored")
MIN_CHARS = 400


def clipboard() -> str:
    """The clipboard's text, or "" where there is no pbpaste."""
    try:
        out = subprocess.run(["pbpaste"], capture_output=True, text=True,
                             timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout if out.returncode == 0 else ""


def looks_like_posting(raw: str) -> bool:
    """Long enough, and either sectioned like a posting or naming two skills."""
    if len((raw or "").strip()) < MIN_CHARS:
        return False
    parsed = job_posting.parse_text(raw)
    sectioned = bool(parsed["requirements"] and parsed["responsibilities"])
    return sectioned or len(skills_mod.wanted(parsed)) >= 2


def tailor(raw: str, company: str = "", role: str = "",
           out_root: str = OUT_ROOT, open_pdf: bool = False) -> dict:
    """Builds the tailored PDF for one pasted posting.

    Returns {path, category, skills, report}; `report` is the lines printed.
    """
    parsed = job_posting.parse_text(raw, title=role)
    role = role or parsed.get("title") or "posting"
    category = prep_package.detect_category(role, parsed)
    bank = prep_package.load_bank()
    tailored = resume_pdf.tailored_skills(bank, parsed)
    path = resume_pdf.build(category, parsed=parsed,
                            company_name=company or "posting", role_name=role,
                            out_root=out_root, skills=tailored.skills,
                            quiet=True)
    # The builder trims tailored.skills in place if an added skill had to
    # give way to hold one page, so the report is of what was printed.
    tailored.added = [(label, ev) for label, ev in tailored.added
                      if label in tailored.skills]
    lines = [f"{role[:60]} — {prep_package.CATEGORIES[category]['label']}",
             *skills_mod.report(tailored), f"-> {path}"]
    if open_pdf and sys.platform == "darwin":
        subprocess.run(["open", path], capture_output=True)
    return {"path": path, "category": category, "skills": tailored.skills,
            "report": lines}


def _notify(title: str, text: str) -> None:
    if sys.platform != "darwin":
        return

    def q(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"')[:200] + '"'
    subprocess.run(["osascript", "-e",
                    f"display notification {q(text)} with title {q(title)}"],
                   capture_output=True)


def run(text_file: str = "", company: str = "", role: str = "",
        open_pdf: bool = False) -> str:
    if text_file:
        with open(text_file, encoding="utf-8") as f:
            raw = f.read()
    else:
        raw = clipboard()
        if not looks_like_posting(raw):
            raise SystemExit(
                "The clipboard does not look like a job posting. Open the "
                "posting, select all, copy, and run this again -- or pass "
                "--text-file.")
    result = tailor(raw, company=company, role=role, open_pdf=open_pdf)
    print("\n".join(result["report"]))
    return result["path"]


def watch(interval: float = 1.0, open_pdf: bool = False,
          max_runs: int | None = None) -> None:
    """Tailors every posting copied while this runs. Ctrl-C stops it."""
    print("Watching the clipboard -- copy a job posting. Ctrl-C to stop.",
          flush=True)
    seen = clipboard()             # what was there before is not a new copy
    runs = 0
    try:
        while max_runs is None or runs < max_runs:
            time.sleep(interval)
            raw = clipboard()
            if not raw or raw == seen:
                continue
            seen = raw
            if not looks_like_posting(raw):
                continue
            try:
                result = tailor(raw, open_pdf=open_pdf)
            except Exception as exc:
                print(f"  ! could not tailor that one: {type(exc).__name__}: "
                      f"{str(exc)[:120]}", flush=True)
                continue
            runs += 1
            print("\n" + "\n".join(result["report"]) + "\n", flush=True)
            _notify("Resume tailored",
                    f"{', '.join(result['skills'][:4])}… -> "
                    f"{os.path.basename(result['path'])}")
    except KeyboardInterrupt:
        print("\nstopped.")
