"""Every test run gets a throwaway home for state, and proves it used it.

Before this existed, people_search.find() -- which two test files call with
invented web pages -- opened the real logs/people.db of whoever ran the
tests and recorded the invented people from those pages as real contacts,
with made-up addresses at real firms' mail domains. Nothing marked them as
fixtures, so the agent could later pick one as a verified contact and draft
to a person who does not exist, or to a real stranger who shares the name.

So, before any project module is imported (core/storage.py reads its roots
at import), the storage roots point into a temporary folder -- and the old
logs/ location too, or the one-time move from logs/ would carry a real
cache file into the temporary folder and delete it from yours. And the run
fails if any real state file changed while it ran, so the next test that
reaches past this is caught the day it is written rather than found in the
contacts database a week later.
"""

import glob
import os
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="jobagent-tests-")
os.environ["JOBAGENT_DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["JOBAGENT_CACHE_DIR"] = os.path.join(_TMP, "logs")


def _real_state() -> dict:
    """(mtime, size) of every real state file. Only the .db itself, not its
    -wal/-shm siblings, which a mere reader may touch."""
    paths = (glob.glob(os.path.join(ROOT, "data", "*.json"))
             + glob.glob(os.path.join(ROOT, "logs", "*.json"))
             + glob.glob(os.path.join(ROOT, "logs", "*.db")))
    out = {}
    for path in paths:
        try:
            stat = os.stat(path)
        except OSError:
            continue
        out[path] = (stat.st_mtime_ns, stat.st_size)
    return out


_BEFORE = _real_state()


def pytest_configure(config):
    from jobagent.core import storage
    storage.LEGACY_DIR = os.path.join(_TMP, "legacy-logs")
    for root in (storage.DATA_DIR, storage.CACHE_DIR,
                 storage.DATABASES["people"]):
        if not os.path.abspath(root).startswith(_TMP):
            raise RuntimeError(
                f"test storage is not isolated: {root} is outside {_TMP}. "
                f"Something imported core/storage.py before tests/conftest.py "
                f"could redirect it.")


def pytest_sessionfinish(session, exitstatus):
    after = _real_state()
    changed = sorted(p for p in set(_BEFORE) | set(after)
                     if _BEFORE.get(p) != after.get(p))
    shutil.rmtree(_TMP, ignore_errors=True)
    if changed:
        session.exitstatus = 1
        print("\n\nTESTS CHANGED REAL STATE -- a test reached past the "
              "temporary home in tests/conftest.py:")
        for path in changed:
            print(f"  {os.path.relpath(path, ROOT)}")
