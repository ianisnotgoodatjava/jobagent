"""The launchd job that runs the pipeline on a schedule, filled in for one Mac.

jobtracker.plist.example is the template. It used to be filled in by hand,
with a sed line from the README. `jobagent install` fills it in here from
the template, which stays the one place the schedule (06:00, 18:00, 23:00)
is written down.

Pure: text in, text out. Reading the template, writing the result and
loading it are the command's job.
"""

import plistlib
import re

PLACEHOLDER = "/ABSOLUTE/PATH/TO/THIS/PROJECT"


def label(user: str) -> str:
    """com.<user>.jobtracker. The .gitignore expects this name for the
    filled-in copy, and so does any job already loaded under it."""
    return f"com.{re.sub(r'[^A-Za-z0-9-]', '-', user) or 'user'}.jobtracker"


def render(template: bytes, checkout: str, home: str, name: str,
           env: dict | None = None) -> bytes:
    """The template with real paths.

    The script it runs (run_auto.sh) is in the checkout. Its stderr log goes
    to the home directory, with the rest of the logs. `env` becomes the
    job's EnvironmentVariables: launchd starts the job with an empty
    environment, so a home directory or interpreter set by hand has to be
    written into the job.
    """
    job = plistlib.loads(template)
    job["Label"] = name
    job["ProgramArguments"] = [a.replace(PLACEHOLDER, checkout)
                               for a in job["ProgramArguments"]]
    if "StandardErrorPath" in job:
        job["StandardErrorPath"] = job["StandardErrorPath"].replace(
            PLACEHOLDER, home)
    if env:
        job["EnvironmentVariables"] = dict(env)
    out = plistlib.dumps(job, sort_keys=False)
    if PLACEHOLDER.encode() in out:
        raise ValueError("the template has a placeholder this does not fill")
    return out
